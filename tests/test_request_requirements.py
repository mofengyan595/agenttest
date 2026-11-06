"""Original clauses, concrete plan witnesses and approved expectations stay separate."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from agenttest.diagnostics import ValidationFailure
from agenttest.product_design import TestDesign as Design
from agenttest.product_focus import FocusSelection
from agenttest.request_requirements import (
    RequestRequirement,
    decode_requirements,
    request_atoms,
    requirement_schema,
    validate_plan_requirements,
    validate_requirement_links,
)


def ctx(text):
    return dict(user_task=text, request_atoms=request_atoms(text),
        approved_ids=['create', 'read', 'delete'], managed_authentication=[dict(identity='owner'),
            dict(identity='other')], resource_dependencies=[dict(from_operation='create',
                operation_id=op) for op in ('read', 'delete')], response_expectations={})


def selection(requirements=()):
    return FocusSelection(interpretation='检查原文对应的全部行为', mode='selected',
        operation_ids=sorted({a.operation_id for r in requirements for a in r.actions}) or ['read'],
        question=None,
        pending_questions=[], not_covered=[], requirements=list(requirements))


def link(atom, **updates):
    return RequestRequirement(source_id=atom['source_id'], source_quote=atom['text'],
        **dict(dict(disposition='required', actions=[dict(operation_id='read', role='owner')],
            before=[], same_resource=[], response_checks=[]), **updates))


def step(key, op, role='owner', source=None, policy=None):
    return dict(id=key, operation_id=op, role=role, policy_id=policy or op,
        arguments=[] if source is None else [dict(location='path_parameters', name='id',
            value=None, from_step=source, select='$.id')])


def design(*cases):
    return Design(interpretation='逐项执行原文要求', cases=[dict(title='检查原文要求', kind='flow',
        plan=dict(name='case', steps=steps)) for steps in cases], questions=[], not_covered=[])


def test_original_partition_keeps_quoted_punctuation_offsets_and_every_clause():
    text = '创建名称“甲，乙”的资源，读取原文；删除后查询同一资源。'
    atoms = request_atoms(text)
    assert [a['text'] for a in atoms] == [
        '创建名称“甲，乙”的资源', '读取原文', '删除后查询同一资源']
    assert all(text[a['start']:a['end']] == a['text'] for a in atoms)
    assert atoms[-1]['ordered'] and atoms[-1]['same_resource']
    long = '测' * 801
    assert ''.join(a['text'] for a in request_atoms(long)) == long
    assert [a['text'] for a in request_atoms('参数使用[1,2]，读取后清理')] == [
        '参数使用[1,2]', '读取后清理']


@pytest.mark.parametrize(('method', 'role', 'continuation', 'linked'), [
    ('GET', 'owner', '并核对成功条数是一条', True),
    ('GET', 'other', '并核对成功条数是一条', False),
    ('POST', 'owner', '并核对成功条数是一条', False),
    ('GET', 'owner', '并核对同一资源的成功条数', False),
    ('GET', 'owner', '另查询下一批次的成功条数', False),
])
def test_response_continuation_requires_reviewed_rule_and_matching_read_identity(
        method, role, continuation, linked):
    context = ctx(f'读取批次23，{continuation}。')
    context['grouped_index'] = [dict(operations=[dict(id='read', method=method)])]
    context['response_expectations'] = dict(count=dict(operation_id='read', policy_id='read',
        roles=[role], select='$.successCount', operator='equals', expected=1))
    first, second = context['request_atoms']
    chosen = selection([link(first), link(second, actions=[], response_checks=['count'])])
    if linked:
        validate_requirement_links(chosen.requirements, context, chosen)
        assert chosen.requirements[1].actions == chosen.requirements[0].actions
        validate_plan_requirements(design([step('read', 'read')]), chosen.requirements,
                                   ['read'], context['response_expectations'])
    else:
        with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_WITNESS_MISSING'):
            validate_requirement_links(chosen.requirements, context, chosen)


@pytest.mark.parametrize('predicate,status,role,linked', [
    ('核对 $.count 等于 2', 200, 'owner', True),
    ('确认 HTTP 404', 404, 'owner', True),
    ('确认 HTTP 404', 200, 'owner', False),
    ('确认 HTTP 404', 404, 'other', False),
    ('确认 HTTP 404 或 403', 404, 'owner', False),
])
def test_exact_adjacent_read_check_uses_same_reviewed_action(predicate, status, role, linked):
    context = ctx(f'读取预约记录，{predicate}。')
    context['grouped_index'] = [dict(operations=[dict(id='read', method='GET')])]
    context['confirmed_response_checks'] = [dict(operation_id='read', roles=[role],
                                                statuses=[status])]
    context['response_expectations'] = dict(count=dict(operation_id='read', policy_id='read',
        roles=['owner'], select='$.count', operator='equals', expected=2))
    first, second = context['request_atoms']
    chosen = selection([link(first), link(second, actions=[])])
    if linked:
        validate_requirement_links(chosen.requirements, context, chosen)
        assert chosen.requirements[1].actions == chosen.requirements[0].actions
    else:
        with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_WITNESS_MISSING'):
            validate_requirement_links(chosen.requirements, context, chosen)


def test_future_unavailable_write_does_not_become_a_current_configuration_question():
    from agenttest.request_requirements import future_only_operations

    context = ctx('读取资源，这条记录留给以后重试。')
    context['unavailable_operations'] = [dict(operation_id='retry', method='POST',
        summary='重试分析任务', state='DISABLED')]
    assert future_only_operations(context) == {'retry'}
    items = [link(context['request_atoms'][0]), link(context['request_atoms'][1],
        disposition='deferred', actions=[])]
    chosen = selection(items)
    chosen.blocked_operation_ids = ['retry']
    with pytest.raises(ValidationFailure, match='FOCUS_FUTURE_OPERATION_SELECTED'):
        validate_requirement_links(items, context, chosen)


def test_identity_values_cannot_swap_even_if_the_global_value_set_is_preserved():
    from types import SimpleNamespace

    from agenttest.product_inputs import (
        InputRequirement,
        constrain_input_schema,
        validate_design_inputs,
    )

    project = SimpleNamespace(registry=SimpleNamespace(definitions=lambda: [
        SimpleNamespace(id=role, negative=None) for role in ('owner', 'other')]))
    inputs = [InputRequirement(operation_id='read', location='query', name='limit', path=[],
        value=value, source_quote=f'{role}使用limit={value}')
        for role, value in [('owner', 1), ('other', 2)]]
    links = [dict(source_quote=r.source_quote, actions=[dict(operation_id='read', role=role)])
             for role, r in zip(('owner', 'other'), inputs)]

    def run(owner, other):
        return design([dict(id=role, operation_id='read', role=role, policy_id=role,
            arguments=[dict(location='query', name='limit', value=value,
                            from_step=None, select=None)]) for role, value in [
                                ('owner', owner), ('other', other)]])

    with pytest.raises(ValueError, match='DESIGN_EXPLICIT_INPUT_NOT_PRESERVED'):
        validate_design_inputs(project, run(2, 1), inputs, source_requirements=links)
    validate_design_inputs(project, run(1, 2), inputs, source_requirements=links)
    schema = dict(type='integer', minimum=1, maximum=3)
    rule = dict(location='query', name='limit')
    owner = constrain_input_schema(schema, 'read', rule, inputs, roles=['owner'],
                                    source_requirements=links)
    other = constrain_input_schema(schema, 'read', rule, inputs, roles=['other'],
                                    source_requirements=links)
    assert owner['enum'] == [1] and other['enum'] == [2]
    assert not Draft202012Validator(owner).is_valid(2)


def test_valid_input_still_cannot_move_from_one_explicit_field_to_another():
    from test_product_design import project

    from agenttest.product_inputs import InputRequirement, input_fields, validate_requirements

    proj, _ = project()
    value = InputRequirement(operation_id='insertJob', location='body', name='quantity', path=[],
                             value=3, source_quote='page=3')
    with pytest.raises(ValidationFailure, match='FOCUS_INPUT_FIELD_CHANGED'):
        validate_requirements([value], dict(user_task='page=3', input_fields=input_fields(proj)),
                              ['insertJob'])


def test_whole_original_clause_cannot_disappear_in_selection_or_become_optional():
    context = ctx('读取资源，删除资源。')
    wire = {a['source_id']: link(a).model_dump(exclude={'source_id', 'source_quote'})
            for a in context['request_atoms']}
    validator = Draft202012Validator(requirement_schema(context))
    assert validator.is_valid(wire)
    dropped = dict(wire)
    dropped.pop('request_2')
    assert not validator.is_valid(dropped)
    with pytest.raises(ValidationFailure, match='FOCUS_SOURCE_CLAUSE_MISSING'):
        decode_requirements(dropped, context)
    wire['request_2']['disposition'] = 'optional'
    assert not validator.is_valid(wire)


def test_future_retry_cannot_request_an_action_relation_or_question_slot():
    context = ctx('读取资源留给以后重试。')
    assert [a['text'] for a in context['request_atoms']] == ['读取资源', '留给以后重试']
    future = context['request_atoms'][-1]
    assert future['future_only']
    with pytest.raises(ValidationFailure, match='FOCUS_FUTURE_SCOPE_INVALID'):
        validate_requirement_links([link(context['request_atoms'][0]), link(future)],
                                   context, selection())
    valid = [link(context['request_atoms'][0]), link(future, disposition='deferred', actions=[])]
    validate_requirement_links(valid, context, selection())


def test_only_future_actions_cannot_become_an_all_ready_project_round():
    context = ctx('这条记录留给以后重试')
    assert len(context['request_atoms']) == 1 and context['request_atoms'][0]['future_only']
    facts = [link(context['request_atoms'][0], disposition='deferred', actions=[])]
    chosen = selection()
    chosen.mode, chosen.operation_ids = 'no_current_action', []
    validate_requirement_links(facts, context, chosen)
    chosen.mode = 'all_ready'
    with pytest.raises(ValidationFailure, match='FOCUS_CURRENT_ACTION_MISSING'):
        validate_requirement_links(facts, context, chosen)
    assert not request_atoms('验证学生不得创建课程')[0]['constraint']


@pytest.mark.asyncio
async def test_future_only_web_task_makes_no_business_request_and_asks_no_question(
        tmp_path, monkeypatch):
    from uuid import uuid4

    import httpx
    from test_product_design import project, settings
    from test_product_focus import focus_response
    from test_product_tasks import finished, service

    from agenttest.product_focus import TestFocusModel
    from agenttest.project_intake import IntakeInput

    def focus(request):
        return focus_response(dict(interpretation='本轮仅保留未来重试，不发起当前测试',
            mode='no_current_action', operation_ids=[], question=None), request)

    svc = service(tmp_path, lambda _: pytest.fail('A future-only request called the designer'))
    svc.focus_factory = lambda _: TestFocusModel(settings(),
        transport=httpx.MockTransport(focus), profile='generic')
    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(
        lambda _: pytest.fail('A future-only request sent business HTTP')))
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name='未来动作保留', document=proj.document.decode(),
            generic=setup, confirmed=True), 'operator')
        done = await finished(svc, await svc.submit('operator', saved['id'],
            '这条记录留给以后重试', uuid4().hex))
        assert done['status'] == 'FINISHED' and done['summary']['complete']
        assert not done['summary']['questions'] and not done['summary']['cases']
        assert done['summary']['context_usage']['requests'] == 0
        assert done['summary']['context_usage']['model_calls'] == 1
        assert not done['summary']['checks_passed']
    finally:
        await svc.close()


def test_required_identity_and_action_have_actual_plan_witnesses():
    requirement = link(ctx('由other读取')['request_atoms'][0],
                       actions=[dict(operation_id='read', role='other')])
    with pytest.raises(ValidationFailure, match='DESIGN_REQUIRED_IDENTITY_ACTION_MISSING'):
        validate_plan_requirements(design([step('r', 'read')]), [requirement], ['read'])
    validate_plan_requirements(design([step('r', 'read', role='other')]), [requirement], ['read'])


def test_delete_before_read_is_not_satisfied_by_a_pre_delete_read():
    requirement = link(ctx('删除后读取')['request_atoms'][0],
        before=[dict(earlier='delete', later='read')])
    with pytest.raises(ValidationFailure, match='DESIGN_REQUIRED_ORDER_MISSING'):
        validate_plan_requirements(design([step('r', 'read'), step('d', 'delete')]),
                                   [requirement], ['read', 'delete'])
    validate_plan_requirements(design([step('d', 'delete'), step('r', 'read')]),
                               [requirement], ['read', 'delete'])


def test_same_resource_means_one_producer_in_one_context_for_all_consumers():
    requirement = link(ctx('读取并删除同一资源')['request_atoms'][0], same_resource=[
        dict(producer='create', consumer=op) for op in ('read', 'delete')])
    split = design([step('a', 'create'), step('r', 'read', source='a')],
                   [step('b', 'create'), step('d', 'delete', source='b')])
    with pytest.raises(ValidationFailure, match='DESIGN_SAME_RESOURCE_WITNESS_MISSING'):
        validate_plan_requirements(split, [requirement], ['create', 'read', 'delete'])
    together = design([step('a', 'create'), step('r', 'read', source='a'),
                       step('d', 'delete', source='a')])
    validate_plan_requirements(together, [requirement], ['create', 'read', 'delete'])


def test_content_expectation_requires_existing_policy_and_never_creates_an_assertion():
    requirement = link(ctx('核对返回名称')['request_atoms'][0], response_checks=['approved_name'])
    catalog = dict(approved_name=dict(operation_id='read', policy_id='check_name', roles=['owner'],
                                     select='$.name', operator='equals', expected='原文'))
    original = deepcopy(catalog)
    with pytest.raises(ValidationFailure, match='DESIGN_REQUIRED_RESPONSE_UNKNOWN'):
        validate_plan_requirements(design([step('r', 'read')]), [requirement], ['read'])
    with pytest.raises(ValidationFailure, match='DESIGN_REQUIRED_RESPONSE_CHECK_MISSING'):
        validate_plan_requirements(design([step('r', 'read')]), [requirement], ['read'], catalog)
    validate_plan_requirements(design([step('r', 'read', policy='check_name')]),
                               [requirement], ['read'], catalog)
    assert catalog == original


def test_response_check_cannot_borrow_a_step_from_another_identity():
    requirement = link(ctx('用 owner 核对返回名称')['request_atoms'][0],
                       response_checks=['approved_name'])
    catalog = dict(approved_name=dict(operation_id='read', policy_id='check_name',
                                     roles=['owner', 'other']))
    proposed = design([step('r', 'read'), step('other', 'read', role='other', policy='check_name')])
    with pytest.raises(ValidationFailure, match='DESIGN_REQUIRED_RESPONSE_CHECK_MISSING'):
        validate_plan_requirements(proposed, [requirement], ['read'], catalog)


def test_focus_cannot_attach_a_response_check_to_an_unrelated_action():
    context = ctx('核对读取内容')
    context['response_expectations'] = dict(content=dict(operation_id='delete',
                                                        policy_id='delete', roles=['owner']))
    chosen = selection([link(context['request_atoms'][0], response_checks=['content'])])
    with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_RESPONSE_INVALID'):
        validate_requirement_links(chosen.requirements, context, chosen)


def test_prohibition_is_not_an_executable_action_or_optional_coverage_escape():
    context = ctx('读取资源，不要delete资源。')
    items = [link(context['request_atoms'][0]), link(context['request_atoms'][1],
        disposition='constraint', actions=[], forbidden_operation_ids=['delete'])]
    validate_requirement_links(items, context, selection())
    with pytest.raises(ValidationFailure, match='DESIGN_FORBIDDEN_ACTION_PROPOSED'):
        validate_plan_requirements(design([step('r', 'read'), step('d', 'delete')]),
                                   items, ['read', 'delete'])
    # "不要漏测" is a request to test, not authority to discard that test.
    assert not request_atoms('不要漏测删除同一资源')[0]['constraint']


def test_ready_credentials_question_is_rejected_but_unknown_auth_behavior_is_preserved():
    from agenttest.product_focus import ExpectationQuestion, validate_expectation_questions

    context = dict(user_task='检查登录', managed_authentication=[dict(identity='owner',
        credential_status='READY')])
    redundant = ExpectationQuestion(operation_id='login', source_quote='检查登录',
        requested_behavior='登录账号', question='请提供使用的账号和密码。')
    with pytest.raises(ValidationFailure, match='FOCUS_READY_CREDENTIALS_QUESTION'):
        validate_expectation_questions([redundant], context, ['login'])
    unknown = redundant.model_copy(update=dict(requested_behavior='错误密码的拒绝语义',
        question='密码错误时预期返回什么状态码？'))
    validate_expectation_questions([unknown], context, ['login'])
    context['managed_authentication'].append(dict(identity='unused', credential_status='MISSING'))
    context['confirmed_response_checks'] = [dict(operation_id='login', roles=['owner'])]
    with pytest.raises(ValidationFailure, match='FOCUS_READY_CREDENTIALS_QUESTION'):
        validate_expectation_questions([redundant], context, ['login'])


def test_real_missing_input_is_backed_by_inventory_not_an_invented_business_question():
    context = ctx('读取资源，创建操作的必要输入仍缺失')
    context['approved_ids'] = ['read']
    context['unavailable_operations'] = [dict(operation_id='create', state='NEEDS_INPUT',
        reason='必填输入未确认')]
    chosen = selection()
    chosen.blocked_operation_ids = ['create']
    facts = [link(context['request_atoms'][0]), link(context['request_atoms'][1],
        disposition='needs_review', actions=[], blocked_operation_ids=['create'])]
    validate_requirement_links(facts, context, chosen)
    facts[-1].blocked_operation_ids = ['delete']
    with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE'):
        validate_requirement_links(facts, context, chosen)


def test_twelve_original_clauses_fit_existing_focus_budget_with_full_closed_validation():
    import json

    from test_product_design import settings

    from agenttest.product_focus import TestFocusModel

    operations = [f'readItem{i}' for i in range(5)]
    text = '；'.join(f'检查{operations[i % 5]}的第{i+1}项读取结果' for i in range(12))
    context = dict(user_task=text, approved_ids=operations, request_atoms=request_atoms(text),
        response_expectations={}, unavailable_operations=[], input_fields=[dict(
            operation_id=op, location='query', name='limit', path=[],
            schema=dict(type='integer', minimum=1, maximum=50)) for op in operations])
    model = TestFocusModel(settings(), profile='generic')
    envelope = json.loads(model.prepare_request(context))
    assert model.stats.input_token_upper_bound <= settings().max_input_tokens
    assert model.stats.calls == 0
    schema = envelope['response_format']['json_schema']['schema']
    maps = {atom['source_id']: dict(disposition='required',
        actions=[dict(operation_id=operations[i % 5], role=None)], before=[], same_resource=[],
        response_checks=[], forbidden_operation_ids=[], blocked_operation_ids=[])
        for i, atom in enumerate(context['request_atoms'])}
    wire = dict(interpretation='逐项核对十二段原文的读取要求', scope=dict(mode='selected',
        operations={op: dict(input_requirements=[], input_questions=[], requested_checks=[],
            expectation_questions=[], test_normal=True)
            for op in operations},
        question=None), blocked_operations={}, requirement_map=maps)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(wire), list(validator.iter_errors(wire))
    assert len(json.loads(model._decode_output(json.dumps(wire), context))['requirements']) == 12
    wire['requirement_map'].pop('request_12')
    assert not validator.is_valid(wire)


def test_source_backed_content_and_necessary_login_are_not_unrequested_test_expansion():
    context = ctx('核对读取内容')
    context['approved_ids'].append('login')
    context['response_expectations'] = dict(content=dict(operation_id='read', policy_id='read',
                                                        roles=['owner']))
    context['confirmed_response_checks'] = [dict(operation_id='read', roles=['owner'])]
    context['managed_authentication'][0]['login_operation'] = 'login'
    current = selection()
    current.operation_ids = ['read', 'login']
    facts = [link(context['request_atoms'][0], response_checks=['content'])]
    validate_requirement_links(facts, context, current)
    assert current.operation_ids == ['read', 'login']  # The original selection is retained.
    assert current.operation_sources == dict(read='USER_REQUIREMENT', login='TRUSTED_PREREQUISITE')
    current.operation_ids.append('delete')
    with pytest.raises(ValidationFailure) as error:
        validate_requirement_links(facts, context, current)
    assert error.value.diagnostic.fields == ['delete']
