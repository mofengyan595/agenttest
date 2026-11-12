"""Source-partitioned obligations and deterministic plan witnesses, never model coverage scores."""

import re

from pydantic import Field, model_serializer, model_validator

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.report import fingerprint
from agenttest.safety import StrictModel


class RequiredAction(StrictModel):
    operation_id: str
    role: str | None


class RequiredOrder(StrictModel):
    """Which concrete clause instance must run before which other concrete instance.

    ``earlier``/``later`` stay operation ids, but an operation id is not an execution instance:
    the same GET may sit on both sides of a DELETE. ``*_source_id`` names the source clause and
    ``*_index`` the action occurrence inside that clause, so two calls of one operation remain
    distinguishable. Both sides are optional only for unique operations, which an unambiguous
    resolver may then bind directly.
    """

    earlier: str
    later: str
    earlier_source_id: str | None = None
    earlier_index: int | None = Field(default=None, ge=0, le=19)
    later_source_id: str | None = None
    later_index: int | None = Field(default=None, ge=0, le=19)

    @property
    def earlier_instance(self):
        return (self.earlier_source_id, self.earlier_index)

    @property
    def later_instance(self):
        return (self.later_source_id, self.later_index)

    @model_validator(mode="after")
    def both_sides_use_one_addressing_scheme(self):
        if (self.earlier_source_id is None) != (self.earlier_index is None):
            raise ValueError("ORDER_INSTANCE_INCOMPLETE")
        if (self.later_source_id is None) != (self.later_index is None):
            raise ValueError("ORDER_INSTANCE_INCOMPLETE")
        if (self.earlier_source_id is None) != (self.later_source_id is None):
            raise ValueError("ORDER_INSTANCE_INCOMPLETE")
        return self

    @model_serializer(mode="wrap")
    def omit_unbound_order(self, handler):
        data = handler(self)
        if self.earlier_source_id is None:
            for key in ("earlier_source_id", "earlier_index",
                        "later_source_id", "later_index"):
                data.pop(key, None)
        return data


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


def prior_fixed_path(resource, item, requirements, atoms, configured):
    """Resolve an explicit same-resource reference across configured read identities."""
    from agenttest.requirement_ledger import explicit_assignments

    if not re.search(r'同一(?:个|条|份)?(?:资源|仓库|记录|条目|项目|对象|预约|宠物|专科|兽医)'
                     r'(?=\s*(?:$|并|确认|验证|核对|返回|检查))', item.source_quote):
        return False
    if any(a['name'] in resource.path and not a['deferred']
           and not json_equal(a['value'], resource.path[a['name']])
           for a in explicit_assignments(item.source_quote)):
        return False
    paths = set()
    for candidate in configured:
        if candidate['operation_id'] != resource.operation_id or candidate['role'] == resource.role:
            continue
        witness = ExistingResource.model_validate(candidate)
        action = RequiredAction(operation_id=witness.operation_id, role=witness.role)
        if any(prior.disposition == 'required' and action in prior.actions
               and atoms[prior.source_id]['start'] < atoms[item.source_id]['start']
               and resource_mentioned(witness, prior.source_quote) for prior in requirements):
            paths.add(fingerprint(witness.path))
    return paths == {fingerprint(resource.path)}


def response_expectations(project):
    return {f'expectation_{fingerprint([p.id, field.model_dump()])[:20]}': dict(
        policy_id=p.id, operation_id=p.operation_id,
        roles=p.roles if p.assertions.reviewed and p.assertions.source.strip() else [],
        **field.model_dump())
        for p in project.registry.definitions() if not p.negative for field in p.assertions.fields}


def bind_response_continuation(item, atom, previous, catalog, read_operations):
    """Attach an approved response check to the adjacent read under the same identity.

    A conjunction continues the preceding read; a bare ``确认字段...`` after that read is the
    same assertion without a second request. Moving the checks to the producer keeps them
    attached to the real read instance while the confirmation itself stays a constraint.
    """
    if (item.disposition != 'required' or item.actions or not item.response_checks
            or atom['future'] or atom['constraint'] or atom['same_resource'] or atom['ordered']
            or previous is None or previous.disposition != 'required'):
        return
    rules = [catalog.get(ref, {}) for ref in item.response_checks]
    choices = [action for action in previous.actions if action.operation_id in read_operations
               and all(r.get('operation_id') == action.operation_id
                       and action.role in r.get('roles', []) for r in rules)]
    if len(choices) != 1:
        return
    if re.match(r'(?:并(?:且|顺便)?|以及|同时|顺便|再)\s*(?:请)?\s*'
                r'(?:核对|确认|检查|验证)', item.source_quote):
        item.actions = choices
        return
    if re.match(r'(?:请)?\s*(?:核对|确认|检查|验证)', item.source_quote):
        previous.response_checks = list(dict.fromkeys(
            [*previous.response_checks, *item.response_checks]))
        item.response_checks = []
        item.actions = []
        item.disposition = 'constraint'
        atom['constraint'] = True


def explicit_response_continuation(atom, entries):
    if atom['future'] or atom['constraint'] or atom['same_resource'] or atom['ordered']:
        return False
    prefix = atom['text'][:entries[0]['start'] - atom['start']]
    return re.fullmatch(r'(?:(?:并(?:且|顺便)?|以及|同时|顺便|再)\s*)?(?:请)?\s*'
                        r'(?:核对|确认|检查|验证)\s*(?:响应(?:中的?)?\s*)?', prefix)


def approved_response_predicate(entry, action, catalog):
    from agenttest.requirement_ledger import response_predicate_matches

    return any(rule['operation_id'] == action.operation_id and action.role in rule['roles']
               and response_predicate_matches(entry, rule) for rule in catalog.values())


def explicit_response_gap(entries, action, catalog):
    return any(entry['selector'] is not None and entry['operator'] is not None
               and not approved_response_predicate(entry, action, catalog) for entry in entries)


_REPEAT_READ = re.compile(r'(?:连续|重复)\s*(?:读取|查询|请求|调用)'
                          r'[^，,；;。\d]{0,20}?'
                          r'([2-9]|[1-9][0-9]+|[二两三四五六七八九十])\s*次')


def explicit_read_repetitions(item, read_operations):
    """Recognize an explicit count for one already mapped, read-only action."""
    item, actions = _requirement_dict(item), None
    actions = item.get('actions') or []
    if (item.get('disposition') != 'required' or len(actions) != 1
            or actions[0]['operation_id'] not in read_operations):
        return None
    quote = item.get('source_quote') or ''
    matches = list(_REPEAT_READ.finditer(quote))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.search(r'不(?:要|必|再|得|用|需)|无需|无须|禁止|避免|不得|如果|是否',
                 quote[:match.start()]):
        return None
    if match is None or re.match(r'\s*(?:或|以上|以下|到|至|[~～])', quote[match.end():]):
        return None
    number = match[1]
    if number.isascii():
        return int(number)
    return 2 if number == '两' else '零一二三四五六七八九十'.index(number)


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


def bounded_span(text, start, limit=300, *, cap=4000):
    """End of a bounded span that never splits a quoted value, a bracket or a JSONPath.

    A closed structure may extend the span past ``limit`` (up to ``cap``) so the original text
    stays in one piece; when even the cap cannot close it, the span runs to the end of the text
    instead of cutting through it. Text is never truncated, rewritten or dropped.
    """
    end = min(start + limit, len(text))
    if end >= len(text):
        return end
    quote = None
    brackets = []
    index = start
    while index < end:
        char = text[index]
        if quote:
            if char == quote and text[index - 1] != '\\':
                quote = None
        elif char in '"\'`“':
            quote = '”' if char == '“' else char
        elif char in '[{':
            brackets.append(']' if char == '[' else '}')
        elif brackets and char == brackets[-1]:
            brackets.pop()
        index += 1
    if not quote and not brackets:
        return end
    # Finish the open structure wherever it closes; a long quoted value or JSONPath is one span.
    while index < min(len(text), start + cap):
        char = text[index]
        if quote:
            if char == quote and text[index - 1] != '\\':
                quote = None
        elif char in '"\'`“':
            quote = '”' if char == '“' else char
        elif char in '[{':
            brackets.append(']' if char == '[' else '}')
        elif brackets and char == brackets[-1]:
            brackets.pop()
        index += 1
        if not quote and not brackets:
            return index
    # Still open: keep the rest of the original text together rather than cutting through it.
    return len(text)


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
        # Preserve long text as bounded source spans rather than silently truncating it, and
        # never cut a quoted value, a JSONPath or a bracketed list in half.
        offset = 0
        while offset < len(source):
            end = bounded_span(source, offset)
            part = source[offset:end]
            result.append(dict(source_id=f'request_{len(result) + 1}', text=part,
                start=start + offset, end=start + end,
                future=bool(re.search(r'(?:留给|保留.*?(?:给|供))?(?:以后|下次|将来|未来)', part)),
                future_only=future_clause(part),
                same_resource=bool(re.search(r'同一|同一个|同一条|同一份', part)),
                constraint=bool(re.match(r'(?:(?:本轮|本次|请|另外|同时|并且|且|但)\s*)?'
                    r'(?:不要(?!漏|遗漏|跳过|省略)|不得(?!漏|遗漏|跳过|省略)|无需|不必)', part)),
                ordered=bool(re.search(
                    r'之后|以后再|完成后|删除后|更新后|创建后|然后|最后', part))))
            offset = max(end, offset + 1)
    return result


def question_source_texts(context):
    return {a['text'] for a in context.get('request_atoms', [])
            if not a['future'] and not a['constraint']} | {
        q['source_quote'] for q in context.get('previous_expectation_questions', [])}


def permission_source_texts(context, operation, role):
    """A permission question needs the named actor in its own current or validated source."""
    previous = {q['source_quote'] for q in context.get('previous_expectation_questions', [])
                if q['operation_id'] == operation and q.get('kind') == 'permission'
                and q.get('role') == role}
    pattern = r'(?<![A-Za-z0-9_-])' + re.escape(role) + r'(?![A-Za-z0-9_-])'
    return {text for text in question_source_texts(context)
            if re.search(pattern, text) or text in previous or re.search(
                r'另一(?:个)?(?:身份|用户|账号)|其他(?:身份|用户|账号)|他人|别人|跨身份|跨用户',
                text)}


def explicit_anonymous_read(text):
    return bool(re.search(r'匿名\s*(?:读取|查询|请求|调用|访问)', text))


def permission_inquiry(text):
    """Any wording that asks what an identity is allowed to receive, including refusals."""
    return bool(re.search(r'权限|403|404|拒绝(?:规则|状态|行为|结果|方式)|是否拒绝|'
                          r'是否.*(?:允许|访问|读取)', text))


def interface_anaphor(text):
    # Entity references still require a real resource witness, even near an interface noun.
    return bool(re.search(r'同(?:一|一个)(?![^，；。]{0,12}(?:资源|记录|实体))'
                          r'[^，；。]{0,12}接口', text))


def permission_question_witness(question, item, context):
    """A sourced permission inquiry is an obligation even when it requests no HTTP action."""
    return (question.kind == 'permission' and item.source_quote == question.source_quote
        and item.disposition in {'required', 'needs_review'}
        and question.role in context.get('permission_question_roles', {}).get(
            question.operation_id, ())
        and question.source_quote in permission_source_texts(
            context, question.operation_id, question.role)
        and (any(a.operation_id == question.operation_id and a.role == question.role
                 for a in item.actions)
             or permission_inquiry(item.source_quote) and (
                 not item.actions and not explicit_anonymous_read(item.source_quote)
                 or explicit_anonymous_read(item.source_quote) and bool(item.actions) and all(
                     a.operation_id == question.operation_id and a.role is None
                     for a in item.actions))))


def common_phrase(text, label, *, minimum=2):
    """Longest letter-only phrase shared by one clause and one operation label."""
    best = ''
    for start in range(len(label)):
        for end in range(len(label), start + len(best), -1):
            phrase = label[start:end]
            if re.fullmatch(r'[^\W\d_]+', phrase) and phrase in text:
                best = phrase
                break
    return best if len(best) >= minimum else ''


def named_role(text, role):
    return bool(re.search(r'(?<![A-Za-z0-9_-])' + re.escape(role) + r'(?![A-Za-z0-9_-])', text))


def permission_inquiry_operations(text, context):
    """Resolve the operations an explicit permission inquiry names, never a guess.

    The witness is the operator's own operation label. A label phrase shared by two
    operations identifies neither, and a clause with no resolvable label yields nothing.
    A shorter shared phrase inside a longer matched label is the weaker reading, so it
    never adds a second operation.
    """
    labels = {operation['id']: operation.get('summary') or ''
              for group in context.get('grouped_index', [])
              for operation in group['operations']
              if operation.get('method') in {'GET', 'HEAD', 'OPTIONS'}}
    phrases = {}
    for operation in context.get('permission_question_roles', {}):
        phrase = common_phrase(text, labels.get(operation, ''))
        if phrase:
            phrases.setdefault(phrase, set()).add(operation)
    specific = {phrase for phrase in phrases
                if not any(phrase != other and phrase in other for other in phrases)}
    return sorted({operation for phrase, operations in phrases.items()
                   if phrase in specific and len(operations) == 1 for operation in operations})


def same_interface_question_reference(item, atom, requirements, questions, context):
    """An explicit same-interface anaphor is not an entity-level resource relation."""
    if not interface_anaphor(atom['text']):
        return False
    positions = {a['source_id']: a['start'] for a in context.get('request_atoms', [])}
    reads = {op['id'] for group in context.get('grouped_index', [])
             for op in group['operations'] if op['method'] in {'GET', 'HEAD'}}
    return bool(item.actions) and all(any(
        question.operation_id == action.operation_id
        and action.operation_id in reads
        and positions.get(prior.source_id, atom['start'] + 1) <= atom['start']
        and permission_question_witness(question, prior, context)
        for question in questions for prior in requirements) for action in item.actions)


def fixed_resource_sources(context, atom):
    """Offer fixed paths witnessed locally, in an adjacent check, or by an explicit anaphor."""
    from agenttest.requirement_ledger import explicit_response_checks

    preceding = [prior for prior in context.get('request_atoms', [])
                 if prior['start'] < atom['start']
                 and not prior['future_only'] and not prior['constraint']]
    sources = [atom]
    if atom['same_resource']:
        sources.extend(preceding)
    else:
        checks = [entry for entry in explicit_response_checks(context['user_task'])
                  if entry['source_id'] == atom['source_id']]
        if checks and explicit_response_continuation(atom, checks) and preceding:
            sources.append(preceding[-1])
    return [key for key, data in context.get('existing_resources', {}).items()
            if any(resource_mentioned(ExistingResource.model_validate(data), source['text'])
                   for source in sources)]


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
                    allowed = set(props['source_ref'].get('enum', references))
                    props['source_ref'] = dict(type='string',
                                              enum=[ref for ref in references if ref in allowed])
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
        **{key: dict(type='string', enum=operations) for key in ('earlier', 'later')},
        # Instance addressing: which clause and which action occurrence inside it.
        'earlier_source_id': dict(type=['string', 'null'],
                                  enum=[*[a['source_id'] for a in context.get('request_atoms', [])],
                                         None],
                                  description='该 before 边前一个实例所在的子句 source_id；'
                                              '同一操作跨子句重复时必须填，不能靠数组位置。'),
        'later_source_id': dict(type=['string', 'null'],
                                enum=[*[a['source_id'] for a in context.get('request_atoms', [])],
                                       None],
                                description='该 before 边后一个实例所在的子句 source_id；'
                                            '同一操作跨子句重复时必须填。'),
        'earlier_index': dict(type=['integer', 'null'], minimum=0, maximum=19,
                              description='该子句内第几次同名调用，从 0 开始；通常为 0。'),
        'later_index': dict(type=['integer', 'null'], minimum=0, maximum=19,
                            description='该子句内第几次同名调用，从 0 开始；通常为 0。')},
        required=['earlier', 'later'])
    resource_choices = [dict(type='object', additionalProperties=False,
        properties=dict(producer=dict(type='string', enum=[producer]),
                        consumer=dict(type='string', enum=consumers)),
        required=['producer', 'consumer'])
        for producer, consumers in resource_edges(context).items()]
    definitions = dict(RequestAction=action, RequestOrder=order)
    action = {'$ref': '#/$defs/RequestAction'}
    order = {'$ref': '#/$defs/RequestOrder'}
    shared = {}
    properties = {}
    trusted_actions = explicit_boundary_actions(context)
    for atom in context.get('request_atoms', []):
        choices = list(resource_choices)
        if references := fixed_resource_sources(context, atom):
            choices.append(dict(type='object', additionalProperties=False,
                properties=dict(resource_ref=dict(type='string', enum=references)),
                required=['resource_ref']))
        branches = []
        trusted = trusted_actions.get(atom['source_id'])
        permission_only = (permission_inquiry(atom['text'])
            and not explicit_anonymous_read(atom['text']) and any(
                atom['text'] in permission_source_texts(context, operation, role)
                for operation, identities in context.get('permission_question_roles', {}).items()
                for role in identities))
        dispositions = (['deferred'] if atom.get('future_only') else ['required', 'needs_review',
            *(['constraint'] if atom['constraint'] else [])])
        if trusted is not None:
            dispositions = ['required']
        for disposition in dispositions:
            props = dict(disposition=dict(type='string', enum=[disposition]),
                actions=dict(type='array', items=action, minItems=int(
                    disposition == 'required' and not permission_only),
                    maxItems=20 if disposition != 'deferred' else 0),
                before=dict(type='array', items=order, minItems=int(
                    disposition == 'required' and atom['ordered']), maxItems=20),
                same_resource=(dict(type='array', items=dict(anyOf=choices), minItems=int(
                    disposition == 'required' and atom['same_resource']
                    and not interface_anaphor(atom['text'])), maxItems=20)
                    if choices else dict(type='array', maxItems=0)),
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
            error = ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
            error.diagnostic.fields = [f'requirement_map.{key}.same_resource']
            raise error
        links, existing = [], []
        for index, relation in enumerate(value['same_resource']):
            if type(relation) is dict and 'resource_ref' in relation:
                ref = relation['resource_ref']
                if (set(relation) != {'resource_ref'} or type(ref) is not str
                        or ref not in resources):
                    error = ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
                    error.diagnostic.fields = [f'requirement_map.{key}.same_resource.{index}']
                    raise error
                existing.append(resources[ref])
            else:
                links.append(relation)
        result.append(RequestRequirement(source_id=key, source_quote=atoms[key]['text'],
            **{**value, 'same_resource': links}, existing_resources=existing))
    return result


def _requirement_dict(item):
    return item.model_dump() if isinstance(item, RequestRequirement) else item


def _clause_actions(item):
    return [dict(operation_id=action['operation_id'], role=action['role'])
            for action in _requirement_dict(item).get('actions') or []]


def _clause_candidates(operation, requirements):
    """Every concrete (clause, action index) that could realize this operation."""
    found = []
    for item in requirements:
        item = _requirement_dict(item)
        if item.get('disposition') not in {'required', 'needs_review'}:
            continue
        for index, action in enumerate(item.get('actions') or []):
            if action['operation_id'] == operation:
                found.append((item['source_id'], index))
    return found


def order_instances(order, requirements, atoms):
    """Resolve one order pair to the clause instances of each side, never to an array slot.

    Explicit ``*_source_id``/``*_index`` names one instance. Otherwise a side denotes every
    occurrence its operation has in the request map: an order between two *different*
    operations is a set constraint ("each earlier call precedes each later call"), which needs
    no guess. The same operation on both sides is only meaningful when the text declares a
    repetition of one clause, or when explicit ids separate the two occurrences; otherwise the
    pair is ambiguous and a human must resolve it. Text adjacency may separate two declared
    occurrences, but never picks a clause for a different operation.
    """
    order = order if isinstance(order, dict) else order.model_dump()
    requirements = [_requirement_dict(item) for item in requirements]

    def invalid(field):
        error = ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_ORDER_INSTANCE_INVALID')
        error.diagnostic.fields = [field]
        error.diagnostic.detail = dict(
            order={key: order.get(key) for key in (
                'earlier', 'later', 'earlier_source_id', 'earlier_index',
                'later_source_id', 'later_index')},
            available={side: [dict(source_id=source, action_index=index)
                              for source, index in candidates(side)[:20]]
                       for side in ('earlier', 'later')},
            hint=("在 requirement_map 对应子句的 before 中提供 earlier/later；"
                  "定位实例时，每侧同时填写 *_source_id 与从 0 开始的 *_index，"
                  "指向对应子句 actions 中的同名操作。保持原始需求和批准范围。"))
        return error

    def explicit(side):
        source, index = order.get(f'{side}_source_id'), order.get(f'{side}_index')
        if source is None:
            return None
        item = next((r for r in requirements if r['source_id'] == source), None)
        actions = _clause_actions(item) if item is not None else []
        matches = [position for position, action in enumerate(actions)
                   if action['operation_id'] == order[side]]
        if (item is None or item.get('disposition') not in {'required', 'needs_review'}
                or index is None or not 0 <= index < len(actions)
                or actions[index]['operation_id'] != order[side]
                or len(matches) == 1):
            # A model commonly writes index 0 for both calls in one clause. When the named
            # operation occurs exactly once there, its position is the only identity evidence;
            # repeated occurrences still require an explicit index.
            if item is None or len(matches) != 1:
                raise invalid(f'requirement_map.{source}.{side}')
            index = matches[0]
        return [(source, index)]

    def candidates(side):
        return _clause_candidates(order[side], requirements)

    def nearest(operation, anchor_start, before):
        starts = {r['source_id']: atoms.get(r['source_id'], {}).get('start') for r in requirements}
        options = [(abs(None if starts[source] is None else starts[source] - anchor_start), source,
                    index) for source, index in _clause_candidates(operation, requirements)
                   if starts[source] is not None and (starts[source] < anchor_start if before
                                                      else starts[source] > anchor_start)]
        if not options:
            return None
        options.sort()
        if len(options) > 1 and options[0][0] == options[1][0]:
            return None
        return [(options[0][1], options[0][2])]

    earlier_explicit, later_explicit = explicit('earlier'), explicit('later')
    earlier = earlier_explicit if earlier_explicit is not None else candidates('earlier')
    later = later_explicit if later_explicit is not None else candidates('later')
    if order['earlier'] == order['later'] and earlier and later:
        if earlier_explicit is not None and later_explicit is not None:
            # Two explicitly indexed occurrences of one clause: exact, so nothing to derive.
            return earlier, later
        if earlier_explicit is not None or later_explicit is not None:
            # One side is pinned; the text must leave exactly one other occurrence.
            known = (earlier_explicit or later_explicit)[0]
            rest = [candidate for candidate in candidates('earlier') if candidate != known]
            if len(rest) != 1:
                raise invalid(f'requirement_map.{known[0]}.before')
            return (earlier_explicit, rest) if earlier_explicit is not None else (rest,
                                                                                later_explicit)
        if len(earlier) == 1 and later == earlier:
            # A repetition of one clause: both sides denote the same single instance.
            item = next((r for r in requirements if r['source_id'] == earlier[0][0]), None)
            operations = {action['operation_id'] for action in _clause_actions(item or {})}
            count = explicit_read_repetitions(item, operations) if item is not None else None
            if count is None or count < 2:
                raise invalid(f'requirement_map.{later[0][0]}.before')
            return earlier, later
        # Two distinct occurrences of one operation: which is which comes from the written
        # clause order (source text rank, never a proposal or array position).
        rank = {requirement['source_id']: position
                for position, requirement in enumerate(requirements)}
        starts = {candidate: atoms.get(candidate[0], {}).get('start') for candidate in earlier}
        if atoms and all(start is not None for start in starts.values()):
            ordered = sorted(earlier, key=lambda candidate: starts[candidate])
        else:
            ordered = sorted(earlier, key=lambda candidate: rank.get(candidate[0], 0))
        if len(ordered) > 1:
            return [ordered[0]], [ordered[-1]]
        raise invalid(f'requirement_map.{later[0][0]}.before')
    # A side with no clause occurrence (for example a trusted login) stays unresolved and is
    # bound at plan time only when its operation occurs exactly once.
    return earlier or [(None, None)], later or [(None, None)]


def order_ambiguity(requirements, atoms):
    """Name the clause whose unaddressed order makes a repeated operation contradict itself.

    A cross-operation pair without instance ids denotes every occurrence of its operation. Once
    one operation is spread over several clauses on both sides of a delete, those set edges can
    contradict each other; the resulting cycle is a symptom, not the cause. Returning the clause
    lets the caller stop with ``FOCUS_REQUIREMENT_ORDER_INSTANCE_MISSING`` instead of reporting a
    dependency cycle that the user cannot act on.
    """
    pairs = []
    for requirement in requirements:
        item = _requirement_dict(requirement)
        if item.get('disposition') != 'required':
            continue
        for order in item.get('before') or []:
            order = order if isinstance(order, dict) else order.model_dump()
            try:
                earlier, later = order_instances(order, requirements, atoms)
            except ValidationFailure:
                return None
            earlier = [instance for instance in earlier if instance[0] is not None]
            later = [instance for instance in later if instance[0] is not None]
            if not earlier or not later:
                continue
            pairs.append((item['source_id'], order, earlier, later))
    remaining = {}
    for requirement in requirements:
        item = _requirement_dict(requirement)
        if item.get('disposition') != 'required':
            continue
        for index, action in enumerate(item.get('actions') or []):
            remaining.setdefault((item['source_id'], index), set())
    for _, _, earlier, later in pairs:
        for source in earlier:
            for target in later:
                if source != target:
                    remaining.setdefault(target, set()).add(source)
    while remaining:
        ready = [node for node, sources in remaining.items() if not sources]
        if not ready:
            break
        for node in ready:
            del remaining[node]
        for sources in remaining.values():
            sources.difference_update(ready)
    if not remaining:
        return None
    for clause, order, earlier, later in pairs:
        addressed = (order.get('earlier_source_id') is not None
                     and order.get('later_source_id') is not None)
        if not addressed and (len(earlier) > 1 or len(later) > 1):
            return dict(clause=clause, order=order, earlier=earlier, later=later)
    return None


def textual_order_edge(atom, item, preceding_operations):
    """Derive the adjacency witness the user's own clause order already states.

    An ordered clause (然后/再/最后) that maps to actions needs a ``before`` edge, but the
    ordering evidence is the clause position itself. The edge names both concrete instances:
    the last action of the preceding clause and this clause's action, so two reads of the same
    operation on both sides of a DELETE never collapse into one instance.
    """
    if not atom.get('ordered') or not item.actions or item.before or not preceding_operations:
        return None
    previous = preceding_operations[-1]
    later_index = 0
    if (previous['source_id'], previous['index']) == (item.source_id, later_index):
        return None
    return RequiredOrder(earlier=previous['operation_id'],
                         later=item.actions[later_index].operation_id,
                         earlier_source_id=previous['source_id'],
                         earlier_index=previous['index'],
                         later_source_id=item.source_id, later_index=later_index)


ACTION_VERB = re.compile(r'创建|新建|提交|上传|更新|修改|替换|读取|回读|查询|检查|验证|测试|'
                         r'删除|写入|导出|执行|注册|登录|分析|重试|改为')
READ_OPERATIONS = re.compile(r'读取|回读|查询|检查')


def evidence_fragment(atom):
    """A split fragment that can only qualify a neighbouring action, never request one.

    Clause splitting is punctuation-based, so an ordering fragment such as
    ``在同一请求成功之后`` carries no verb and no status. Demanding an action witness for it
    asks the model to invent a request, so the fragment stays accounted without actions and
    its ordering information is expressed by the neighbouring clause position instead.
    A bare confirmation of a preceding write ("确认删除成功") is the same kind of
    non-executable evidence: the write's reviewed status is already its assertion, and the
    confirmation must not become an invented GET.
    """
    text = atom['text']
    confirmation = (re.search(r'确认|核对|校验', text)
                    and not re.search(r'(?<!\d)[1-5]\d{2}(?!\d)|字段|条数|等于|一致|'
                                      r'不存在|无法|失败|返回', text))
    return ((not ACTION_VERB.search(text) or confirmation)
            and not atom.get('future_only')
            and not atom.get('constraint')
            and not re.search(r'(?<!\d)[1-5]\d{2}(?!\d)', text)
            # A fragment that asserts on a response is not evidence-neutral: it still needs the
            # reviewed continuation rule and the matching read identity.
            and not re.search(r'核对|校验|断言|条数|字段|等于|一致|不变|相同', text))


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
    from agenttest.requirement_ledger import (
        data_reference_candidates,
        explicit_data_references,
        explicit_http_statuses,
        explicit_response_checks,
        scoped_read_status,
    )

    text = context.get('user_task', '')
    data_references = explicit_data_references(text)
    predicates = {}
    for entry in explicit_response_checks(text):
        predicates.setdefault(entry['source_id'], []).append(entry)
    statuses = {entry['source_id']: entry for entry in explicit_http_statuses(text)
                if entry['reason'] == 'HTTP_STATUS_UNWITNESSED'}
    read_operations = {o['id'] for group in context.get('grouped_index', [])
                       for o in group['operations'] if o['method'] in {'GET', 'HEAD'}} & selected
    by_source = {item.source_id: item for item in requirements}
    requested = [check.model_dump() for check in selection.requested_checks]
    previous = None
    last_action_operations = []
    for atom in atoms.values():
        item = by_source[atom['source_id']]
        if (item.source_quote != atom['text'] or item.disposition not in {
                'required', 'deferred', 'needs_review', 'constraint'}):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_SOURCE_INVALID')
        if atom.get('future_only') and item.disposition != 'deferred':
            raise ValidationFailure(Layer.MODEL, 'FOCUS_FUTURE_SCOPE_INVALID')
        entries = predicates.get(atom['source_id'])
        if (not item.actions and previous is not None and previous.disposition == 'required'
                and len(previous.actions) == 1 and previous.actions[0].operation_id
                in read_operations):
            action = previous.actions[0]
            field_scope = entries and explicit_response_continuation(atom, entries)
            fields_match = field_scope and all(approved_response_predicate(entry, action, catalog)
                                               for entry in entries)
            status = statuses.get(atom['source_id'])
            status_continuation = (status
                and status.get('preceding_source_id') == previous.source_id
                and scoped_read_status(status, by_source, read_operations, requested,
                                       inherited=action) is not None)
            if (item.disposition == 'required' and fields_match
                    or item.disposition == 'needs_review' and field_scope
                    and explicit_response_gap(entries, action, catalog)
                    or item.disposition in {'required', 'needs_review'} and status_continuation):
                item.actions = previous.actions
        bind_response_continuation(item, atom, previous, catalog, read_operations)
        derived = textual_order_edge(atom, item, last_action_operations)
        if derived is not None:
            item.before = [derived]
        evidence = evidence_fragment(atom)
        if item.actions and item.disposition in {'required', 'needs_review'}:
            last_action_operations = [
                dict(operation_id=action.operation_id, index=index, source_id=item.source_id)
                for index, action in enumerate(item.actions)]
        previous = item
        if item.disposition == 'required':
            pending_inquiry = not item.actions and any(
                permission_question_witness(q, item, context)
                for q in selection.expectation_questions)
            interface_reference = same_interface_question_reference(item, atom, requirements,
                selection.expectation_questions, context)
            missing_witnesses = [field for field, missing in (
                ('actions', not item.actions and not pending_inquiry and not evidence),
                ('same_resource', atom['same_resource'] and not evidence and not (
                    item.same_resource or item.existing_resources or interface_reference)),
                ('before', atom['ordered'] and not item.before and not evidence),
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
    # A selected operation can own a pending question without requesting an execution.
    pending_operations = {q.operation_id for q in selection.expectation_questions
        if any(permission_question_witness(q, item, context) for item in requirements)}
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
    missing = (set(selection.operation_ids) - supported - pending_operations
               if selection.mode == 'selected' else set())
    if missing:
        error = ValidationFailure(Layer.MODEL, 'FOCUS_SELECTED_ACTION_WITHOUT_SOURCE')
        error.diagnostic.fields = sorted(missing)
        raise error
    selection.operation_sources = {operation: 'USER_REQUIREMENT' if operation in mapped
        else 'PENDING_PERMISSION' if operation in pending_operations
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
        repeated = explicit_read_repetitions(item, read_operations)
        if any(r.earlier not in context['approved_ids'] or r.later not in selected
               or (r.earlier_source_id is not None
                   and (r.earlier, r.earlier_instance) == (r.later, r.later_instance))
               for r in item.before):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_ORDER_INVALID')
        for order in item.before:
            if (order.earlier != order.later or order.earlier_source_id is not None):
                continue
            # A self order without instance ids is only a repetition claim: the operation must
            # be a read, the text must host two occurrences, and no mutating replay is assumed.
            candidates = _clause_candidates(order.earlier, requirements)
            if (order.earlier not in read_operations
                    or (repeated is None and len(candidates) < 2)
                    or (repeated is not None and repeated < 2)):
                raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_ORDER_INVALID')
        # Every remaining pair must denote at least one concrete instance per side; an
        # ambiguity stops here rather than being resolved later from proposal or array order.
        for order in item.before:
            order_instances(order, requirements, atoms)
        item.before = [order for order in item.before
                       if order.earlier_source_id is None
                       or order.earlier_instance != order.later_instance]
        if repeated is not None:
            # The literal count is verified before execution; it never creates a self edge.
            item.before = [order for order in item.before
                           if order.earlier_source_id is None
                           or order.earlier_instance != order.later_instance]
        for index, relation in enumerate(item.same_resource):
            if (relation.producer, relation.consumer) not in resources:
                error = ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
                error.diagnostic.fields = [
                    f'requirement_map.{item.source_id}.same_resource.{index}']
                raise error
        for index, resource in enumerate(item.existing_resources):
            action = RequiredAction(operation_id=resource.operation_id, role=resource.role)
            anchored = any(prior.disposition in {'required', 'needs_review'}
                and action in prior.actions
                and atoms[prior.source_id]['start'] <= atom['start']
                and resource_mentioned(resource, prior.source_quote) for prior in requirements)
            if (item.disposition not in {'required', 'needs_review'} or action not in item.actions
                    or resource.model_dump() not in configured_resources
                    or not (anchored or prior_fixed_path(
                        resource, item, requirements, atoms, configured_resources))):
                error = ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESOURCE_INVALID')
                error.diagnostic.fields = [
                    f'requirement_map.{item.source_id}.existing_resources.{index}']
                raise error
        if any(ref not in catalog for ref in item.response_checks):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_RESPONSE_INVALID')
        if item.disposition == 'required' and any(not any(
                a.operation_id == catalog[ref]['operation_id']
                and a.role in catalog[ref]['roles'] for a in item.actions)
                for ref in item.response_checks):
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
        predicate_gap = (item.disposition == 'needs_review' and len(item.actions) == 1
            and item.actions[0].operation_id in read_operations and explicit_response_gap(
                predicates.get(item.source_id, ()), item.actions[0], catalog))
        status = statuses.get(item.source_id)
        status_action = (scoped_read_status(status, by_source, read_operations, requested)
                         if item.disposition == 'needs_review' and status else None)
        status_gap = status_action is not None and not any(
            rule['operation_id'] == status_action.operation_id
            and status_action.role in rule['roles'] and rule['statuses'] == [status['expected']]
            for rule in context.get('confirmed_response_checks', []))
        data_gap = any(entry['source_id'] == item.source_id and not data_reference_candidates(
            entry, requirements, context.get('resource_dependencies', []))
            for entry in data_references)
        has_gap = (human_gap or identity_gap or configuration_gap or boundary_gap or predicate_gap
                   or status_gap or data_gap
                   or selection.mode == 'needs_information' and selection.question)
        if item.disposition == 'needs_review' and not has_gap:
            # An unresolved clause is not silently accepted as a completed requirement.
            raise ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE')
    ambiguity = order_ambiguity(requirements, atoms)
    if ambiguity:
        error = ValidationFailure(Layer.MODEL, 'FOCUS_REQUIREMENT_ORDER_INSTANCE_MISSING')
        error.diagnostic.fields = [f"requirement_map.{ambiguity['clause']}.before"]
        error.construction = dict(order=ambiguity['order'],
                                  earlier=[list(instance) for instance in ambiguity['earlier']],
                                  later=[list(instance) for instance in ambiguity['later']])
        raise error


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


def _identical_request(step, other):
    return (step.operation_id == other.operation_id and step.role == other.role
            and step.policy_id == other.policy_id
            and step.model_dump(exclude={'id', 'requirement_source_id',
                                         'requirement_action_index'})
            == other.model_dump(exclude={'id', 'requirement_source_id',
                                         'requirement_action_index'}))


def _order_steps(drafts, instances, operation, *, identity_required=False):
    """Every design step that realizes one side of an order pair, or a precise review stop.

    A clause instance normally owns exactly one step. Several steps may share one instance
    only when they send the identical request: that is a replay group, whose internal order
    carries no user intent, so it is serialised separately and never used as evidence.

    When the two sides are different operations, the pair is a set constraint, so every
    realization of the operation satisfies it without any identity guess. When both sides are
    the same operation, only real bindings can tell the occurrences apart.
    """
    resolved = [instance for instance in instances if instance[0] is not None]
    pool = [step for step in drafts if step.operation_id == operation]
    if resolved:
        matches, missing = [], []
        for source, index in resolved:
            found = [step for step in drafts if step.requirement_source_id == source
                     and (step.requirement_action_index or 0) == index]
            if not found:
                missing.append(source)
                continue
            if len(found) > 1 and not all(_identical_request(found[0], other)
                                          for other in found[1:]):
                error = ValidationFailure(Layer.MODEL,
                                          'DESIGN_REQUIRED_ORDER_INSTANCE_CONFLICT')
                error.diagnostic.fields = [f'plan.steps.{drafts.index(step)}.requirement_source_id'
                                           for step in found]
                raise error
            if any(step.operation_id != operation for step in found):
                error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_INSTANCE_INVALID')
                error.diagnostic.fields = [f'plan.steps.{drafts.index(step)}.operation_id'
                                           for step in found]
                raise error
            matches.extend(found)
        if missing:
            replay = (len(resolved) == 1 and len(pool) > 1
                      and all(_identical_request(pool[0], other) for other in pool[1:]))
            if identity_required and not replay:
                error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_INSTANCE_MISSING')
                error.diagnostic.fields = [f'requirement_map.{source}' for source in missing]
                raise error
            # An over-approximation is safe for two different operations: every call of the
            # earlier operation precedes every call of the later one, so no slot is guessed.
            if pool:
                return pool
            error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_ACTION_MISSING')
            error.diagnostic.fields = [operation]
            raise error
        return matches
    if identity_required:
        error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_INSTANCE_MISSING')
        error.diagnostic.fields = [operation]
        raise error
    if len(pool) == 1 or pool and all(_identical_request(pool[0], other)
                                      for other in pool[1:]):
        return pool
    if not pool:
        error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_ACTION_MISSING')
        error.diagnostic.fields = [operation]
        raise error
    return pool


def validate_plan_requirements(design, requirements, primary, expectations=None, *,
                               read_operations=()):
    """Check actions, identities, same-resource bindings and order before business HTTP."""
    scenarios = [case.plan.scenario() for case in design.cases]
    steps = [step for scenario in scenarios for step in scenario.steps]
    drafts = [draft for case in design.cases for draft in case.plan.steps]
    case_of = {draft.id: position for position, case in enumerate(design.cases)
               for draft in case.plan.steps}
    expectations = expectations or {}
    by_source = {item.source_id: item for item in requirements}
    bound = {}
    for position, draft in enumerate(drafts):
        source = draft.requirement_source_id
        if source is None:
            continue
        item = by_source.get(source)
        actions = [a.operation_id for a in item.actions] if item is not None else []
        index = draft.requirement_action_index
        if item is not None:
            # The Design schema deliberately hides the array index. When a vague clause names
            # several distinct operations, the operation itself is the unambiguous witness;
            # only repeated occurrences of one operation still require an explicit index. Some
            # model outputs also echo a default 0 for every step, which is not evidence.
            matches = [position for position, action in enumerate(actions)
                       if action == draft.operation_id]
            if (index is None or not 0 <= index < len(actions)
                    or actions[index] != draft.operation_id) and len(matches) == 1:
                index = matches[0]
                draft.requirement_action_index = index
        if (item is None or index is None or not 0 <= index < len(actions)
                or actions[index] != draft.operation_id):
            error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_INSTANCE_INVALID')
            error.diagnostic.fields = [f'plan.steps.{position}.requirement_source_id',
                                       f'plan.steps.{position}.requirement_action_index']
            raise error
        previous = bound.get((source, index))
        if previous is not None and not _identical_request(previous, draft):
            error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_INSTANCE_CONFLICT')
            error.diagnostic.fields = [f'plan.steps.{position}.requirement_source_id']
            raise error
        bound[(source, index)] = draft
    repeated = {}
    for item in requirements:
        count = explicit_read_repetitions(item, read_operations)
        if count is not None:
            action = item.actions[0]
            repeated.setdefault(action.operation_id, {})[action.role] = count
    for item in requirements:
        if any(s.operation_id in item.forbidden_operation_ids for s in steps):
            raise ValidationFailure(Layer.MODEL, 'DESIGN_FORBIDDEN_ACTION_PROPOSED')
        if item.disposition not in {'required', 'constraint'}:
            continue
        for action in item.actions:
            if action.operation_id in primary and not any(
                    s.operation_id == action.operation_id and s.role == action.role for s in steps):
                raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_IDENTITY_ACTION_MISSING')
        repetitions = explicit_read_repetitions(item, read_operations)
        if repetitions is not None and item.actions[0].operation_id in primary:
            action = item.actions[0]
            matching = [s for s in steps if s.operation_id == action.operation_id
                        and s.role == action.role]
            code = None
            if len(matching) != repetitions:
                code = 'DESIGN_REQUIRED_READ_COUNT_MISMATCH'
            elif len({fingerprint(s.model_dump(exclude={'id'})) for s in matching}) != 1 or any(
                    arg.from_step is not None for s in matching
                    for location in (s.body, s.query, s.path_parameters)
                    for arg in location.values()):
                code = 'DESIGN_REQUIRED_READ_INPUT_CHANGED'
            if code:
                error = ValidationFailure(Layer.MODEL, code)
                error.diagnostic.fields = [f'requirement_map.{item.source_id}']
                raise error
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
                    s.operation_id == rule['operation_id'] and s.policy_id == rule['policy_id']
                    and s.role in rule['roles'] and any(a.operation_id == s.operation_id
                        and a.role == s.role for a in item.actions) for s in steps):
                raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_RESPONSE_CHECK_MISSING')
        for order in item.before:
            if order.later not in primary:
                continue
            # The design must name the concrete instances it realizes; a design may schedule
            # them in any array order because construction derives the executable order.
            earlier_instances, later_instances = order_instances(order, requirements, {})
            same_operation = order.earlier == order.later
            earlier_steps = _order_steps(drafts, earlier_instances, order.earlier,
                                         identity_required=same_operation)
            later_steps = _order_steps(drafts, later_instances, order.later,
                                       identity_required=same_operation)
            earlier_ids = {step.id for step in earlier_steps}
            later_ids = {step.id for step in later_steps}
            if earlier_ids & later_ids:
                # One instance cannot be ordered against itself; a replay group legitimately
                # serves both sides and only needs its own serialisation.
                if earlier_instances != later_instances:
                    error = ValidationFailure(Layer.MODEL,
                                              'DESIGN_REQUIRED_ORDER_INSTANCE_CONFLICT')
                    error.diagnostic.fields = [f'requirement_map.{item.source_id}.before']
                    raise error
                if len(earlier_steps) < 2:
                    error = ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_ORDER_INVALID')
                    error.diagnostic.fields = [f'requirement_map.{item.source_id}.before']
                    raise error
                continue
            # Execution contexts run in the design's own case order. When the two instances
            # live in different cases, that sequence must satisfy the written order; inside a
            # case the executable order comes from construction, not from the array.
            if (max(case_of[step.id] for step in earlier_steps)
                    > min(case_of[step.id] for step in later_steps)):
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
