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
# A response field can be a value source instead of an assertion. The marker may be a
# transfer preposition, an anaphor, or both ("将它响应里的", "以该响应中的", "用这份响应中的").
_DATA_MARKER = (r'(?:用|使用|将|把|以|根据|依据|按照|从|拿|'
                r'它|其|该|此|这份|这个|这些|上述|前面)')
_DATA_PREFIX = re.compile(_DATA_MARKER + r'[^，,；。$]{0,28}?'
                          r'响应(?:中|里|内)?(?:的|里面|之中)?\s*$')
_CHECK_VERB = re.compile(r'验证|断言|确认|核对|检查')
_PREDICATE_TAIL = re.compile(r'\s*(?:[=＝]|等于|应|存在|非空|不为空|数量|条数|长度)')
# A complete literal predicate can be followed by another independent action clause.
_RESPONSE_TAIL = re.compile(r'^\s*(?:后|之后|以后|然后|随后|接着|最后|并且|并|再|同时|顺便|'
                            r'，|,|；|;|。|$)')
# "读取后验证该字段存在" asserts an unnamed value; the selector stays unresolved.
_RESPONSE_ANAPHOR = re.compile(
    r'(?:验证|断言|确认|核对|检查)[^，,；;。$]{0,12}?'
    r'(?:该字段|该值|该选择器|该属性|这个字段|这个值|它|其)'
    r'[^，,；;。]{0,10}?(?:存在|非空|不为空|等于|应|==?|＝)')


def _continued_predicate(tail):
    """Read a complete literal predicate that a following action clause continues past.

    An approved selector predicate stays a predicate even when one original clause also
    schedules another action ("核对回执 $.code 等于 "R73" 后匿名读取健康状态"). Returning the
    literal and the end offset keeps the continuation visible instead of downgrading the
    predicate to an unwitnessed assertion.
    """
    for pattern, operator in (
            (re.compile(r'\s*(?:应)?存在'), 'exists'),
            (re.compile(r'\s*(?:应)?(?:非空|不为空)'), 'nonempty'),
            (_RESPONSE_COUNT, 'count')):
        match = pattern.match(tail)
        if match is None:
            continue
        if not _RESPONSE_TAIL.match(tail[match.end():]):
            return None
        expected = int(match[1]) if operator == 'count' else None
        return operator, expected, match.end()
    equal = re.match(r'\s*(?:==?|＝|等于|应为)\s*', tail)
    if equal is None:
        return None
    literals = explicit_assignments('v=' + tail[equal.end():])
    if not literals:
        return None
    end = equal.end() + literals[0]['end'] - 2
    if not _RESPONSE_TAIL.match(tail[end:]):
        return None
    return 'equals', literals[0]['value'], end


def explicit_data_references(text):
    """Extract transfer candidates, independent of consumer verb vocabulary.

    The source expression and selector identify a possible transfer, not an assertion
    or approval. Only an exact, unambiguous producer/consumer policy edge can authorize
    it below; the final plan and executed response must also witness that binding.
    """
    from agenttest.request_requirements import request_atoms

    entries = []
    for atom in request_atoms(text):
        if atom['future_only'] or atom['constraint']:
            continue
        source = atom['text']
        plain = _QUOTED.sub(lambda m: ' ' * len(m.group()), source)
        for match in _RESPONSE_PATH.finditer(plain):
            prefix, tail = plain[:match.start()], plain[match.end():]
            if (_DATA_PREFIX.search(prefix) and not _CHECK_VERB.search(prefix)
                    and tail.strip() and not _PREDICATE_TAIL.match(tail)):
                entries.append(dict(id=f'data_{len(entries) + 1}', source_id=atom['source_id'],
                    source_quote=source, start=atom['start'] + match.start(),
                    end=atom['start'] + match.end(), selector=match.group(), witnesses=[],
                    status='NEEDS_REVIEW', reason='DATA_SOURCE_NOT_APPROVED'))
    return entries


def explicit_response_checks(text):
    """Capture bounded JSONPath predicates and common unscoped nonempty requests."""
    from agenttest.request_requirements import request_atoms

    entries = []
    data_offsets = {entry['start'] for entry in explicit_data_references(text)}
    for atom in request_atoms(text):
        if atom['future_only'] or atom['constraint']:
            continue
        source = atom['text']
        plain = _QUOTED.sub(lambda match: ' ' * len(match.group()), source)
        for match in _RESPONSE_PATH.finditer(plain):
            prefix, tail = source[:match.start()], source[match.end():]
            if atom['start'] + match.start() in data_offsets:
                # One occurrence is either a value source or an assertion, never both.
                # An assertion on the transferred value itself has no selector; it stays
                # pending instead of being attributed to the source selector.
                if _RESPONSE_VERB.search(prefix) and _RESPONSE_ANAPHOR.search(tail):
                    entries.append(dict(id=f'response_{len(entries) + 1}',
                        source_id=atom['source_id'], source_quote=source,
                        start=atom['start'] + match.start(), end=atom['end'], selector=None,
                        operator=None, expected=None, witnesses=[], continues=False,
                        status='NEEDS_REVIEW', reason='RESPONSE_ANAPHOR_UNRESOLVED'))
                continue
            operator, expected, continues = None, None, False
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
            if operator is None and (continued := _continued_predicate(tail)):
                operator, expected, end = continued
                continues = bool(tail[end:].strip())
            if operator is None and not _RESPONSE_VERB.search(prefix):
                continue
            try:
                selector = validate_selector(match.group())
            except ValueError:
                selector = None
            entries.append(dict(id=f'response_{len(entries) + 1}', source_id=atom['source_id'],
                source_quote=source, start=atom['start'] + match.start(), end=atom['end'],
                selector=selector, operator=operator, expected=expected, witnesses=[],
                continues=continues, status='NEEDS_REVIEW',
                reason='RESPONSE_CHECK_UNWITNESSED'))
        # Plain-language nonempty is actionable only when the selector is explicit.
        if not _RESPONSE_PATH.search(plain) and (match := re.search(
                r'(?:列表|数组|结果集)\s*(?:应当|应该|应)?\s*(?:非空|不为空)', plain)):
            entries.append(dict(id=f'response_{len(entries) + 1}', source_id=atom['source_id'],
                source_quote=source, start=atom['start'] + match.start(),
                end=atom['start'] + match.end(), selector=None, operator='nonempty',
                expected=None, witnesses=[], continues=False, status='NEEDS_REVIEW',
                reason='RESPONSE_SELECTOR_UNRESOLVED'))
    return entries


def data_reference_candidates(entry, requirements, dependencies):
    """Bind the explicit consumer to the immediately preceding sourced producer and roles."""
    from agenttest.policies import dependency_roles_match

    items = sorted(requirements, key=lambda item: int(item.source_id.removeprefix('request_')))
    index = next((i for i, item in enumerate(items) if item.source_id == entry['source_id']), -1)
    if index < 1:
        return []
    consumer, producer = items[index], items[index - 1]
    if (consumer.disposition not in {'required', 'needs_review'}
            or producer.disposition != 'required'
            or len(consumer.actions) != 1 or len(producer.actions) != 1):
        return []
    use, source = consumer.actions[0], producer.actions[0]
    candidates = [dict(edge, source_role=source.role, role=use.role) for edge in dependencies
        if edge['operation_id'] == use.operation_id and use.role in edge.get('roles', ())
        and edge['from_operation'] == source.operation_id
        and source.role in edge.get('source_roles', ()) and edge['selector'] == entry['selector']
        and dependency_roles_match(source.role, use.role, edge['role_relation'])]
    # Multiple possible input slots or policies are not permission to choose one silently.
    return candidates if len(candidates) == 1 else []


def resource_input_dependencies(project):
    policies = {p.id: p for p in project.registry.definitions()}
    methods = {o.operation_id: o.method for o in project.contract.operations}
    return [dict(operation_id=p.operation_id, roles=p.roles, policy_id=p.id,
        location=r.location, name=r.name, selector=r.source_selector,
        source_policy_id=r.source_policy_id,
        from_operation=policies[r.source_policy_id].operation_id,
        from_method=methods[policies[r.source_policy_id].operation_id],
        source_roles=policies[r.source_policy_id].roles, role_relation=r.role_relation)
        for p in policies.values() if not p.negative and p.assertions.reviewed
        and p.assertions.source.strip() for r in p.binding_rules
        if r.source_policy_id in policies and not policies[r.source_policy_id].negative
        and policies[r.source_policy_id].assertions.reviewed
        and policies[r.source_policy_id].assertions.source.strip()]


def unresolved_data_operations(ledger, selection, project):
    dependencies = resource_input_dependencies(project)
    by_source = {item.source_id: item for item in selection.requirements}
    blocked = set()
    for entry in ledger:
        item = by_source[entry['source_id']]
        candidates = data_reference_candidates(entry, selection.requirements, dependencies)
        entry['actions'] = [a.model_dump() for a in item.actions]
        if candidates and item.disposition == 'required':
            entry.update(status='NOT_EXECUTED', approved_bindings=candidates,
                         reason='DATA_SOURCE_NOT_EXECUTED')
        else:
            operations = {a.operation_id for a in item.actions} | set(item.blocked_operation_ids)
            if not operations:
                raise ValidationFailure(Layer.MODEL, 'FOCUS_DATA_SOURCE_SCOPE_MISSING')
            entry['blocked_operations'] = sorted(operations)
            blocked.update(operations)
    return blocked


def validate_data_witnesses(design, primary, ledger):
    """Require the exact approved input reference, never infer or repair a model binding."""
    for entry in ledger:
        bindings = [r for r in entry.get('approved_bindings', []) if r['operation_id'] in primary]
        if not bindings:
            continue
        found = False
        for case in design.cases:
            steps = {s.id: s for s in case.plan.steps}
            for step in case.plan.steps:
                for edge in bindings:
                    if (step.operation_id != edge['operation_id']
                            or step.policy_id != edge['policy_id']
                            or step.role != edge['role']):
                        continue
                    for argument in step.arguments:
                        source = steps.get(argument.from_step)
                        if (argument.location == edge['location'] and argument.name == edge['name']
                                and argument.select == entry['selector'] and source is not None
                                and source.policy_id == edge['source_policy_id']
                                and source.operation_id == edge['from_operation']
                                and source.role == edge['source_role']):
                            found = True
                            case._data_source_steps.setdefault(entry['id'], []).append(dict(
                                step_id=step.id, source_step_id=source.id,
                                operation_id=step.operation_id, policy_id=step.policy_id,
                                role=step.role, selector=argument.select,
                                location=argument.location, name=argument.name))
        if not found:
            raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_DATA_SOURCE_MISSING')


def response_predicate_matches(entry, rule):
    if rule['select'] != entry['selector']:
        return False
    if entry['operator'] == 'nonempty':
        return rule['operator'] == 'count' and rule['expected'] > 0
    return rule['operator'] == entry['operator'] and (
        rule['operator'] == 'exists' or json_equal(rule['expected'], entry['expected']))


def unresolved_response_operations(ledger, selection, project, requested):
    """Hold the linked component when an explicit response check lacks an approved rule."""
    from agenttest.request_requirements import response_expectations

    catalog = response_expectations(project)
    by_source = {item.source_id: item for item in selection.requirements}
    blocked = set()
    for entry in ledger:
        item = by_source[entry['source_id']]
        if item.disposition not in {'required', 'needs_review'}:
            continue
        operations = {action.operation_id for action in item.actions}
        entry['actions'] = [action.model_dump() for action in item.actions]
        if item.disposition == 'needs_review':
            operations.update(question.operation_id for question in selection.expectation_questions
                              if question.kind == 'business_rule'
                              and question.source_quote == item.source_quote)
        if not operations:
            continue
        approved = [action for action in item.actions if any(
            rule['operation_id'] == action.operation_id and action.role in rule['roles']
            and response_predicate_matches(entry, rule) for rule in catalog.values())]
        negative = any(r['source_quote'] in item.source_quote for r in requested)
        # The check itself names one approved action, so another action in the same
        # original clause ("... 后匿名读取健康状态") is an independent read, not ambiguity.
        attributed = len(approved) == 1 and (len(item.actions) == 1 or entry.get('continues'))
        if item.disposition == 'required' and (attributed or len(item.actions) == 1 and negative):
            entry['status'] = 'NOT_EXECUTED'
            if attributed:
                entry['approved_action'] = approved[0].model_dump()
            continue
        entry['reason'] = ('RESPONSE_SCOPE_AMBIGUOUS' if len(item.actions) > 1 else
                           'RESPONSE_CHECK_NOT_APPROVED' if entry['selector'] else entry['reason'])
        entry['blocked_operations'] = sorted(operations)
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
        actions = item.actions
        if entry.get('approved_action') is not None:
            actions = [action for action in actions
                       if action.model_dump() == entry['approved_action']]
        if len(actions) != 1:
            error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_RESPONSE_WITNESS_MISSING')
            error.diagnostic.fields = [f'requirement_map.{item.source_id}']
            raise error
        action = actions[0]
        found = False
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


def scoped_read_status(entry, by_source, reads, requested, *, inherited=None):
    """Resolve one literal status on a mapped read, excluding negative checks."""
    item = by_source[entry['source_id']]
    if (entry['reason'] != 'HTTP_STATUS_UNWITNESSED'
            or item.disposition not in {'required', 'needs_review'}):
        return None
    actions = item.actions or ([inherited] if inherited is not None else [])
    if len(actions) != 1 or actions[0].operation_id not in reads:
        return None
    sources = [item.source_quote]
    if preceding := entry.get('preceding_source_id'):
        previous = by_source[preceding]
        if previous.disposition != 'required' or previous.actions != actions:
            return None
        sources.append(previous.source_quote)
    if any(r['source_quote'] in source for r in requested for source in sources):
        return None
    return actions[0]


def unresolved_http_status_operations(ledger, selection, project):
    """Hold the component of an unambiguous read status lacking a reviewed rule."""
    reads = {op.operation_id for op in project.contract.operations
             if op.method in {'GET', 'HEAD'}
             and op.operation_id in project.target.allowed_operations}
    by_source = {item.source_id: item for item in selection.requirements}
    requested = [check.model_dump() for check in selection.requested_checks]
    policies = project.registry.definitions()
    blocked = set()
    for entry in ledger:
        action = scoped_read_status(entry, by_source, reads, requested)
        if action is None or any(p.operation_id == action.operation_id and action.role in p.roles
            and not p.negative and p.assertions.reviewed and p.assertions.source.strip()
            and p.assertions.status and p.assertions.status.codes == [entry['expected']]
            for p in policies):
            continue
        entry.update(status='NEEDS_REVIEW', reason='HTTP_STATUS_NOT_APPROVED',
                     actions=[action.model_dump()], blocked_operations=[action.operation_id])
        blocked.add(action.operation_id)
    return blocked


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
    for entry in summary.get('data_references', []):
        for planned in case_row.get('data_source_steps', {}).get(entry['id'], []):
            witness = next((w for w in entry['witnesses'] if w['case_id'] == case_row['case_id']
                            and w['step_id'] == planned['step_id']), None)
            if witness is None:
                witness = dict(planned, case_id=case_row['case_id'], status='PLANNED')
                entry['witnesses'].append(witness)
            source, consumer = steps.get(witness['source_step_id']), steps.get(witness['step_id'])
            if report and source and source.attempts and consumer and consumer.attempts:
                witness.update(status='EXECUTED', run_id=report.run_id)
        if entry['witnesses']:
            entry['status'] = ('EXECUTED' if all(w['status'] == 'EXECUTED'
                for w in entry['witnesses']) else 'PLANNED')
            entry.pop('reason', None)


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
        held = held_assignment_source(context, selection, item)
        if item['deferred']:
            entry['status'] = 'DEFERRED'
        elif held is not None:
            entry.update(held, status='NEEDS_REVIEW', reason='OPERATION_NOT_READY')
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


def held_assignment_source(context, selection, assignment):
    """Retain a uniquely sourced unavailable input; never add an executable slot."""
    from agenttest.request_requirements import request_atoms

    atoms = {a['source_id']: a for a in request_atoms(context['user_task'])}
    unavailable = {o['operation_id'] for o in context.get('unavailable_operations', [])}
    candidates = []
    for requirement in selection.requirements:
        atom = atoms.get(requirement.source_id)
        if (requirement.disposition != 'needs_review' or atom is None
                or requirement.source_quote != atom['text']
                or not atom['start'] <= assignment['start'] < assignment['end'] <= atom['end']):
            continue
        blocked = unavailable & set(selection.blocked_operation_ids) & set(
            requirement.blocked_operation_ids)
        candidates.extend(dict(source_id=requirement.source_id, **field)
            for field in context.get('unavailable_input_fields', [])
            if field['operation_id'] in blocked and field['name'] == assignment['name'])
    if len(candidates) != 1:
        return None
    field = candidates[0]
    return dict(source_id=field['source_id'], operation_id=field['operation_id'],
                location=field['location'], candidate_operations=[field['operation_id']])


def validate_assignment_coverage(context, selection):
    ledger = reconcile_assignments(context, selection)
    if any(item['status'] in {'UNACCOUNTED', 'UNKNOWN_FIELD'} for item in ledger):
        error = ValidationFailure(Layer.MODEL, 'FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED')
        error.requirement_ledger = ledger
        raise error
    return ledger
