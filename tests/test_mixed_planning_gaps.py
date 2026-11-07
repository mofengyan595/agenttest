"""Source-scoped resources and permission questions in mixed, partially held requests."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_focus_references import bundle, clause, wire
from test_product_design import settings
from test_product_tasks import finished, response, service
from test_request_requirements import ctx

from agenttest.diagnostics import ValidationFailure
from agenttest.generic_intake import GenericSetup, IntakeOperation, IntakeRole
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput
from agenttest.request_requirements import requirement_schema


def mixed_request():
    return '读取详情 detailId=2，验证 $.name 等于 "pending"；独立读取状态 statusId=2。'


def resource_context():
    context = ctx(mixed_request())
    context.update(approved_ids=['detail', 'status'], resource_dependencies=[],
        grouped_index=[dict(operations=[dict(id=op, method='GET') for op in ('detail', 'status')])],
        existing_resources=dict(
            detail=dict(operation_id='detail', role=None, path=dict(detailId=2)),
            status=dict(operation_id='status', role=None, path=dict(statusId=2))))
    return context


def requirements():
    return dict(request_1=clause('detail'),
        request_2=clause('detail', disposition='needs_review',
                        same_resource=[dict(resource_ref='detail')]),
        request_3=clause('status'))


def test_pending_content_resource_choices_are_bound_to_the_adjacent_source():
    context = resource_context()
    validator = Draft202012Validator(requirement_schema(context))
    maps = requirements()
    assert validator.is_valid(maps)
    maps['request_2']['same_resource'] = [dict(resource_ref='status')]
    assert not validator.is_valid(maps)


def test_pending_content_keeps_a_valid_fixed_path_without_authorizing_a_read():
    context = resource_context()
    raw = wire(dict(detail=bundle(test_normal=True), status=bundle(test_normal=True)),
               requirements())
    chosen = json.loads(FocusModel(settings(), profile='generic')._decode_output(
        json.dumps(raw), context))
    pending = chosen['requirements'][1]
    assert pending['disposition'] == 'needs_review'
    assert pending['existing_resources'] == [context['existing_resources']['detail']]


@pytest.mark.parametrize('fault', ['other_resource', 'other_role', 'wrong_value'])
def test_invalid_pending_resource_is_rejected_without_rebinding_it(fault):
    context = resource_context()
    raw = wire(dict(detail=bundle(test_normal=True), status=bundle(test_normal=True)),
               requirements())
    reference = 'status' if fault == 'other_resource' else 'detail'
    if fault == 'other_role':
        context['existing_resources']['detail']['role'] = 'owner'
    if fault == 'wrong_value':
        context['existing_resources']['detail']['path']['detailId'] = 3
    raw['requirement_map']['request_2']['same_resource'] = [dict(resource_ref=reference)]
    original = deepcopy(raw)
    with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_RESOURCE_INVALID') as error:
        FocusModel(settings(), profile='generic')._decode_output(json.dumps(raw), context)
    assert error.value.diagnostic.fields == [
        'requirement_map.request_2.existing_resources.0']
    assert raw == original


def test_permission_question_cannot_borrow_an_identity_from_another_clause():
    context = ctx('owner 读取详情，另获取服务版本。')
    context.update(approved_ids=['detail', 'version'], resource_dependencies=[],
        permission_question_roles=dict(detail=['other'], version=['owner']),
        confirmed_response_checks=[dict(operation_id='version', roles=[None], statuses=[200])])
    raw = wire(dict(detail=bundle(test_normal=True), version=bundle(test_normal=True,
        expectation_questions=[dict(kind='permission', role='owner', status_choices=[403, 404],
            question='版本是否需要特定身份权限？', requested_behavior='确认版本读取权限',
            source_ref='source_3')])), dict(request_1=clause('detail', 'owner'),
                                         request_2=clause('version')))
    model = FocusModel(settings(), profile='generic')
    validator = Draft202012Validator(json.loads(model.prepare_request(context))[
        'response_format']['json_schema']['schema'])
    assert not validator.is_valid(raw)
    with pytest.raises(ValidationFailure, match='FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION'):
        model._decode_output(json.dumps(raw), context)
    raw['scope']['operations']['version']['expectation_questions'] = []
    chosen = json.loads(model._decode_output(json.dumps(raw), context))
    assert chosen['requirements'][1]['actions'] == [dict(operation_id='version', role=None)]
    assert not chosen['expectation_questions']


def test_requested_unknown_permission_and_validated_previous_question_are_preserved():
    from agenttest.product_focus import ExpectationQuestion, validate_expectation_questions
    from agenttest.request_requirements import RequestRequirement

    text = 'other 读取详情时应当返回 403 还是 404'
    context = ctx(text)
    context.update(approved_ids=['detail'], permission_question_roles=dict(detail=['other']))
    context['managed_authentication'] = [dict(identity='other', credential_status='READY')]
    question = ExpectationQuestion(operation_id='detail', source_quote=text, kind='permission',
        role='other', status_choices=[403, 404], requested_behavior='跨身份读取详情',
        question='other 读取其他身份详情应返回 403 还是 404？')
    requirement = RequestRequirement(source_id='request_1', source_quote=text,
        **clause('detail', 'other', disposition='needs_review'))
    validate_expectation_questions([question], context, ['detail'], requirements=[requirement])
    context['previous_expectation_questions'] = [question.model_dump()]
    context.update(user_task='继续详情检查', request_atoms=ctx('继续详情检查')['request_atoms'])
    validate_expectation_questions([question], context, ['detail'], requirements=[])


def test_permission_source_does_not_match_a_longer_identity_alias():
    from agenttest.request_requirements import permission_source_texts

    context = ctx('owner2 读取详情，独立读取状态')
    assert not permission_source_texts(context, 'detail', 'owner')


def test_explicit_other_identity_intent_can_use_its_mapped_role():
    from agenttest.product_focus import ExpectationQuestion, validate_expectation_questions
    from agenttest.request_requirements import RequestRequirement

    text = '另一身份读取此详情时应当返回 403 还是 404'
    context = ctx(text)
    context['permission_question_roles'] = dict(detail=['other'])
    context['managed_authentication'] = [dict(identity='other', credential_status='READY')]
    question = ExpectationQuestion(operation_id='detail', source_quote=text, kind='permission',
        role='other', status_choices=[403, 404], requested_behavior='跨身份读取详情',
        question='另一身份读取此详情应返回 403 还是 404？')
    requirement = RequestRequirement(source_id='request_1', source_quote=text,
        **clause('detail', 'other', disposition='needs_review'))
    validate_expectation_questions([question], context, ['detail'], requirements=[requirement])


def read_setup():
    document = json.dumps(dict(openapi='3.0.3', info=dict(title='Mixed reads', version='1'),
        paths={f'/{op}/{{{name}}}': dict(get=dict(operationId=op, parameters=[
            dict(name=name, **{'in': 'path'}, required=True, schema=dict(type='integer'))],
            responses={'200': dict(description='ok')}))
            for op, name in [('detail', 'detailId'), ('status', 'statusId')]}))
    setup = GenericSetup(base_url='http://127.0.0.1:18998', isolated_confirmed=True,
        assisted=True, operations=[IntakeOperation(operation_id=op, alias=op, statuses=[200],
            arguments=[dict(location='path_parameters', name=name, value=2)],
            checks=[dict(select='$.name', operator='equals', expected='approved')],
            check_source='Operator-confirmed isolated fixture.')
            for op, name in [('detail', 'detailId'), ('status', 'statusId')]])
    return document, setup


@pytest.mark.asyncio
@pytest.mark.parametrize('placement', ['continuation', 'read'])
@pytest.mark.parametrize('invalid_resource', [False, True])
async def test_pending_fixed_resource_only_holds_its_own_read(
        tmp_path, monkeypatch, placement, invalid_resource):
    request = (mixed_request() if placement == 'continuation' else
        '读取详情 detailId=2 并验证 $.name 等于 "pending"；独立读取状态 statusId=2。')

    def focus(payload):
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        reference = next(key for key, value in context['existing_resources'].items()
                         if value['operation_id'] == ('status' if invalid_resource else 'detail'))
        maps = {atom['source_id']: clause('status' if '独立' in atom['text'] else 'detail',
            disposition='required' if 'pending' not in atom['text'] else 'needs_review',
            same_resource=[dict(resource_ref=reference)] if 'pending' in atom['text'] else [])
            for atom in context['request_atoms']}
        operations = {}
        for operation, name in [('detail', 'detailId'), ('status', 'statusId')]:
            source = next(s['source_id'] for s in context['input_sources']
                          if name + '=2' in s['text'] and s['text'] in {
                              atom['text'] for atom in context['request_atoms']})
            operations[operation] = bundle(test_normal=True, input_requirements=[
                dict(field_ref='path_parameters/' + name, value=2, source_ref=source)])
        return response(wire(operations, maps))

    def designer(payload):
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        assert context['primary_operations'] == ['status']
        policy, = context['policies']
        return response(dict(interpretation='完成独立状态读取，内容规则保留待审',
            questions=[], not_covered=[], cases=[dict(title='独立读取', kind='normal',
                plan=dict(name='status', steps=[dict(id='s', operation_id='status', role=None,
                    policy_id=policy['id'], arguments=[dict(location='path_parameters',
                        name='statusId', value=2, from_step=None, select=None)])]))]))

    sent = []

    def target(payload):
        sent.append(payload.url.path)
        return httpx.Response(200, json=dict(name='approved'))

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile='generic')
    try:
        document, setup = read_setup()
        saved = svc.intake.save(IntakeInput(name='待审资源关联', document=document, generic=setup,
                                           confirmed=True), 'operator')
        row = await svc.submit('operator', saved['id'], request, uuid4().hex)
        done = await finished(svc, row)
        summary = done['summary']
        if invalid_resource:
            assert done['status'] == 'NEEDS_REVIEW' and not sent
            assert summary['design_records'][0]['diagnostic']['code'] == (
                'FOCUS_REQUIREMENT_RESOURCE_INVALID')
            return
        assert done['status'] == 'FINISHED' and not summary['complete']
        assert sent == ['/status/2']
        entry, = summary['response_requirements']
        assert entry['status'] == 'NEEDS_REVIEW'
        assert entry['expected'] == 'pending' and entry['blocked_operations'] == ['detail']
        assert all(item['status'] == 'ACCOUNTED' for item in summary['requirement_ledger'])
        assert summary['cases'][0]['outcome'] == 'PASS'
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('unrequested_question', [False, True])
async def test_mixed_read_does_not_invent_an_anonymous_permission_check(
        tmp_path, monkeypatch, unrequested_question):
    def focus(payload):
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        atoms = context['request_atoms']
        operations = {}
        for operation, name, atom in [('detail', 'detailId', atoms[0]),
                                       ('status', 'statusId', atoms[1])]:
            source = next(s['source_id'] for s in context['input_sources']
                          if s['text'] == atom['text'])
            questions = [dict(kind='permission', role='owner', status_choices=[403, 404],
                question='状态读取是否需要特定身份权限？', requested_behavior='确认状态读取权限',
                source_ref=source)] if unrequested_question and operation == 'status' else []
            operations[operation] = bundle(test_normal=True, expectation_questions=questions,
                input_requirements=[dict(field_ref='path_parameters/' + name, value=2,
                                         source_ref=source)])
        return response(wire(operations, dict(request_1=clause('detail', 'owner'),
                                             request_2=clause('status'))))

    def designer(payload):
        assert not unrequested_question
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        policies = {p['operation_id']: p for p in context['policies']}
        return response(dict(interpretation='按已确认的身份分别读取详情与公开状态',
            questions=[], not_covered=[], cases=[dict(title='身份与公开读取', kind='normal',
                plan=dict(name='reads', steps=[dict(id=operation, operation_id=operation,
                    role=role, policy_id=policies[operation]['id'], arguments=[dict(
                        location='path_parameters', name=name, value=2,
                        from_step=None, select=None)])
                    for operation, name, role in [('detail', 'detailId', 'owner'),
                                                   ('status', 'statusId', None)]]))]))

    sent = []

    def target(payload):
        sent.append((payload.url.path, 'authorization' in payload.headers))
        return httpx.Response(200, json=dict(name='approved'))

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.environ['AGENTTEST_TARGET_OWNER'] = 'synthetic-local-bearer'
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile='generic')
    try:
        document, setup = read_setup()
        parsed = json.loads(document)
        parsed['components'] = dict(securitySchemes=dict(bearerAuth=dict(
            type='http', scheme='bearer')))
        parsed['paths']['/detail/{detailId}']['get']['security'] = [dict(bearerAuth=[])]
        document = json.dumps(parsed)
        setup.roles = [IntakeRole(alias='owner', bearer_env='AGENTTEST_TARGET_OWNER')]
        setup.operations[0].role = 'owner'
        saved = svc.intake.save(IntakeInput(name='公开读取与身份', document=document, generic=setup,
                                           confirmed=True), 'operator')
        row = await svc.submit('operator', saved['id'],
            'owner 读取详情 detailId=2，独立读取状态 statusId=2。', uuid4().hex)
        done = await finished(svc, row)
        summary = done['summary']
        if unrequested_question:
            assert done['status'] == 'NEEDS_REVIEW' and not sent
            assert summary['design_records'][0]['diagnostic']['code'] == (
                'FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION')
        else:
            assert done['status'] == 'FINISHED' and summary['complete']
            assert not summary['questions']
            assert sent == [('/detail/2', True), ('/status/2', False)]
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_anonymous_read_does_not_offer_unrequested_permission_roles():
    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import build_generic_project
    from agenttest.product_focus import select_focus

    captured = {}

    class Capture:
        async def generate(self, context):
            captured.update(context)
            raise RuntimeError('context captured')

    document, setup = read_setup()
    setup.roles = [IntakeRole(alias='owner', bearer_env='AGENTTEST_TARGET_OWNER')]
    setup.operations[0].role = 'owner'
    environ = dict(AGENTTEST_TARGET_OWNER='synthetic-local-bearer')
    project = build_generic_project('roles', document, load_openapi(document), setup,
                                    environ=environ)
    with pytest.raises(RuntimeError, match='context captured'):
        await select_focus(project, Capture(), dict(current_request=
            'owner 读取详情 detailId=2，另查状态 statusId=2。'), environ=environ)
    question = dict(kind='permission', role='owner', status_choices=[403, 404],
        question='状态接口是否需要特定身份权限？', requested_behavior='确认状态读取权限',
        source_ref='source_3')
    raw = wire(dict(detail=bundle(test_normal=True), status=bundle(test_normal=True,
        expectation_questions=[question])), dict(request_1=clause('detail', 'owner'),
                                                 request_2=clause('status')))
    schema = json.loads(FocusModel(settings(), profile='generic').prepare_request(captured))[
        'response_format']['json_schema']['schema']
    assert not Draft202012Validator(schema).is_valid(raw)
