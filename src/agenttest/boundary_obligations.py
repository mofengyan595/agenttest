"""Bind source clauses to an already approved numeric boundary check before focus validation."""

import re
from collections import Counter

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.llm_planner import literal_data
from agenttest.negative import boundary_side, select_negative_approval, validate_approval
from agenttest.product_checks import ExactNegativeValue, RequestedCheck
from agenttest.product_sources import numeric_boundary_reference
from agenttest.request_requirements import RequiredAction

_STATUS = re.compile(r'(?:HTTP(?:\s*状态码)?|状态码|返回)\s*([1-5]\d{2})(?!\d)', re.I)
_STATUS_ONLY = re.compile(
    r'(?:(?:预期|应当?|确认|核对)\s*)?(?:返回\s*)?(?:HTTP(?:\s*状态码)?|状态码|返回)'
    r'\s*([45]\d{2})(?!\d)\s*(?:状态码)?', re.I)
_BOUNDARY_INTENT = re.compile(r'上界|下界|越界|边界|超出|拒绝')
_TEST_INTENT = re.compile(r'验证|检查|测试|核对|发起|请求|读取|查询')
_AMBIGUOUS = re.compile(r'或|任选|不要|无需|不必|不得|不确定|先别|(?:仅|只)(?:说明|解释)')


def _mentions(text, name):
    return bool(re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', text))


def boundary_links(project, context):
    """Find unique current read obligations using the real registry, not model choices."""
    atoms = context['request_atoms']
    operations = {o.operation_id: o for o in project.contract.operations if o.operation_id}
    policies = {p.id: p for p in project.registry.definitions()}
    identity_state = {entry['identity']: entry['credential_status']
                      for entry in context['managed_authentication']}
    unavailable = {o['operation_id'] for o in context['unavailable_operations']}
    links = []
    for index, atom in enumerate(atoms):
        quote = atom['text']
        if atom['future'] or atom['constraint'] or _AMBIGUOUS.search(quote):
            continue
        if not _TEST_INTENT.search(quote) or not (_BOUNDARY_INTENT.search(quote)
                                                 or _STATUS.search(quote)):
            continue
        following = atoms[index + 1] if index + 1 < len(atoms) else None
        status_only = _STATUS_ONLY.fullmatch(following['text']) if following else None
        statuses = {int(code) for code in _STATUS.findall(quote)}
        if status_only:
            statuses.add(int(status_only[1]))
        named_operations = {key for key in operations if _mentions(quote, key)}
        named_roles = {role for role in identity_state if _mentions(quote, role)}
        anonymous = bool(re.search(r'匿名|不登录', quote))
        if len(named_operations) > 1 or len(named_roles) + anonymous > 1:
            continue
        candidates = []
        for check in context['approved_checks']:
            key = check['operation_id']
            op = operations[key]
            if (key in unavailable or op.method not in {'GET', 'HEAD'}
                    or named_operations and key not in named_operations
                    or len(check['roles']) != 1):
                continue
            role = check['roles'][0]
            if (role is not None and identity_state.get(role) != 'READY'
                    or named_roles and role not in named_roles or anonymous and role is not None):
                continue
            _, value = numeric_boundary_reference(check, quote)
            if value is None or statuses and statuses != set(check['expected_statuses']):
                continue
            side = boundary_side(value, check['field_constraints'])
            if ('上界' in quote and 'upper' not in side or '下界' in quote and 'lower' not in side):
                continue
            policy = policies[check['policy_id']]
            try:
                approved = select_negative_approval(policy.negative, op, value, override=True)
                validate_approval(approved, op)
            except ValueError:
                continue
            candidates.append(dict(source_id=atom['source_id'], source_quote=quote,
                status_source_id=following['source_id'] if status_only else None,
                operation_id=key, role=role, policy_id=policy.id, value=value,
                explicit=bool(named_operations and (named_roles or anonymous))))
        if len(candidates) == 1:
            links.append(candidates[0])
    counts = Counter(link['policy_id'] for link in links)
    return [link for link in links if counts[link['policy_id']] == 1]


def explicit_boundary_actions(context):
    """Return closed, pre-associated actions indexed by the server's source clause IDs."""
    return {source: dict(operation_id=link['operation_id'], role=link['role'])
            for link in context.get('boundary_links', []) if link['explicit']
            for source in (link['source_id'], link['status_source_id']) if source}


def bind_boundary_actions(selection, context):
    """Supply missing links, preserving conflicting proposals and dependency gates as errors."""
    requirements = {r.source_id: r for r in selection.requirements}
    actions = explicit_boundary_actions(context)
    if actions and len(actions) == len(context['request_atoms']):
        operations = {action['operation_id'] for action in actions.values()}
        policies = {link['policy_id'] for link in context['boundary_links'] if link['explicit']}
        if (selection.normal_operation_ids or selection.source_conflicts
                or set(selection.operation_ids) != operations):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_BOUNDARY_SCOPE_CONFLICT')
        if any(check.policy_id not in policies for check in selection.requested_checks):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_BOUNDARY_CHECK_CONFLICT')
    for link in context.get('boundary_links', []):
        source = requirements.get(link['source_id'])
        if source is None or source.source_quote != link['source_quote']:
            continue
        action = RequiredAction(operation_id=link['operation_id'], role=link['role'])
        checks = [c for c in selection.requested_checks if c.policy_id == link['policy_id']]
        exact = (len(checks) == 1 and checks[0].source_quote == source.source_quote
                 and checks[0].exact_value is not None and checks[0].exact_length is None
                 and json_equal(literal_data(checks[0].exact_value.value), link['value']))
        if not link['explicit'] and not (exact and source.disposition == 'required'
                                         and source.actions == [action]):
            continue
        if selection.mode != 'selected' or action.operation_id not in selection.operation_ids:
            raise ValidationFailure(Layer.MODEL, 'FOCUS_BOUNDARY_SCOPE_CONFLICT')
        for source_id in (link['source_id'], link['status_source_id']):
            item = requirements.get(source_id)
            if item is None:
                continue
            if item.disposition != 'required' or item.actions and item.actions != [action]:
                error = ValidationFailure(Layer.MODEL, 'FOCUS_BOUNDARY_SCOPE_CONFLICT')
                error.diagnostic.fields = [f'requirement_map.{source_id}.actions']
                raise error
            item.actions = [action]
        if not checks:
            selection.requested_checks.append(RequestedCheck(policy_id=link['policy_id'],
                source_quote=source.source_quote,
                exact_value=ExactNegativeValue(value=link['value'])))
        elif not exact:
            raise ValidationFailure(Layer.MODEL, 'FOCUS_BOUNDARY_CHECK_CONFLICT')
