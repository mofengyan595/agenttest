"""Omissions are checked against the original request, including unselected fields."""

import json

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.product_focus import FocusSelection
from agenttest.requirement_ledger import (
    explicit_assignments,
    explicit_http_statuses,
    validate_assignment_coverage,
)


def selection(**kwargs):
    return FocusSelection(interpretation="检查分页字段", mode="selected", operation_ids=["list"],
        question=None, pending_questions=[], not_covered=[], **kwargs)


def context(text):
    return dict(user_task=text, input_fields=[dict(operation_id="list", name=n)
        for n in ("page", "size")], approved_checks=[])


def requirement(name, value, quote):
    return dict(operation_id="list", location="query", name=name, path=[], value=value,
                source_quote=quote)


def test_selector_cannot_hide_size_zero_by_selecting_only_page_zero():
    ctx = context("检查page=0和size=0。")
    chosen = selection(input_requirements=[requirement("page", 0, "page=0")])
    with pytest.raises(ValidationFailure) as error:
        validate_assignment_coverage(ctx, chosen)
    assert error.value.diagnostic.code == "FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED"
    assert [x['status'] for x in error.value.requirement_ledger] == ['ACCOUNTED', 'UNACCOUNTED']
    chosen.input_requirements.append(type(chosen.input_requirements[0]).model_validate(
        requirement("size", 0, "size=0")))
    assert all(x['status'] == 'ACCOUNTED' for x in validate_assignment_coverage(ctx, chosen))


def test_unknown_field_and_unselected_operation_do_not_disappear():
    with pytest.raises(ValidationFailure):
        validate_assignment_coverage(context("检查unknown=7"), selection())
    with pytest.raises(ValidationFailure):
        validate_assignment_coverage(context("检查size=0"), selection())


def test_future_clause_does_not_request_execution_or_a_question():
    ledger = validate_assignment_coverage(context("留给下次检查size=0。"), selection())
    assert ledger[0]['status'] == 'DEFERRED'
    assert ledger[0]['source_quote'] == 'size=0'


def test_future_action_does_not_hide_current_required_assignment():
    for text in ('现在检查size=0，留给以后重试。', '本轮检查size=0并保留给下次重试。'):
        with pytest.raises(ValidationFailure):
            validate_assignment_coverage(context(text), selection())


def test_parser_preserves_scalar_type_source_offsets_and_identifier_boundaries():
    text = '检查size＝0；name="x"；flag=true；other=null；标题=3；foo.size=4'
    items = explicit_assignments(text)
    assert [x['value'] for x in items] == [0, 'x', True, None]
    assert all(text[x['start']:x['end']] == x['source_quote'] for x in items)


def test_status_parser_preserves_current_literals_and_marks_alternatives_unresolved():
    text = ('核对消息“HTTP 500”，返回200条，确认状态码为 201；'
            '检查 HTTP 200 或 204；下次再验证 HTTP 422。')
    exact, ambiguous = explicit_http_statuses(text)
    assert (exact['expected'], ambiguous['expected']) == (201, 200)
    assert text[exact['start']:exact['end']] == '状态码为 201'
    assert ambiguous['reason'] == 'HTTP_STATUS_SCOPE_AMBIGUOUS'


def test_approved_negative_is_accounted_without_creating_a_new_expectation():
    ctx = context("检查size=0")
    ctx['approved_checks'] = [dict(policy_id='bad_size', name='size', operation_id='list',
                                   invalid_value=0)]
    chosen = selection(requested_checks=[dict(policy_id='bad_size', source_quote='size=0')])
    assert validate_assignment_coverage(ctx, chosen)[0]['status'] == 'ACCOUNTED'
    chosen.requested_checks[0].source_quote = '0'
    with pytest.raises(ValidationFailure):
        validate_assignment_coverage(ctx, chosen)


@pytest.mark.parametrize('assertion,extra,status,requests', [
    ('$.items 存在', '', 'PASS', ['/records']),
    ('$.items 存在', '', 'ASSERTION_FAILED', ['/records', '/records']),
    # The approved read still runs; only the unapproved content assertion is held.
    ('$.items 非空', '', 'NEEDS_REVIEW', ['/records']),
    ('$.items 数量为 2', '', 'NEEDS_REVIEW', ['/records']),
    ('$.items 非空', '；检查健康', 'NEEDS_REVIEW', ['/health', '/records']),
    ('$.items 数量为 3', '；检查健康', 'NEEDS_REVIEW', ['/health', '/records']),
    ('$.items 数量为 4', '；检查健康', 'NEEDS_REVIEW', ['/health', '/records']),
    ('只用未确认的方式检查记录', '；检查健康', 'NEEDS_REVIEW', ['/health']),
])
@pytest.mark.asyncio
async def test_explicit_response_cannot_disappear_from_focus(tmp_path, monkeypatch, assertion,
                                                            extra, status, requests):
    import httpx
    from test_product_tasks import finished, service

    from agenttest.product_focus import FocusSelection
    from agenttest.project_intake import IntakeInput
    from agenttest.report import ModelCallStats

    seen = []
    review_only = assertion.startswith('只用')
    question_only = review_only or '数量为 3' in assertion
    predicate_only = '数量为 4' in assertion

    def target(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={} if status == 'ASSERTION_FAILED' else {'items': []})

    class Focus:
        stats = ModelCallStats(calls=1, input_tokens=1, output_tokens=1, total_tokens=2)

        async def generate(self, context):
            operations = ['records', 'health'] if extra else ['records']
            requirements = [dict(source_id=atom['source_id'], source_quote=atom['text'],
                disposition='required', actions=[dict(operation_id=(
                    'health' if '健康' in atom['text'] else 'records'), role=None)],
                before=[], same_resource=[], response_checks=[])
                for atom in context['request_atoms']]
            questions = []
            if question_only or predicate_only:
                pending = requirements[0 if review_only else 1]
                pending.update(disposition='needs_review', actions=pending['actions']
                               if review_only else [])
                if question_only:
                    questions.append(dict(operation_id='records', question='请确认规则依据',
                        requested_behavior='核对尚未获准的检查',
                        source_quote=pending['source_quote']))
            return FocusSelection(interpretation='核对记录', mode='selected',
                operation_ids=operations, normal_operation_ids=(['health'] if review_only
                    else operations), question=None, pending_questions=[], not_covered=[],
                expectation_questions=questions, requirements=requirements).model_dump_json()

    class Design:
        stats = Focus.stats

        async def generate(self, context):
            cases = [dict(title='读取记录', kind='normal', plan=dict(name='records', steps=[dict(
                id='read', operation_id=policy['operation_id'], policy_id=policy['id'],
                role=None, arguments=[])])) for policy in context['policies']
                if policy['operation_id'] in context['primary_operations']]
            return json.dumps(dict(interpretation='核对记录', cases=cases, questions=[],
                                   not_covered=[]))

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, lambda _: pytest.fail('unexpected model'))
    svc.focus_factory = lambda _: Focus()
    svc.designer_factory = lambda _: Design()
    try:
        paths = {'/records': dict(get=dict(operationId='records', responses={
                '200': dict(description='ok', content={'application/json': dict(schema=dict(
                    type='object', properties={'items': dict(type='array', items=dict(
                        type='string'))}))})}))}
        operations = [dict(operation_id='records', alias='读取记录',
            statuses=[200], check_source='已核对记录结构', checks=[dict(
                select='$.items', operator='exists')])]
        if extra:
            paths['/health'] = dict(get=dict(operationId='health', responses={
                '200': dict(description='ok')}))
            operations.append(dict(operation_id='health', alias='检查健康', statuses=[200]))
        document = json.dumps(dict(openapi='3.0.3', info=dict(title='Records', version='1'),
                                   paths=paths))
        saved = svc.intake.save(IntakeInput(name='记录', document=document, confirmed=True,
            generic=dict(base_url='http://127.0.0.1:18998', isolated_confirmed=True,
                assisted=True, operations=operations)), 'operator')
        text = (assertion if review_only else f'读取记录，并验证 {assertion}') + extra + '。'
        result = await finished(svc, await svc.submit('operator', saved['id'], text,
                                                      'response-witness'))
        summary = result['summary']
        assert sorted(seen) == sorted(requests), summary.get('design_records')
        assert summary['complete'] is (status in {'PASS', 'ASSERTION_FAILED'})
        if review_only:
            # No separate approved read exists here, so the whole action stays held.
            assert summary['input_blocked_operations'] == ['records']
            return
        entry, = summary['response_requirements']
        if status == 'NEEDS_REVIEW':
            assert entry['held_operations'] == ['records']
            assert entry['blocked_operations'] == []
            assert not summary.get('input_blocked_operations')
        assert summary['checks_passed'] is (status == 'PASS')
        entry, = summary['response_requirements']
        assert entry['status'] == status
        assert entry['source_id'] == 'request_2'
        if status in {'PASS', 'ASSERTION_FAILED'}:
            witness, = entry['witnesses']
            assert witness['run_id'] and witness['policy_id'] and witness['step_id'] == 'read'
            assert witness['status'] == status
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_all_cases_are_validated_before_first_business_write(tmp_path, monkeypatch):
    import httpx
    from test_product_design import case, project
    from test_product_tasks import finished, response, service

    from agenttest import product_tasks
    from agenttest.project_intake import IntakeInput

    events = []
    validate = product_tasks.validate_design_case

    def preflight(*args, **kwargs):
        events.append('validate')
        return validate(*args, **kwargs)

    def target(request):
        events.append('http')
        return httpx.Response(201, json=dict(id='new-owned-resource'))

    def model(request):
        # Distinct values are distinct independent setup scenarios, not resource reuse.
        return response(dict(interpretation='检查两种合法数量', cases=[case(quantity=1),
            case(quantity=3)], questions=[], not_covered=[]))

    monkeypatch.setattr(product_tasks, 'validate_design_case', preflight)
    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name='前置校验', document=proj.document.decode(),
            generic=setup, confirmed=True), 'operator')
        done = await finished(svc, await svc.submit('operator', saved['id'],
            '分别用数量1和3创建任务', 'preflight'))
        assert done['status'] == 'FINISHED', done
        assert events == ['validate', 'validate', 'http', 'http']
    finally:
        await svc.close()


def test_trusted_state_chain_stays_in_one_context_across_batch_boundary():
    import json

    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import GenericSetup, build_generic_project
    from agenttest.product_design import design_groups
    from agenttest.product_inputs import input_blocked_operations

    document = json.dumps(dict(openapi='3.0.3', info=dict(title='State chain', version='1'),
        paths={f'/r{i}': dict(get=dict(operationId=f'read{i}', responses={
            '200': dict(description='ok')})) for i in range(8)}))
    setup = GenericSetup.model_validate(dict(base_url='http://127.0.0.1:18998',
        isolated_confirmed=True, assisted=True, operations=[dict(operation_id=f'read{i}',
            alias=f'检查阶段{i}', statuses=[200],
            after_operations=[f'read{i-1}'] if 0 < i < 7 else []) for i in range(8)]))
    proj = build_generic_project('chain', document, load_openapi(document), setup)
    groups = design_groups(proj)
    assert groups[0]['primary'] == [f'read{i}' for i in range(7)]
    assert groups[0]['supported']
    assert groups[1]['primary'] == ['read7']
    # Selecting the terminal read still requires its entire trusted chain.
    assert set(design_groups(proj, operation_ids=['read6'])[0]['operations']) == {
        f'read{i}' for i in range(7)}
    assert input_blocked_operations(proj, [], operation_ids=['read0']) == [
        f'read{i}' for i in range(7)]
