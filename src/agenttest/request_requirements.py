"""Source-partitioned obligations and deterministic plan witnesses, never model coverage scores."""

import re

from pydantic import Field, model_serializer

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.report import fingerprint
from agenttest.safety import StrictModel


class RequiredAction(StrictModel):
    operation_id: str
    role: str | None


class RequiredOrder(StrictModel):
    earlier: str
    later: str


class RequiredResource(StrictModel):
    producer: str
    consumer: str


class ExistingResource(StrictModel):
    operation_id: str
    role: str | None
    path: dict[str, str | int]


class RequestRequirement(StrictModel):
    source_id: str
    source_quote: str
    disposition: str
    actions: list[RequiredAction] = Field(max_length=20)
    before: list[RequiredOrder] = Field(max_length=20)
    same_resource: list[RequiredResource] = Field(max_length=20)
    existing_resources: list[ExistingResource] = Field(default_factory=list, max_length=20)
    response_checks: list[str] = Field(default_factory=list, max_length=30)
    forbidden_operation_ids: list[str] = Field(default_factory=list, max_length=30)
    blocked_operation_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_serializer(mode='wrap')
    def omit_empty_resources(self, handler):
        data = handler(self)
        if not self.existing_resources:
            data.pop('existing_resources', None)
        return data


def existing_resource_index(project):
    """Expose only complete, fixed path bindings from configured read policies."""
    reads = {op.operation_id: {p['name'] for p in op.parameters if p['in'] == 'path'}
             for op in project.contract.operations if op.method in {'GET', 'HEAD'}
             and op.operation_id in project.target.allowed_operations}
    resources = {}
    for policy in project.registry.definitions():
        names = reads.get(policy.operation_id)
        if not names or policy.negative:
            continue
        path = {}
        for rule in policy.binding_rules:
            choices = (rule.literal_schema or {}).get('enum', [])
            if (rule.location == 'path_parameters' and rule.source_policy_id is None
                    and len(choices) == 1 and type(choices[0]) in {str, int}):
                path[rule.name] = choices[0]
        if path.keys() == names:
            for role in policy.roles:
                item = dict(operation_id=policy.operation_id, role=role, path=path)
                resources['resource_' + fingerprint(item)[:16]] = item
    return resources


def resource_mentioned(resource, quote):
    from agenttest.requirement_ledger import explicit_assignments

    assignments = explicit_assignments(quote)
    unassigned = quote
    for item in reversed(assignments):
        unassigned = unassigned[:item['start']] + ' ' + unassigned[item['end']:]
    unassigned = re.sub(r'(?:HTTP|返回|状态码)\s*\d{3}(?!\d)', '', unassigned, flags=re.I)
    for name, value in resource.path.items():
        named = [a['value'] for a in assignments if a['name'] == name and not a['deferred']]
        if named:
            if not all(json_equal(value, item) for item in named):
                return False
        elif type(value) is int:
            if str(value) not in re.findall(r'-?\d+(?:\.\d+)?', unassigned):
                return False
        elif not re.search(r'(?<![A-Za-z0-9_-])' + re.escape(value) + r'(?![A-Za-z0-9_-])',
                           unassigned):
            return False
    return True


def response_expectations(project):
    return {f'expectation_{fingerprint([p.id, field.model_dump()])[:20]}': dict(
        policy_id=p.id, operation_id=p.operation_id,
        roles=p.roles if p.assertions.reviewed else [], **field.model_dump())
        for p in project.registry.definitions() if not p.negative for field in p.assertions.fields}


def bind_response_continuation(item, atom, previous, catalog, read_operations):
    """Attach an approved response check to the adjacent read under the same identity."""
    if (item.disposition != 'required' or item.actions or not item.response_checks
            or atom['future'] or atom['constraint'] or atom['same_resource'] or atom['ordered']
            or previous is None or previous.disposition != 'required'
            or not re.match(r'(?:并(?:且|顺便)?|以及|同时|顺便|再)\s*(?:请)?\s*'
                            r'(?:核对|确认|检查|验证)', item.source_quote)):
        return
    rules = [catalog.get(ref, {}) for ref in item.response_checks]
    choices = [action for action in previous.actions if action.operation_id in read_operations
               and all(r.get('operation_id') == action.operation_id
                       and action.role in r.get('roles', []) for r in rules)]
    if len(choices) == 1:
        item.actions = choices


def future_clause(text):
    future = re.search(r'以后|下次|将来|未来', text)
    if future is None:
        return False
    prefix = text[:future.start()]
    if re.search(r'创建|新建|提交|上传|更新|修改|读取|查询|检查|验证|测试|删除|导出|执行|'
                 r'注册|登录|分析|重试',
                 prefix):
        return False
    return bool(re.match(r'(?:请|先)?(?:留给|以后|下次|将来|未来)', text) or
                re.search(r'留给|供以后(?:再)?重试|供下次(?:再)?重试', text))


def request_atoms(text):
    """Partition original text without asking a model which clauses deserve accounting.

    Quote spans remain intact. Clause boundaries do not assert business meaning.
    Unknown interpretations must remain reviewable rather than disappear as suggestions.
    """
    spans, start, quote, brackets = [], 0, None, []
    closing = {'“': '”', '「': '」', '"': '"', "'": "'", '`': '`'}
    for position, char in enumerate(text):
        if quote:
            if char == quote and (position == 0 or text[position - 1] != '\\'):
                quote = None
        elif char in closing:
            quote = closing[char]
        elif char in '[{':
            brackets.append(']' if char == '[' else '}')
        elif brackets:
            if char == brackets[-1]:
                brackets.pop()
        elif position > start and not future_clause(text[start:].lstrip()) and re.match(
                r'留给(?:以后|下次|将来|未来)|(?:以后|下次)再|保留(?:到|给|供)(?:以后|下次|将来|未来)',
                text[position:]):
            if text[start:position].strip():
                spans.append((start, position))
            start = position
        elif char in '，,；;。\n':
            if text[start:position].strip():
                spans.append((start, position))
            start = position + 1
    if text[start:].strip():
        spans.append((start, len(text)))
    result = []
    for start, end in spans:
        source = text[start:end].strip()
        start += len(text[start:end]) - len(text[start:end].lstrip())
        # Preserve long text as bounded source spans rather than silently truncating it.
        for offset in range(0, len(source), 300):
            part = source[offset:offset + 300]
            result.append(dict(source_id=f'request_{len(result) + 1}', text=part,
                start=start + offset, end=start + offset + len(part),
                future=bool(re.search(r'(?:留给|保留.*?(?:给|供))?(?:以后|下次|将来|未来)', part)),
                future_only=future_clause(part),
                same_resource=bool(re.search(r'同一|同一个|同一条|同一份', part)),
                constraint=bool(re.match(r'(?:(?:本轮|本次|请|另外|同时|并且|且|但)\s*)?'
                    r'(?:不要(?!漏|遗漏|跳过|省略)|不得(?!漏|遗漏|跳过|省略)|无需|不必)', part)),
                ordered=bool(re.search(
                    r'之后|以后再|完成后|删除后|更新后|创建后|然后|最后', part))))
    return result


def question_source_texts(context):
    return {a['text'] for a in context.get('request_atoms', [])
            if not a['future'] and not a['constraint']} | {
        q['source_quote'] for q in context.get('previous_expectation_questions', [])}


def future_only_operations(context):
    """Keep explicit future-only mutation verbs out of the current selection grammar."""
    atoms = context.get('request_atoms', [])
    verbs = ('重试', '重新入队', '删除', '上传', '创建', '注册')
    future = {verb for verb in verbs if any(a.get('future_only') and verb in a['text']
        for a in atoms) and not any(not a.get('future_only') and verb in a['text'] for a in atoms)}
    operations = [op for group in context.get('grouped_index', []) for op in group['operations']]
    operations.extend(dict(id=op['operation_id'], method=op.get('method', 'GET'),
        summary=op.get('summary')) for op in context.get('unavailable_operations', []))
    return {op['id'] for op in operations
            if op['method'] not in {'GET', 'HEAD', 'OPTIONS'}
            and any(verb in (op.get('summary') or '') for verb in future)}


def constrain_question_sources(schema, sources, context):
    allowed_text = question_source_texts(context)
    references = [s['source_id'] for s in sources if s['text'] in allowed_text]

    def visit(node):
        if isinstance(node, dict):
            props = node.get('properties', {})
            if 'expectation_questions' in props and not references:
                props['expectation_questions'] = dict(type='array', maxItems=0)
            if all(key in props for key in ('requested_behavior', 'question', 'source_ref')):
                if references:
                    props['source_ref'] = dict(type='string', enum=references)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)


def requirement_schema(context):
    """A closed slot for every source clause; no free-form 'fully covered' boolean."""
    from agenttest.boundary_obligations import explicit_boundary_actions

    operations = context['approved_ids']
    unavailable = [o['operation_id'] for o in context.get('unavailable_operations', [])]
    roles = [None, *[entry['identity'] for entry in context.get('managed_authentication', [])]]
    action = dict(type='object', additionalProperties=False, properties=dict(
        operation_id=dict(type='string', enum=operations),
        role=dict(type=['string', 'null'], enum=roles)), required=['operation_id', 'role'])
    order = dict(type='object', additionalProperties=False, properties={
        key: dict(type='string', enum=operations) for key in ('earlier', 'later')},
        required=['earlier', 'later'])
    resource_choices = [dict(type='object', additionalProperties=False,
        properties=dict(producer=dict(type='string', enum=[producer]),
                        consumer=dict(type='string', enum=consumers)),
        required=['producer', 'consumer'])
        for producer, consumers in resource_edges(context).items()]
    if existing := context.get('existing_resources'):
        resource_choices.append(dict(type='object', additionalProperties=False,
            properties=dict(resource_ref=dict(type='string', enum=list(existing))),
            required=['resource_ref']))
    definitions = dict(RequestAction=action, RequestOrder=order)
    if resource_choices:
        definitions['RequestResource'] = dict(anyOf=resource_choices)
    action = {'$ref': '#/$defs/RequestAction'}
    order = {'$ref': '#/$defs/RequestOrder'}
    resource = {'$ref': '#/$defs/RequestResource'}
    shared = {}
    properties = {}
    trusted_actions = explicit_boundary_actions(context)
    for atom in context.get('request_atoms', []):
        branches = []
        trusted = trusted_actions.get(atom['source_id'])
        dispositions = (['deferred'] if atom.get('future_only') else ['required', 'needs_review',
            *(['constraint'] if atom['constraint'] else [])])
        if trusted is not None:
            dispositions = ['required']
        for disposition in dispositions:
            props = dict(disposition=dict(type='string', enum=[disposition]),
                actions=dict(type='array', items=action, minItems=1 if disposition == 'required'
                             else 0, maxItems=20 if disposition != 'deferred' else 0),
                before=dict(type='array', items=order, minItems=int(
                    disposition == 'required' and atom['ordered']), maxItems=20),
                same_resource=(dict(type='array', items=resource, minItems=int(
                    disposition == 'required' and atom['same_resource']), maxItems=20)
                    if resource_choices else dict(type='array', maxItems=0)),
                response_checks=(dict(type='array', items=dict(type='string', enum=list(
                    context['response_expectations'])), maxItems=30)
                    if context.get('response_expectations') else dict(type='array', maxItems=0)),
                forbidden_operation_ids=dict(type='array',
                    items=dict(type='string', enum=operations),
                    maxItems=30 if disposition == 'constraint' else 0),
                blocked_operation_ids=(dict(type='array',
                    items=dict(type='string', enum=unavailable), maxItems=30)
                    if unavailable and disposition == 'needs_review' else
                    dict(type='array', maxItems=0)))
            if trusted is not None:
                props['actions'] = dict(type='array', maxItems=0)
            if disposition == 'deferred':
                for key in ('before', 'same_resource', 'response_checks',
                            'forbidden_operation_ids', 'blocked_operation_ids'):
                    props[key]['maxItems'] = 0
            if disposition == 'constraint':
                for key in ('actions', 'response_checks'):
                    props[key]['maxItems'] = 0
            branch = dict(type='object', additionalProperties=False,
                          properties=props, required=list(props))
            signature = fingerprint(branch)
            name = shared.setdefault(signature, f'RequestClause{len(shared) + 1}')
            definitions[name] = branch
            branches.append({'$ref': f'#/$defs/{name}'})
        properties[atom['source_id']] = dict(anyOf=branches)
    return dict(type='object', additionalProperties=False, properties=properties,
                required=list(properties), **{'$defs': definitions})


def resource_edges(context):
    result = {}
    for edge in context.get('resource_dependencies', []):
        result.setdefault(edge['from_operation'], set()).add(edge['operation_id'])
    return {key: sorted(consumers) for key, consumers in result.items()}


def decode_requirements(wire, context):
    atoms = {a['source_id']: a for a in context.get('request_atoms', [])}
    if type(wire) is not dict or set(wire) != set(atoms):
        raise ValidationFailure(Layer.MODEL, 'FOCUS_SOURCE_CLAUSE_MISSING')
    resources = context.get('existing_resources', {})
    result = []
    for key, value in wire.items():
        if (type(value) is not dict or 'existing_resources' in value
                or type(value.get('same_resource')) is not list):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
        links, existing = [], []
        for relation in value['same_resource']:
            if type(relation) is dict and 'resource_ref' in relation:
                ref = relation['resource_ref']
                if (set(relation) != {'resource_ref'} or type(ref) is not str
                        or ref not in resources):
                    raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
                existing.append(resources[ref])
            else:
                links.append(relation)
        result.append(RequestRequirement(source_id=key, source_quote=atoms[key]['text'],
            **{**value, 'same_resource': links}, existing_resources=existing))
    return result


def validate_requirement_links(requirements, context, selection):
    """Validate source ownership and concrete obligation links, not semantic self-assessment."""
    atoms = {a['source_id']: a for a in context.get('request_atoms', [])}
    if {r.source_id for r in requirements} != set(atoms) or len(requirements) != len(atoms):
        raise ValidationFailure(Layer.MODEL, 'FOCUS_SOURCE_CLAUSE_MISSING')
    selected = set(selection.operation_ids if selection.mode == 'selected'
                   else context['approved_ids'])
    roles = {None, *[entry['identity'] for entry in context.get('managed_authentication', [])]}
    resources = {(d['from_operation'], d['operation_id'])
                 for d in context.get('resource_dependencies', [])}
    if selection.mode == 'no_current_action' and (not requirements or any(
            r.disposition not in {'deferred', 'constraint'} for r in requirements) or
            selection.input_requirements or selection.requested_checks or selection.input_questions
            or selection.expectation_questions or selection.blocked_operation_ids):
        raise ValidationFailure(Layer.MODEL, 'FOCUS_CURRENT_ACTION_MISSING')
    if selection.mode == 'all_ready' and not any(r.disposition == 'required' for r in requirements):
        raise ValidationFailure(Layer.MODEL, 'FOCUS_CURRENT_ACTION_MISSING')
    if (set(selection.operation_ids) | set(selection.blocked_operation_ids)) & (
            future_only_operations(context)):
        raise ValidationFailure(Layer.MODEL, 'FOCUS_FUTURE_OPERATION_SELECTED')
    catalog = context.get('response_expectations', {})
    read_operations = {o['id'] for group in context.get('grouped_index', [])
                       for o in group['operations'] if o['method'] in {'GET', 'HEAD'}} & selected
    by_source = {item.source_id: item for item in requirements}
    previous = None
    for atom in atoms.values():
        item = by_source[atom['source_id']]
        if (item.source_quote != atom['text'] or item.disposition not in {
                'required', 'deferred', 'needs_review', 'constraint'}):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_SOURCE_INVALID')
        if atom.get('future_only') and item.disposition != 'deferred':
            raise ValidationFailure(Layer.MODEL, 'FOCUS_FUTURE_SCOPE_INVALID')
        bind_response_continuation(item, atom, previous, catalog, read_operations)
        previous = item
        if item.disposition == 'required':
            missing_witnesses = [field for field, missing in (
                ('actions', not item.actions),
                ('same_resource', atom['same_resource'] and not (
                    item.same_resource or item.existing_resources)),
                ('before', atom['ordered'] and not item.before),
            ) if missing]
            if missing_witnesses:
                error = ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_WITNESS_MISSING')
                error.diagnostic.fields = [f'requirement_map.{atom["source_id"]}.{field}'
                                           for field in missing_witnesses]
                raise error
    mapped = {action.operation_id for item in requirements
              if item.disposition in {'required', 'needs_review'} for action in item.actions}
    mapped.update(r.operation_id for r in selection.input_requirements)
    checks = {c['policy_id']: c for c in context.get('approved_checks', [])}
    mapped.update(checks[r.policy_id]['operation_id'] for r in selection.requested_checks
                  if r.policy_id in checks)
    mapped.update(catalog[ref]['operation_id'] for item in requirements
                  for ref in item.response_checks
                  if ref in catalog)
    supported = set(mapped)
    while True:
        previous = set(supported)
        supported.update(d['from_operation'] for d in context.get('resource_dependencies', [])
                         if d['operation_id'] in supported)
        supported.update(d['after_operation'] for d in context.get('execution_prerequisites', [])
                         if d['operation_id'] in supported)
        dependency_roles = {role for rule in context.get('confirmed_response_checks', [])
                 if rule['operation_id'] in supported for role in rule['roles']}
        supported.update(a['login_operation'] for a in context.get('managed_authentication', [])
                         if a['identity'] in dependency_roles and a.get('login_operation'))
        if previous == supported:
            break
    missing = set(selection.operation_ids) - supported if selection.mode == 'selected' else set()
    if missing:
        error = ValidationFailure(Layer.MODEL, 'FOCUS_SELECTED_ACTION_WITHOUT_SOURCE')
        error.diagnostic.fields = sorted(missing)
        raise error
    selection.operation_sources = {operation: 'USER_REQUIREMENT' if operation in mapped
        else 'TRUSTED_PREREQUISITE' for operation in selection.operation_ids}
    configured_resources = list(context.get('existing_resources', {}).values())
    from agenttest.product_sources import numeric_boundary_reference

    for requested in selection.requested_checks:
        check = checks.get(requested.policy_id, {})
        _, value = numeric_boundary_reference(check, requested.source_quote)
        if value is not None and 'roles' in check and not any(
                item.disposition in {'required', 'needs_review'}
                and item.source_quote == requested.source_quote
                and any(a.operation_id == check['operation_id'] and a.role in check['roles']
                        for a in item.actions) for item in requirements):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_NEGATIVE_IDENTITY_INVALID')
    pending_boundaries = pending_numeric_boundaries(selection, checks)
    for item in requirements:
        atom = atoms[item.source_id]
        if item.disposition == 'deferred':
            if (not atom['future'] or item.actions or item.before or item.same_resource
                    or item.existing_resources
                    or item.response_checks or item.forbidden_operation_ids
                    or item.blocked_operation_ids):
                raise ValidationFailure(Layer.MODEL, 'FOCUS_FUTURE_SCOPE_INVALID')
            continue
        if item.disposition == 'constraint' and (not atom['constraint'] or item.actions):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_CONSTRAINT_INVALID')
        if item.forbidden_operation_ids and (item.disposition != 'constraint'
                or set(item.forbidden_operation_ids) - set(context['approved_ids'])):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_CONSTRAINT_INVALID')
        if set(item.forbidden_operation_ids) & set(selection.normal_operation_ids):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_CONTRADICTORY_REQUIRED_ACTION')
        if item.blocked_operation_ids and (item.disposition != 'needs_review' or
                set(item.blocked_operation_ids) - set(selection.blocked_operation_ids)):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE')
        if any(a.operation_id not in selected or a.role not in roles for a in item.actions):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_ACTION_INVALID')
        if any(r.earlier == r.later or r.earlier not in context['approved_ids']
               or r.later not in selected for r in item.before):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_ORDER_INVALID')
        if any((r.producer, r.consumer) not in resources for r in item.same_resource):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
        for resource in item.existing_resources:
            action = RequiredAction(operation_id=resource.operation_id, role=resource.role)
            anchored = any(prior.disposition == 'required' and action in prior.actions
                and atoms[prior.source_id]['start'] <= atom['start']
                and resource_mentioned(resource, prior.source_quote) for prior in requirements)
            if (item.disposition != 'required' or action not in item.actions or not anchored
                    or resource.model_dump() not in configured_resources):
                raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
        if any(ref not in context.get('response_expectations', {}) for ref in item.response_checks):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESPONSE_INVALID')
        human_gap = any(item.source_quote in q.source_quote or q.source_quote in item.source_quote
                        for q in [*selection.input_questions, *selection.expectation_questions])
        identity_gap = any(p['operation_id'] == action.operation_id and p['role'] == action.role
                          for p in context.get('pending_checks', []) for action in item.actions)
        configuration_gap = bool(item.blocked_operation_ids) and all(any(
            operation == o['operation_id'] for o in context.get('unavailable_operations', []))
            for operation in item.blocked_operation_ids)
        boundary_gap = any(item.source_quote in quote and item.actions and all(
            a.operation_id == operation and a.role in check_roles for a in item.actions)
            for quote, operation, check_roles in pending_boundaries)
        has_gap = (human_gap or identity_gap or configuration_gap or boundary_gap or
                   selection.mode == 'needs_information' and selection.question)
        if item.disposition == 'needs_review' and not has_gap:
            # An unresolved clause is not silently accepted as a completed requirement.
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE')


def pending_numeric_boundaries(selection, checks):
    from agenttest.llm_planner import literal_data
    from agenttest.product_sources import numeric_boundary_reference

    result = []
    for requested in selection.requested_checks:
        check = checks.get(requested.policy_id, {})
        _, value = numeric_boundary_reference(check, requested.source_quote)
        if (value is not None and requested.exact_value is not None
                and json_equal(value, literal_data(requested.exact_value.value))
                and not check['allow_value_variants']
                and not json_equal(value, check['invalid_value'])):
            result.append((requested.source_quote, check['operation_id'], check['roles']))
    return result


def validate_plan_requirements(design, requirements, primary, expectations=None):
    """Check actions, identities, same-resource bindings and order before business HTTP."""
    scenarios = [case.plan.scenario() for case in design.cases]
    steps = [step for scenario in scenarios for step in scenario.steps]
    expectations = expectations or {}
    for item in requirements:
        if any(s.operation_id in item.forbidden_operation_ids for s in steps):
            raise ValidationFailure(Layer.MODEL, 'DESIGN_FORBIDDEN_ACTION_PROPOSED')
        if item.disposition not in {'required', 'constraint'}:
            continue
        for action in item.actions:
            if action.operation_id in primary and not any(
                    s.operation_id == action.operation_id and s.role == action.role for s in steps):
                raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_IDENTITY_ACTION_MISSING')
        for resource in item.existing_resources:
            if resource.operation_id not in primary:
                continue
            witnesses = [s for s in steps if s.operation_id == resource.operation_id
                         and s.role == resource.role]
            if not witnesses or any(set(s.path_parameters) != resource.path.keys() or any(
                    binding.from_step is not None
                    or not json_equal(binding.value, resource.path[key])
                    for key, binding in s.path_parameters.items()) for s in witnesses):
                raise ValidationFailure(Layer.MODEL, 'DESIGN_EXISTING_RESOURCE_WITNESS_MISSING')
        for ref in item.response_checks:
            rule = expectations.get(ref)
            if rule is None:
                raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_RESPONSE_UNKNOWN')
            if rule['operation_id'] in primary and not any(
                    s.policy_id == rule['policy_id'] for s in steps):
                raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_RESPONSE_CHECK_MISSING')
        for order in item.before:
            if order.later not in primary:
                continue
            if not any(any(s.operation_id == order.earlier for s in scenario.steps[:position])
                for scenario in scenarios for position, step in enumerate(scenario.steps)
                if step.operation_id == order.later):
                raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_MISSING')
        producers = {r.producer for r in item.same_resource if r.consumer in primary}
        for producer in producers:
            consumers = {r.consumer for r in item.same_resource
                         if r.producer == producer and r.consumer in primary}
            matched = any(all(any(step.operation_id == consumer and any(
                binding.from_step == source.id for location in ('body', 'query', 'path_parameters')
                for binding in getattr(step, location).values()) for step in scenario.steps)
                for consumer in consumers) for scenario in scenarios for source in scenario.steps
                if source.operation_id == producer)
            if not matched:
                raise ValidationFailure(Layer.MODEL, 'DESIGN_SAME_RESOURCE_WITNESS_MISSING')
