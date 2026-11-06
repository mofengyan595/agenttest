"""Regression witnesses for numeric provenance and configured read resources."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_negative_values import case as report_case
from test_negative_values import policy, read_project
from test_product_design import settings
from test_product_tasks import finished, response, service
from test_request_requirements import ctx, design, step

from agenttest.diagnostics import ValidationFailure
from agenttest.product_checks import check_index
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.product_focus_wire import operation_checks
from agenttest.product_inputs import input_fields
from agenttest.product_sources import input_sources
from agenttest.project_intake import IntakeInput
from agenttest.request_requirements import validate_plan_requirements


def clause(operation, role=None, **overrides):
    return dict(dict(disposition='required', actions=[dict(operation_id=operation, role=role)],
                     before=[], same_resource=[], response_checks=[], forbidden_operation_ids=[],
                     blocked_operation_ids=[]), **overrides)


def bundle(**overrides):
    return dict(dict(input_requirements=[], input_questions=[], requested_checks=[],
                     expectation_questions=[], test_normal=False),
                **overrides)


def wire(operations, requirements):
    return dict(interpretation='按原文和已确认配置核对', blocked_operations={},
        scope=dict(mode='selected', operations=operations, question=None),
        requirement_map=requirements)


@pytest.mark.asyncio
@pytest.mark.parametrize(('expected', 'actual', 'state', 'sent_count'), [
    (201, 200, 'NEEDS_REVIEW', 0),
    (200, 200, 'PASS', 1),
    (200, 422, 'ASSERTION_FAILED', 2),  # Existing read-only investigation follows the failure.
])
async def test_explicit_http_status_requires_an_approved_assertion_and_real_evidence(
        tmp_path, monkeypatch, expected, actual, state, sent_count):
    project, setup = read_project()
    normal = next(p for p in project.registry.definitions() if not p.negative)

    def focus(_):
        return response(wire(dict(readReports=bundle(test_normal=True)), {
            'request_1': clause('readReports'), 'request_2': clause('readReports'),
        }))

    def designer(_):
        return response(dict(interpretation='读取报告并核对状态', questions=[], not_covered=[],
                             cases=[report_case(normal.id)]))

    sent = []

    def target(request):
        sent.append(request.url.path)
        return httpx.Response(actual, json={'ok': True})

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile='generic')
    try:
        saved = svc.intake.save(IntakeInput(name='状态见证', document=project.document.decode(),
                                           generic=setup, confirmed=True), 'operator')
        row = await svc.submit('operator', saved['id'], f'读取报告，确认 HTTP {expected}。',
                               uuid4().hex)
        summary = (await finished(svc, row))['summary']
        assert len(sent) == sent_count
        status, = summary['http_status_requirements']
        assert status['source_id'] == 'request_2' and status['expected'] == expected
        assert status['status'] == state
        assert summary['complete'] is (sent_count > 0)
        assert summary['checks_passed'] is (state == 'PASS')
        if sent_count:
            witness, = status['witnesses']
            assert witness['actual'] == actual and witness['step_id'] == 'read'
            assert witness['run_id'] == summary['cases'][0]['run_id']
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_rule_only_question_does_not_trigger_model_proposed_sampling(tmp_path, monkeypatch):
    project, setup = read_project()
    text = '报告列表是否承诺按标题升序排列并把缺少的排序规则告诉我。'

    def focus(request):
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        atom, = context['request_atoms']
        source = next(s['source_id'] for s in context['input_sources'] if s['text'] == atom['text'])
        question = dict(kind='business_rule', role=None, status_choices=[], source_ref=source,
                        requested_behavior='核对报告排序承诺', question='确认报告按什么字段排序？')
        return response(wire(dict(readReports=bundle(test_normal=True,
            expectation_questions=[question])), {atom['source_id']: clause('readReports')}))

    def unexpected(_):
        pytest.fail('A contract-only question must not call the designer or target')

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(unexpected))
    svc = service(tmp_path, unexpected)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile='generic')
    try:
        saved = svc.intake.save(IntakeInput(name='排序待确认', document=project.document.decode(),
                                           generic=setup, confirmed=True), 'operator')
        row = await svc.submit('operator', saved['id'], text, uuid4().hex)
        result = await finished(svc, row)
        summary = result['summary']
        assert not summary['complete'] and summary['context_usage']['requests'] == 0
        assert summary['expectation_blocked_operations'] == ['readReports']
        assert summary['questions'][0]['operation_id'] == 'readReports'
    finally:
        await svc.close()


def test_rule_only_login_is_removed_from_primary_and_kept_for_independent_reads():
    from types import SimpleNamespace

    from agenttest.product_focus import FocusSelection, rule_review_scope
    from agenttest.request_requirements import request_atoms

    rule = '报表接口有没有明确约定按标题排序'
    actions = [dict(operation_id=op, role='tester') for op in ('login', 'reports')]
    project = SimpleNamespace(contract=SimpleNamespace(operations=[
        SimpleNamespace(operation_id='reports', method='GET')]),
        target=SimpleNamespace(roles={'tester': SimpleNamespace(login_operation='login')}),
        registry=SimpleNamespace(definitions=lambda: [
            SimpleNamespace(id='read_reports', operation_id='reports', roles=['tester'])]))
    for execute in (False, True):
        requirements = [dict(source_id=a['source_id'], source_quote=a['text'],
            **clause('reports', actions=actions)) for a in request_atoms(
                rule + ('。实际读取报表一遍。' if execute else ''))]
        selection = FocusSelection(interpretation='已确认读取和排序问题', mode='selected',
            operation_ids=['login', 'reports'], question=None, pending_questions=[], not_covered=[],
            requirements=requirements, expectation_questions=[dict(operation_id='reports',
                source_quote=rule, question='请确认报告标题排序规则',
                requested_behavior='排序规则')])
        expected = (set(), set()) if execute else ({'reports'}, {'login'})
        assert rule_review_scope(project, selection) == expected


@pytest.mark.parametrize(('text', 'actions', 'missing'), [
    ('查询资料', [], ['actions']),
    ('查询同一资料', [dict(operation_id='read', role=None)], ['same_resource']),
    ('完成后查询资料', [dict(operation_id='read', role=None)], ['before']),
    ('完成后查询同一资料', [], ['actions', 'same_resource', 'before']),
])
def test_missing_witness_diagnostic_names_clause_and_fields(text, actions, missing):
    context = ctx(text)
    proposal = wire(dict(create=None, read=bundle(), delete=None), {
        'request_1': clause('read', actions=actions),
    })
    with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_WITNESS_MISSING') as exc:
        FocusModel(settings(), profile='generic')._decode_output(json.dumps(proposal), context)
    fields = [f'requirement_map.request_1.{field}' for field in missing]
    assert exc.value.diagnostic.fields == fields


@pytest.mark.asyncio
@pytest.mark.parametrize(('request_text', 'variants', 'explicit'), [
    ('匿名检查 readReports 的 limit=9 上界，预期 HTTP 422。', True, True),
    ('匿名检查 readReports 的 limit=9 上界，预期 HTTP 403。', True, False),
    ('匿名检查 readReports 的 limit=9 下界，预期 HTTP 422。', True, False),
    ('匿名检查 readReports 的 limit=9 或 limit=8 上界。', True, False),
    ('匿名检查 readReports 的 limit=9 上界。再检查 limit=8 上界。', True, False),
    ('匿名检查 readReports 的 limit=9 上界，预期 HTTP 422。', False, False),
])
async def test_only_unique_approved_values_get_actions(request_text, variants, explicit):
    from agenttest.product_focus import select_focus

    project, _ = read_project(variants=variants)
    model = FocusModel(settings(), profile='generic')

    class Capture:
        async def generate(self, context):
            clauses = {a['source_id']: clause('readReports', actions=[])
                       for a in context['request_atoms']}
            proposal = wire(dict(readReports=bundle()), clauses)
            assert bool(context.get('boundary_links')) == explicit
            if explicit:
                payload = json.loads(model.prepare_request(context))
                schema = payload['response_format']['json_schema']['schema']
                assert Draft202012Validator(schema).is_valid(proposal)
            return model._decode_output(json.dumps(proposal), context)

    if not explicit:
        with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_WITNESS_MISSING'):
            await select_focus(project, Capture(), dict(current_request=request_text), environ={})
        return
    result = await select_focus(project, Capture(), dict(current_request=request_text), environ={})
    assert [[a.operation_id for a in r.actions] for r in result.requirements] == [
        ['readReports'], ['readReports']]
    assert len(result.requested_checks) == 1 and result.requested_checks[0].exact_value.value == 9


@pytest.mark.asyncio
async def test_confirmed_boundary_sends_exact_negative(tmp_path, monkeypatch):
    project, setup = read_project()
    approved = policy(project, kind='boundary_outside', value=4)

    def focus(request):
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert context['boundary_links'][0]['explicit']
        return response(wire(dict(readReports=bundle()), {
            a['source_id']: clause('readReports', actions=[]) for a in context['request_atoms']}))

    def designer(request):
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        check, = context['requested_checks']
        assert check['policy_id'] == approved.id and check['exact_value'] == {'value': 9}
        negative = report_case(approved.id)['plan']['steps'][0]
        return response(dict(interpretation='执行已确认边界', relevant=True, questions=[],
            not_covered=[], cases=[], required_cases={approved.id: dict(title='核对上界拒绝',
                before=[], check=negative, after=[])}))

    sent = []

    def target(request):
        sent.append((request.method, dict(request.url.params)))
        return httpx.Response(422)

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile='generic')
    try:
        saved = svc.intake.save(IntakeInput(name='已确认上界', document=project.document.decode(),
                                           generic=setup, confirmed=True), 'operator')
        text = '匿名检查 readReports 的 limit=9 上界，预期 HTTP 422。'
        row = await svc.submit('operator', saved['id'], text, uuid4().hex)
        done = await finished(svc, row)
        assert done['summary']['complete'] and done['summary']['checks_passed']
        assert done['summary']['coverage']['executed_requested_checks'] == 1
        assert sent == [('GET', {'limit': '9', 'order': 'asc'})]
    finally:
        await svc.close()


def test_named_boundary_wire_resolves_value_without_retyping_or_reusing_status():
    project, _ = read_project()
    text = '以limit=9验证报告列表上界，预期返回422。'
    context = dict(ctx(text), approved_ids=['readReports'], input_fields=input_fields(project),
                   approved_checks=check_index(project), resource_dependencies=[])
    approved = operation_checks(context['approved_checks'])['readReports']
    reference = next(k for k, v in approved.items()
                     if v['policy_id'] == policy(project, kind='boundary_outside', value=4).id)
    atom = context['request_atoms'][0]
    source_ref = next(s['source_id'] for s in input_sources(context) if s['text'] == atom['text'])
    check = dict(check_ref=reference, source_ref=source_ref, exact_value=None)
    raw = wire(dict(readReports=bundle(requested_checks=[check])), {
        a['source_id']: clause('readReports') for a in context['request_atoms']})
    model = FocusModel(settings(), profile='generic')
    schema = json.loads(model.prepare_request(context))['response_format']['json_schema']['schema']
    validator = Draft202012Validator(schema)
    assert validator.is_valid(raw)
    chosen = json.loads(model._decode_output(json.dumps(raw), context))['requested_checks'][0]
    assert chosen['exact_value'] == {'value': 9} and chosen['source_quote'] == atom['text']
    check['exact_value'] = {'value': 422}
    assert not validator.is_valid(raw)
    with pytest.raises(ValidationFailure, match='FOCUS_INPUT_NUMBER_CHANGED'):
        model._decode_output(json.dumps(raw), context)
    check['exact_value'] = None
    check['source_ref'] = 'source_1'  # Broad text is not the explicit current clause.
    assert not validator.is_valid(raw)
    with pytest.raises(ValidationFailure, match='FOCUS_NEGATIVE_SOURCE_INVALID'):
        model._decode_output(json.dumps(raw), context)


def test_fixed_resource_ref_uses_configured_role_and_literal_for_every_case():
    from agenttest.product_focus import FocusSelection

    context = ctx('管理员读取资源22。对同一资源检查越界。')
    resource = dict(operation_id='read', role='owner', path=dict(id=22))
    context.update(existing_resources={'resource_1': resource}, resource_dependencies=[])
    raw = wire(dict(create=None, read=bundle(), delete=None), {
        'request_1': clause('read', 'owner'),
        'request_2': clause('read', 'owner', same_resource=[dict(resource_ref='resource_1')]),
    })
    model = FocusModel(settings(), profile='generic')
    schema = json.loads(model.prepare_request(context))['response_format']['json_schema']['schema']
    assert Draft202012Validator(schema).is_valid(raw)
    chosen = FocusSelection.model_validate_json(model._decode_output(json.dumps(raw), context))
    assert chosen.requirements[1].existing_resources[0].model_dump() == resource

    def read(value):
        return [dict(step('read', 'read'), arguments=[dict(location='path_parameters', name='id',
            value=value, from_step=None, select=None)])]

    validate_plan_requirements(design(read(22), read(22)), chosen.requirements, ['read'])
    with pytest.raises(ValidationFailure, match='DESIGN_EXISTING_RESOURCE_WITNESS_MISSING'):
        validate_plan_requirements(design(read(22), read(23)), chosen.requirements, ['read'])
    for relation in (dict(producer='read', consumer='read'), dict(resource_ref='not_approved')):
        changed = deepcopy(raw)
        changed['requirement_map']['request_2']['same_resource'] = [relation]
        assert not Draft202012Validator(schema).is_valid(changed)
        with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_RESOURCE_INVALID'):
            model._decode_output(json.dumps(changed), context)
    wrong_context = ctx('管理员读取资源23。对同一资源检查越界。')
    wrong_context.update(existing_resources=context['existing_resources'], resource_dependencies=[])
    with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_RESOURCE_INVALID'):
        model._decode_output(json.dumps(raw), wrong_context)


@pytest.mark.asyncio
async def test_pending_numeric_check_does_not_send_default_or_block_independent_health(
        tmp_path, monkeypatch):
    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import IntakeOperation, build_generic_project

    project, setup = read_project(variants=False)
    document = json.loads(project.document)
    document['paths']['/health'] = {'get': dict(operationId='health', responses={
        '200': dict(description='up')})}
    raw_document = json.dumps(document)
    setup.operations.append(IntakeOperation(operation_id='health', alias='健康', statuses=[200]))
    project = build_generic_project('reports', raw_document, load_openapi(raw_document), setup)
    caps = operation_checks(check_index(project))['readReports']
    lower = next(k for k, c in caps.items() if c.get('invalid_value') == 0)
    health = next(p for p in project.registry.definitions() if p.operation_id == 'health')

    def focus(request):
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        quote = context['request_atoms'][1]['text']
        source = next(s['source_id'] for s in context['input_sources'] if s['text'] == quote)
        return response(wire(dict(health=bundle(test_normal=True), readReports=bundle(
            requested_checks=[dict(check_ref=lower, exact_value=None, source_ref=source)])), {
                'request_1': clause('health'),
                'request_2': clause('readReports', disposition='needs_review'),
            }))

    def designer(_):
        return response(dict(interpretation='执行独立健康检查', relevant=True, questions=[],
            not_covered=[], cases=[dict(title='健康状态', kind='normal', plan=dict(name='health',
                steps=[dict(id='health', operation_id='health', role=None, policy_id=health.id,
                            arguments=[])]))]))

    sent = []

    def target(request):
        sent.append(request.url.path)
        return httpx.Response(200)

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile='generic')
    try:
        saved = svc.intake.save(IntakeInput(name='边界与健康', document=raw_document, generic=setup,
                                           confirmed=True), 'operator')
        request = '核对公开健康。检查报告limit=-7的下界拒绝。'
        row = await svc.submit('operator', saved['id'], request, uuid4().hex)
        result = await finished(svc, row)
        summary = result['summary']
        assert result['status'] == 'FINISHED' and not summary['complete']
        assert summary['pending_negative_values'][0]['requested_value'] == -7
        assert sent == ['/health']
    finally:
        await svc.close()
