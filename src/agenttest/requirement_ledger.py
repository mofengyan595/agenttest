"""Independent, bounded extraction of explicit assignments; never grants test authority."""

import json
import re

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.llm_planner import literal_data
from agenttest.selectors import validate_selector

_ASSIGNMENT = re.compile(
    r'(?<![A-Za-z0-9_.])(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[=＝]\s*'
    r'(?P<value>"(?:[^"\\]|\\.)*"|“[^”]*”|\'[^\']*\'|'
    r'-?\d+(?:\.\d+)?|true|false|null)(?![A-Za-z0-9_]|\.\d)')

_STATUS = re.compile(
    r'(?<![A-Za-z0-9_])(?:HTTP(?:\s*状态码)?|状态码)\s*(?:为|是|[:：=＝])?\s*'
    r'(?P<code>[1-5][0-9]{2})(?![A-Za-z0-9_]|\.\d)', re.I)
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|“[^”]*”|「[^」]*」|\'[^\']*\'|`[^`]*`')
_STATUS_ALTERNATIVE = re.compile(r'^\s*(?:或(?:者)?|/|、|至|到|[-–])\s*(?:HTTP\s*)?\d', re.I)
_STATUS_UNCERTAIN = re.compile(r'(?:不是|并非|不应(?:当)?|不会|不能|不得)\s*(?:返回\s*)?$')
_STATUS_PREFIX = re.compile(r'(?:并(?:且)?\s*)?(?:确认|检查|核对|验证|预期)?\s*(?:返回\s*)?')
_RESPONSE_PATH = re.compile(r'(?<![A-Za-z0-9_.])\$(?:\.[^\W\d]\w*|\[\d{1,6}\])*(?![\w.\[])')
_RESPONSE_EQUAL = re.compile(r'\s*(?:==?|＝|等于|应为)\s*(.+?)\s*$')
_RESPONSE_COUNT = re.compile(
    r'\s*(?:数量|条数|长度)\s*(?:为|是|[=＝])\s*([0-9]{1,8})\s*(?:条|项)?\s*$')
_RESPONSE_VERB = re.compile(r'响应|返回|确认|核对|检查|验证|断言')


def explicit_response_checks(text):
    """Capture bounded JSONPath predicates and common unscoped nonempty requests."""
    from agenttest.request_requirements import request_atoms

    entries = []
    for atom in request_atoms(text):
        if atom['future_only'] or atom['constraint']:
            continue
        source = atom['text']
        plain = _QUOTED.sub(lambda match: ' ' * len(match.group()), source)
        for match in _RESPONSE_PATH.finditer(plain):
            prefix, tail = source[:match.start()], source[match.end():]
            operator, expected = None, None
            if re.fullmatch(r'\s*(?:应)?存在\s*', tail):
                operator = 'exists'
            elif re.fullmatch(r'\s*(?:应)?(?:非空|不为空)\s*', tail):
                operator = 'nonempty'
            elif count := _RESPONSE_COUNT.fullmatch(tail):
                operator, expected = 'count', int(count[1])
            elif equal := _RESPONSE_EQUAL.fullmatch(tail):
                literals = explicit_assignments('v=' + equal[1])
                if literals and literals[0]['end'] == len(equal[1]) + 2:
                    operator, expected = 'equals', literals[0]['value']
            if operator is None and not _RESPONSE_VERB.search(prefix):
                continue
            try:
                selector = validate_selector(match.group())
            except ValueError:
                selector = None
            entries.append(dict(id=f'response_{len(entries) + 1}', source_id=atom['source_id'],
                source_quote=source, start=atom['start'] + match.start(), end=atom['end'],
                selector=selector, operator=operator, expected=expected, witnesses=[],
                status='NEEDS_REVIEW', reason='RESPONSE_CHECK_UNWITNESSED'))
        # Plain-language nonempty is actionable only when the selector is explicit.
        if not _RESPONSE_PATH.search(plain) and (match := re.search(
                r'(?:列表|数组|结果集)\s*(?:应当|应该|应)?\s*(?:非空|不为空)', plain)):
            entries.append(dict(id=f'response_{len(entries) + 1}', source_id=atom['source_id'],
                source_quote=source, start=atom['start'] + match.start(),
                end=atom['start'] + match.end(), selector=None, operator='nonempty',
                expected=None, witnesses=[], status='NEEDS_REVIEW',
                reason='RESPONSE_SELECTOR_UNRESOLVED'))
    return entries


def response_predicate_matches(entry, rule):
    if rule['select'] != entry['selector']:
        return False
    if entry['operator'] == 'nonempty':
        return rule['operator'] == 'count' and rule['expected'] > 0
    return rule['operator'] == entry['operator'] and (
        rule['operator'] == 'exists' or json_equal(rule['expected'], entry['expected']))


def unresolved_response_operations(ledger, requirements, project, requested):
    """Hold the linked component when an explicit response check lacks an approved rule."""
    from agenttest.request_requirements import response_expectations

    catalog = response_expectations(project)
    by_source = {item.source_id: item for item in requirements}
    blocked = set()
    for entry in ledger:
        item = by_source[entry['source_id']]
        if not item.actions or item.disposition not in {'required', 'needs_review'}:
            continue
        operations = {action.operation_id for action in item.actions}
        entry['actions'] = [action.model_dump() for action in item.actions]
        if item.disposition == 'required' and len(item.actions) == 1:
            action = item.actions[0]
            if (any(r['source_quote'] in item.source_quote for r in requested)
                    or any(rule['operation_id'] == action.operation_id
                           and action.role in rule['roles']
                           and response_predicate_matches(entry, rule)
                           for rule in catalog.values())):
                entry['status'] = 'NOT_EXECUTED'
                continue
        entry['reason'] = ('RESPONSE_SCOPE_AMBIGUOUS' if len(item.actions) > 1 else
                           'RESPONSE_CHECK_NOT_APPROVED' if entry['selector'] else entry['reason'])
        blocked.update(operations)
    return blocked


def validate_response_witnesses(design, primary, requirements, ledger, policies, requested):
    """Match explicit predicates to reviewed assertions on the same case step and identity."""
    by_source = {item.source_id: item for item in requirements if item.disposition == 'required'}
    for entry in ledger:
        item = by_source.get(entry['source_id'])
        if item is None or not any(action.operation_id in primary for action in item.actions):
            continue
        negative_ids = {r['policy_id'] for r in requested if r['source_quote'] in item.source_quote}
        found = False
        if len(item.actions) == 1:
            action = item.actions[0]
            for case in design.cases:
                for step in case.plan.steps:
                    policy = policies.get(step.policy_id)
                    if (policy is None or step.operation_id != action.operation_id
                            or policy.operation_id != action.operation_id
                            or step.role != action.role
                            or step.role not in policy.roles or not policy.assertions.reviewed
                            or not policy.assertions.source.strip()
                            or (policy.id not in negative_ids if negative_ids
                                else policy.negative)):
                        continue
                    for index, field in enumerate(policy.assertions.fields):
                        if response_predicate_matches(entry, field.model_dump()):
                            found = True
                            case._response_steps.setdefault(entry['id'], []).append(dict(
                                step_id=step.id, operation_id=step.operation_id, role=step.role,
                                policy_id=step.policy_id, check_kind=field.operator,
                                check_index=index + int(policy.assertions.status is not None)))
        if not found:
            error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_RESPONSE_WITNESS_MISSING')
            error.diagnostic.fields = [f'requirement_map.{item.source_id}']
            raise error


def explicit_http_statuses(text):
    """Extract literal status requirements and keep unsupported alternatives visible."""
    from agenttest.request_requirements import request_atoms

    ledger = []
    atoms = request_atoms(text)
    for index, atom in enumerate(atoms):
        if atom['future_only'] or atom['constraint']:
            continue
        source = atom['text']
        plain = _QUOTED.sub(lambda m: ' ' * len(m.group()), source)
        matches = list(_STATUS.finditer(plain))
        for match in matches:
            ambiguous = (len(matches) != 1 or _STATUS_ALTERNATIVE.match(plain[match.end():])
                         or _STATUS_UNCERTAIN.search(plain[:match.start()]))
            ledger.append(dict(source_id=atom['source_id'], source_quote=source,
                start=atom['start'] + match.start(), end=atom['start'] + match.end(),
                expected=int(match['code']), status='NEEDS_REVIEW', witnesses=[],
                reason='HTTP_STATUS_SCOPE_AMBIGUOUS' if ambiguous else 'HTTP_STATUS_UNWITNESSED'))
            if (index and not plain[match.end():].strip()
                    and _STATUS_PREFIX.fullmatch(plain[:match.start()])):
                ledger[-1]['preceding_source_id'] = atoms[index - 1]['source_id']
    return ledger


def validate_http_status_witnesses(design, primary, requirements, ledger, policies, requested):
    """Require a step with an exact reviewed assertion under the clause's action and role."""
    by_source = {item.source_id: item for item in requirements if item.disposition == 'required'}
    for entry in ledger:
        item = by_source.get(entry['source_id'])
        if item is None or not any(a.operation_id in primary for a in item.actions):
            continue
        previous = by_source.get(entry.get('preceding_source_id'))
        sources = [item.source_quote]
        if previous and previous.actions == item.actions:
            sources.append(previous.source_quote)
        negative_ids = {r['policy_id'] for r in requested
                        if any(r['source_quote'] in source for source in sources)}
        matched = []
        if (len(item.actions) == 1 and entry['reason'] != 'HTTP_STATUS_SCOPE_AMBIGUOUS'
                and (previous is None or previous.actions == item.actions)):
            action = item.actions[0]
            for case in design.cases:
                for step in case.plan.steps:
                    policy = policies.get(step.policy_id)
                    if (policy and step.operation_id == policy.operation_id == action.operation_id
                            and step.role == action.role and step.role in policy.roles
                            and (not negative_ids or step.policy_id in negative_ids)
                            and policy.assertions.reviewed and policy.assertions.source.strip()
                            and policy.assertions.status
                            and policy.assertions.status.codes == [entry['expected']]):
                        matched.append((case, step))
        if not matched:
            error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_HTTP_STATUS_MISSING')
            error.diagnostic.fields = [f'requirement_map.{item.source_id}']
            raise error
        for case, step in matched:
            case._http_status_steps.setdefault(item.source_id, []).append(dict(
                step_id=step.id, operation_id=step.operation_id, role=step.role,
                policy_id=step.policy_id))


def record_requirement_evidence(summary, case_row, report=None):
    """Only checks in the primary execution report can advance a planned obligation."""
    steps = {step.step_id: step for step in report.steps} if report else {}
    obligations = [(entry, case_row.get('http_status_steps', {}).get(entry['source_id'], []))
                   for entry in summary.get('http_status_requirements', [])]
    obligations += [(entry, case_row.get('response_steps', {}).get(entry['id'], []))
                    for entry in summary.get('response_requirements', [])]
    for entry, witnesses in obligations:
        for planned in witnesses:
            witness = next((w for w in entry['witnesses'] if w['case_id'] == case_row['case_id']
                            and w['step_id'] == planned['step_id']
                            and w.get('check_index') == planned.get('check_index')), None)
            if witness is None:
                witness = dict(planned, case_id=case_row['case_id'], status='PLANNED')
                entry['witnesses'].append(witness)
            if report:
                witness['run_id'] = report.run_id
            step = steps.get(witness['step_id'])
            if step and step.attempts:
                witness['status'] = 'EXECUTED'
                if 'check_index' in witness:
                    index = witness['check_index']
                    check = step.checks[index] if index < len(step.checks) else None
                    if check and (check.kind != witness['check_kind']
                                  or check.selector != entry['selector']):
                        check = None
                else:
                    check = next((c for c in step.checks if c.kind == 'http_status'
                                  and c.expected == [entry['expected']]), None)
                if check is not None:
                    witness.update(actual=check.actual,
                                   status='PASS' if check.passed else 'ASSERTION_FAILED')
        states = {w['status'] for w in entry['witnesses']}
        if states:
            entry.pop('reason', None)
            entry['status'] = next(state for state in (
                'ASSERTION_FAILED', 'EXECUTED', 'PASS', 'PLANNED') if state in states)


def explicit_assignments(text):
    """Recognize only field=scalar syntax, retaining offsets and deferred evidence.

    This is not a general semantic parser. Ambiguous fields and unknown business
    expectations remain reviewable, rather than becoming assertions or permission.
    """
    result = []
    for match in _ASSIGNMENT.finditer(text):
        raw = match['value']
        if raw.startswith(('“', "'")):
            value = raw[1:-1]
        elif raw[0] in '-0123456789':
            value = float(raw) if '.' in raw else int(raw)
        else:
            value = json.loads(raw)
        start = max(text.rfind(c, 0, match.start()) for c in '。；;，,\n') + 1
        ends = [pos for c in '。；;，,\n' if (pos := text.find(c, match.end())) >= 0]
        clause = text[start:min(ends) if ends else len(text)]
        prefix = text[start:match.start()]
        result.append(dict(name=match['name'], value=value, source_quote=match.group(),
            start=match.start(), end=match.end(),
            deferred=bool(re.search(r'(?:留给)?(?:以后|下次|将来|未来)(?:再|检查|测试)', prefix))
                and not any(cue in clause for cue in ('现在', '本轮', '本次'))))
    return result


def reconcile_assignments(context, selection):
    """Audit raw text against selected inputs/checks without relying on selected items alone."""
    checks = {c['policy_id']: c for c in context.get('approved_checks', [])}
    fields = context.get('input_fields', [])
    ledger = []
    for item in explicit_assignments(context['user_task']):
        candidates = {f['operation_id'] for f in fields if f['name'] == item['name']}
        candidates.update(c['operation_id'] for c in checks.values() if c['name'] == item['name'])
        responses = context.get('response_expectations', {})
        candidates.update(c['operation_id'] for c in responses.values()
                          if c.get('select', '').rsplit('.', 1)[-1] == item['name'])
        entry = dict(**item, candidate_operations=sorted(candidates), status='UNACCOUNTED')
        if item['deferred']:
            entry['status'] = 'DEFERRED'
        elif not candidates:
            entry['status'] = 'UNKNOWN_FIELD'
        else:
            matched = [r.operation_id for r in selection.input_requirements
                if r.name == item['name'] and not r.path
                and json_equal(literal_data(r.value), item['value'])
                and item['source_quote'] in r.source_quote]
            typed_gaps = []
            for requested in selection.requested_checks:
                check = checks.get(requested.policy_id, {})
                value = (literal_data(requested.exact_value.value)
                         if requested.exact_value is not None else check.get('invalid_value'))
                if (check.get('name') == item['name'] and json_equal(value, item['value'])
                        and item['source_quote'] in requested.source_quote):
                    matched.append(check['operation_id'])
                elif (check.get('name') == item['name'] and requested.exact_value is not None
                        and item['source_quote'] in requested.source_quote
                        and check.get('kind') == 'boundary_outside'
                        and check.get('field_constraints', {}).get('type') in {'integer', 'number'}
                        and type(item['value']) in {int, float} and type(value) is str
                        and value == str(item['value'])):
                    # The existing negative gate holds this type-changed check locally.
                    # Keep the raw obligation unresolved; never coerce it or stop unrelated work.
                    typed_gaps.append(requested.policy_id)
            for requirement in selection.requirements:
                if item['source_quote'] not in requirement.source_quote:
                    continue
                for resource in requirement.existing_resources:
                    if item['name'] in resource.path and json_equal(
                            item['value'], resource.path[item['name']]):
                        matched.append(resource.operation_id)
                for ref in requirement.response_checks:
                    rule = context.get('response_expectations', {}).get(ref, {})
                    if (rule.get('operator') == 'equals'
                            and rule.get('select', '').rsplit('.', 1)[-1] == item['name']
                            and json_equal(rule.get('expected'), item['value'])
                            and re.search(r'响应|返回|核对', requirement.source_quote)):
                        matched.append(rule['operation_id'])
            if matched:
                entry.update(status='ACCOUNTED', matched_operations=sorted(set(matched)))
            elif typed_gaps:
                entry.update(status='NEEDS_REVIEW', pending_checks=typed_gaps,
                             reason='NEGATIVE_VALUE_TYPE_CHANGED')
            elif any(q.operation_id in candidates and item['source_quote'] in q.source_quote
                     for q in [*selection.input_questions, *selection.expectation_questions]):
                entry['status'] = 'NEEDS_REVIEW'
        ledger.append(entry)
    return ledger


def validate_assignment_coverage(context, selection):
    ledger = reconcile_assignments(context, selection)
    if any(item['status'] in {'UNACCOUNTED', 'UNKNOWN_FIELD'} for item in ledger):
        error = ValidationFailure(Layer.MODEL, 'FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED')
        error.requirement_ledger = ledger
        raise error
    return ledger
