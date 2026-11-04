"""Omissions are checked against the original request, including unselected fields."""

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.product_focus import FocusSelection
from agenttest.requirement_ledger import explicit_assignments, validate_assignment_coverage


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


def test_approved_negative_is_accounted_without_creating_a_new_expectation():
    ctx = context("检查size=0")
    ctx['approved_checks'] = [dict(policy_id='bad_size', name='size', operation_id='list',
                                   invalid_value=0)]
    chosen = selection(requested_checks=[dict(policy_id='bad_size', source_quote='size=0')])
    assert validate_assignment_coverage(ctx, chosen)[0]['status'] == 'ACCOUNTED'
    chosen.requested_checks[0].source_quote = '0'
    with pytest.raises(ValidationFailure):
        validate_assignment_coverage(ctx, chosen)


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
