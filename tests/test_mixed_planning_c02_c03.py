"""Deterministic C02/C03 counterexamples; no model or external target access."""

import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_focus_references import bundle, clause, wire
from test_mixed_planning_gaps import read_setup
from test_product_design import settings
from test_product_tasks import finished, response, service
from test_request_requirements import ctx

from agenttest.diagnostics import ValidationFailure
from agenttest.generic_intake import IntakeArgument, IntakeRole
from agenttest.product_focus import (
    ExpectationQuestion,
    FocusSelection,
    validate_expectation_questions,
)
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.product_sources import input_sources
from agenttest.project_intake import IntakeInput
from agenttest.report import ModelCallStats
from agenttest.request_requirements import (
    RequestRequirement,
    request_atoms,
    validate_requirement_links,
)
from agenttest.requirement_ledger import (
    data_reference_candidates,
    explicit_data_references,
    explicit_response_checks,
    resource_input_dependencies,
)


async def run_case(tmp_path, monkeypatch, *, permission=False, disposition='needs_review',
                   wording='随后用该响应中的 $.link.id 打开相应状态', anonymous=True,
                   bad_binding=False, selector='$.link.id', response_id=7, same_clause=False):
    document, setup = read_setup()
    if permission:
        parsed = json.loads(document)
        parsed['paths']['/status'] = parsed['paths'].pop('/status/{statusId}')
        parsed['paths']['/status']['get']['parameters'] = []
        document = json.dumps(parsed)
        setup.roles = [IntakeRole(alias='reviewer', bearer_env='AGENTTEST_TARGET_REVIEWER')]
        setup.operations[0].role = 'reviewer'
        setup.operations[1].arguments = []
        text = ('请确认 reviewer 访问状态接口时应当返回 403 还是 404；'
                + ('另外匿名访问同一个状态接口，核对 HTTP 200。' if anonymous else
                   '独立读取详情 detailId=2。'))
        if same_clause:
            text = '请确认 reviewer 访问状态接口的权限并匿名访问同一个状态接口。'
    else:
        setup.operations[1].arguments = [IntakeArgument(location='path_parameters', name='statusId',
            source_operation='detail', selector='$.link.id')]
        text = f'先读取详情 detailId=2 并核对 HTTP 200；{wording}。'
    sent, contexts = [], []

    class Focus:
        stats = ModelCallStats(calls=1, input_tokens=1, output_tokens=1, total_tokens=2)

        async def generate(self, context):
            requirements, inputs, questions = [], [], []
            for atom in context['request_atoms']:
                quote = atom['text']
                pending = permission and 'reviewer' in quote
                op = ('status' if pending or '状态' in quote or quote == '核对 HTTP 200'
                      else 'detail')
                values = clause(op, disposition=disposition if pending else 'required',
                    actions=[] if pending and not same_clause else [dict(operation_id=op,
                        role='reviewer' if permission and op == 'detail' else None)])
                if not permission and op == 'status':
                    values['same_resource'] = [dict(producer='detail', consumer='status')]
                requirements.append(dict(source_id=atom['source_id'], source_quote=quote,
                                         **values))
                if 'detailId=2' in quote:
                    inputs.append(dict(operation_id='detail', location='path_parameters',
                        name='detailId', path=[], value=2, source_quote=quote))
                if pending:
                    questions.append(dict(operation_id='status', kind='permission', role='reviewer',
                        status_choices=[403, 404], question='请确认 reviewer 的权限预期？',
                        requested_behavior='确认 reviewer 访问状态接口的权限', source_quote=quote))
            operations = ['status'] if permission and anonymous else ['detail', 'status']
            return FocusSelection(interpretation='保留原文待审身份及独立执行动作', mode='selected',
                operation_ids=operations, normal_operation_ids=operations, question=None,
                pending_questions=[], not_covered=[], requirements=requirements,
                input_requirements=inputs, expectation_questions=questions).model_dump_json()

    def designer(payload):
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        contexts.append(context)
        policies = {p['operation_id']: p for p in context['policies']}
        steps = []
        for op in context['primary_operations']:
            dynamic = not permission and op == 'status'
            arguments = [] if permission and op == 'status' else [dict(
                location='path_parameters', name='detailId' if op == 'detail' else 'statusId',
                value=None if dynamic else 2, from_step='detail' if dynamic else None,
                select=('$.wrong.id' if bad_binding else selector) if dynamic else None)]
            steps.append(dict(id=op, operation_id=op, policy_id=policies[op]['id'],
                role='reviewer' if permission and (op == 'detail' or bad_binding) else None,
                arguments=arguments))
        return response(dict(interpretation='按批准数据来源执行请求', questions=[], not_covered=[],
            cases=[dict(title='独立身份与来源', kind='flow',
                        plan=dict(name='reads', steps=steps))]))

    def target(request):
        sent.append((request.url.path, 'authorization' in request.headers))
        return httpx.Response(200, json=dict(name='approved', link=dict(id=response_id)))

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.environ['AGENTTEST_TARGET_REVIEWER'] = 'synthetic-fixture-only'
    svc.focus_factory = lambda _: Focus()
    try:
        saved = svc.intake.save(IntakeInput(name='C02 C03 确定性反例', document=document,
            generic=setup, confirmed=True), 'operator')
        row = await svc.submit('operator', saved['id'], text, uuid4().hex)
        return await finished(svc, row), sent, contexts, text
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('disposition', ['required', 'needs_review'])
@pytest.mark.parametrize('anonymous', [True, False])
async def test_c02_question_without_reviewer_action_preserves_independent_read(
        tmp_path, monkeypatch, disposition, anonymous):
    done, sent, _, _ = await run_case(tmp_path, monkeypatch, permission=True,
                                    disposition=disposition, anonymous=anonymous)
    assert done['status'] == 'FINISHED', done['summary']
    assert sent == ([('/status', False)] if anonymous else [('/detail/2', False)])
    summary = done['summary']
    assert not summary['complete'] and len(summary['questions']) == 1
    pending, = summary['permission_requirements']
    assert (pending['source_id'], pending['operation_id'], pending['role']) == (
        'request_1', 'status', 'reviewer')
    original = summary['focus']['requirements'][0]
    assert original['disposition'] == disposition and original['actions'] == []
    assert summary['permission_action_scope'] == dict(status=[None] if anonymous else [])


@pytest.mark.asyncio
async def test_c02_cannot_execute_reviewer_with_public_policy(tmp_path, monkeypatch):
    done, sent, _, _ = await run_case(tmp_path, monkeypatch, permission=True, bad_binding=True)
    assert not sent and not done['summary']['complete']
    assert any(r['status'] == 'REJECTED' for r in done['summary']['design_records'])


@pytest.mark.asyncio
@pytest.mark.parametrize('response_id', [7, 19])
@pytest.mark.parametrize('wording', [
    '随后用该响应中的 $.link.id 打开相应状态',
    '随后将该响应中的 $.link.id 交给状态接口作为入参',
    '依据该响应中的 $.link.id 获取对应状态',
])
async def test_c03_response_to_input_is_bound_to_actual_response(
        tmp_path, monkeypatch, wording, response_id):
    done, sent, _, text = await run_case(tmp_path, monkeypatch, wording=wording,
                                       response_id=response_id)
    assert done['status'] == 'FINISHED', done['summary']
    assert sent == [('/detail/2', False), (f'/status/{response_id}', False)]
    assert done['summary']['complete']
    assert not done['summary'].get('response_requirements')
    reference, = done['summary']['data_references']
    assert reference['status'] == 'EXECUTED'
    assert text[reference['start']:reference['end']] == '$.link.id'
    assert reference['witnesses'][0]['source_step_id'] == 'detail'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['selector', 'binding'])
async def test_c03_unknown_source_and_wrong_runtime_binding_are_held(tmp_path, monkeypatch, fault):
    wording = '随后用该响应中的 $.unknown.id 打开相应状态' if fault == 'selector' else (
        '随后用该响应中的 $.link.id 打开相应状态')
    done, sent, _, _ = await run_case(tmp_path, monkeypatch, wording=wording,
                                    bad_binding=fault == 'binding')
    assert not done['summary']['complete']
    assert sent == ([('/detail/2', False)] if fault == 'selector' else [])
    if fault == 'selector':
        entry, = done['summary']['data_references']
        assert entry['status'] == 'NEEDS_REVIEW' and entry['selector'] == '$.unknown.id'


def test_c02_missing_action_witness_is_only_exempt_for_sourced_permission_question():
    text = '请确认 reviewer 访问版本接口时应当返回 403 还是 404'
    context = ctx(text)
    context.update(approved_ids=['version'], resource_dependencies=[],
        managed_authentication=[dict(identity='reviewer', credential_status='READY')],
        permission_question_roles=dict(version=['reviewer']))
    requirement = RequestRequirement(source_id='request_1', source_quote=text,
        **clause('version', actions=[]))
    question = ExpectationQuestion(operation_id='version', kind='permission', role='reviewer',
        status_choices=[403, 404], source_quote=text, question='请确认访问权限？',
        requested_behavior='确认 reviewer 版本接口权限')
    chosen = FocusSelection(interpretation='保留权限问题的原文见证', mode='selected',
        operation_ids=['version'], question=None, pending_questions=[], not_covered=[],
        requirements=[requirement], expectation_questions=[question])
    validate_expectation_questions([question], context, ['version'], requirements=[requirement])
    validate_requirement_links([requirement], context, chosen)
    chosen.expectation_questions = []
    with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_WITNESS_MISSING'):
        validate_requirement_links([requirement], context, chosen)


def test_c03_unknown_predicate_does_not_disappear_as_data():
    text = '读取详情；核对响应 $.link.id 的业务含义'
    assert not explicit_data_references(text)
    check, = explicit_response_checks(text)
    assert check['operator'] is None


@pytest.mark.parametrize('disposition', ['required', 'needs_review'])
def test_c02_normal_wire_accepts_permission_inquiry_without_inventing_an_action(disposition):
    text = ('请确认 reviewer 访问版本接口时应当返回 403 还是 404；'
            '另外匿名访问同一个版本接口，核对 HTTP 200。')
    context = ctx(text)
    context.update(approved_ids=['version'], resource_dependencies=[],
        managed_authentication=[dict(identity='reviewer', credential_status='READY')],
        permission_question_roles=dict(version=['reviewer']),
        confirmed_response_checks=[dict(operation_id='version', roles=[None], statuses=[200])],
        grouped_index=[dict(operations=[dict(id='version', method='GET')])])
    source = next(s['source_id'] for s in input_sources(context)
                  if s['text'] == context['request_atoms'][0]['text'])
    raw = wire(dict(version=bundle(test_normal=True, expectation_questions=[dict(
        kind='permission', role='reviewer', status_choices=[403, 404], source_ref=source,
        question='请确认 reviewer 的访问权限？', requested_behavior='确认 reviewer 访问权限')])),
        dict(request_1=clause('version', actions=[], disposition=disposition),
             request_2=clause('version'), request_3=clause('version')))
    model = FocusModel(settings(), profile='generic')
    schema = json.loads(model.prepare_request(context))['response_format']['json_schema']['schema']
    Draft202012Validator(schema).validate(raw)
    chosen = json.loads(model._decode_output(json.dumps(raw), context))
    assert chosen['requirements'][0]['actions'] == []
    assert chosen['requirements'][1]['actions'] == [dict(operation_id='version', role=None)]
    assert chosen['expectation_questions'][0]['role'] == 'reviewer'


@pytest.mark.parametrize('fault', ['role', 'source', 'no_question', 'constraint',
                                  'missing_anonymous'])
def test_c02_permission_question_cannot_excuse_an_unsourced_or_missing_action(fault):
    text = '请确认 reviewer 访问版本接口时应当返回 403 还是 404'
    if fault == 'missing_anonymous':
        text += '并匿名访问版本接口'
    context = ctx(text)
    context.update(approved_ids=['version'], resource_dependencies=[],
        managed_authentication=[dict(identity='reviewer', credential_status='READY')],
        permission_question_roles=dict(version=['reviewer', 'owner']))
    item = RequestRequirement(source_id='request_1', source_quote=text,
        **clause('version', actions=[], disposition='constraint' if fault == 'constraint'
                 else 'required'))
    question = ExpectationQuestion(operation_id='version', kind='permission',
        role='owner' if fault == 'role' else 'reviewer', status_choices=[403, 404],
        source_quote='无来源的 reviewer 权限' if fault == 'source' else text,
        question='请确认访问权限？', requested_behavior='确认身份的访问权限')
    chosen = FocusSelection(interpretation='严格保留分项身份来源', mode='selected',
        operation_ids=['version'], question=None, pending_questions=[], not_covered=[],
        requirements=[item], expectation_questions=[] if fault == 'no_question' else [question])
    with pytest.raises(ValidationFailure):
        validate_expectation_questions(chosen.expectation_questions, context, ['version'],
                                       requirements=[item])
        validate_requirement_links([item], context, chosen)


def test_c03_original_array_selector_and_mixed_predicate_remain_separate():
    text = ('先取回 vetId=2 的兽医资料并核对 HTTP 200；'
            '随后用该响应中的 $.specialties[0].id 打开相应专科，验证 $.id 等于 1。')
    reference, = explicit_data_references(text)
    check, = explicit_response_checks(text)
    assert reference['selector'] == '$.specialties[0].id'
    assert check['selector'] == '$.id' and check['operator'] == 'equals' and check['expected'] == 1
    assert text[reference['start']:reference['end']] == reference['selector']


def test_c03_ambiguous_approved_consumer_slots_do_not_grant_a_binding():
    text = '读取详情；随后用该响应中的 $.link.id 打开状态'
    requirements = [RequestRequirement(source_id=a['source_id'], source_quote=a['text'],
        **clause('detail' if i == 0 else 'status')) for i, a in enumerate(request_atoms(text))]
    edge = dict(operation_id='status', from_operation='detail', selector='$.link.id',
        roles=[None], source_roles=[None], role_relation='anonymous', name='statusId')
    entry, = explicit_data_references(text)
    assert data_reference_candidates(entry, requirements, [edge])
    assert not data_reference_candidates(entry, requirements, [edge, dict(edge, name='otherId')])


@pytest.mark.parametrize('fault', ['source_review', 'consumer_review', 'source_provenance',
                                  'consumer_provenance', 'source_negative'])
def test_c03_dependency_catalog_requires_operator_approved_source_and_consumer(fault):
    from types import SimpleNamespace

    from agenttest.policies import BindingRule, PolicyRegistry, RegisteredPolicy

    policies = [RegisteredPolicy(id=op, operation_id=op, roles=[None], business_scenario='read',
        assertions=dict(reviewed=True, source='Operator-reviewed fixture',
                        status=dict(codes=[200])),
        binding_rules=[] if op == 'detail' else [BindingRule(location='path_parameters',
            name='statusId', source_policy_id='detail', source_selector='$.link.id',
            role_relation='anonymous')]) for op in ('detail', 'status')]
    project = SimpleNamespace(registry=PolicyRegistry(policies), contract=SimpleNamespace(
        operations=[SimpleNamespace(operation_id=op, method='GET') for op in ('detail', 'status')]))
    assert len(resource_input_dependencies(project)) == 1
    policy = policies[0 if fault.startswith('source') else 1]
    if fault.endswith('review'):
        policy.assertions.reviewed = False
    elif fault.endswith('provenance'):
        policy.assertions.source = ' '
    else:
        # The catalog must reject negative producer metadata, independently of registry loading.
        policy = policy.model_copy(update=dict(negative=object()))
        policies[0] = policy
    project.registry = SimpleNamespace(definitions=lambda: policies)
    assert not resource_input_dependencies(project)


@pytest.mark.parametrize('anaphor', ['', '同一个'])
@pytest.mark.parametrize('disposition', ['required', 'needs_review'])
def test_c02_same_clause_question_and_explicit_anonymous_action_keep_separate_identities(
        anaphor, disposition):
    from agenttest.product_focus import permission_review_scope

    text = f'请确认 reviewer 访问状态接口的权限并匿名访问{anaphor}状态接口'
    context = ctx(text)
    context.update(approved_ids=['status'], resource_dependencies=[],
        managed_authentication=[dict(identity='reviewer', credential_status='READY')],
        permission_question_roles=dict(status=['reviewer']),
        grouped_index=[dict(operations=[dict(id='status', method='GET')])])
    item = RequestRequirement(source_id='request_1', source_quote=text,
        **clause('status', disposition=disposition))
    question = ExpectationQuestion(operation_id='status', kind='permission', role='reviewer',
        status_choices=[403, 404], source_quote=text, question='请确认 reviewer 的访问权限？',
        requested_behavior='确认 reviewer 访问权限')
    chosen = FocusSelection(interpretation='分开权限问题与匿名动作', mode='selected',
        operation_ids=['status'], question=None, pending_questions=[], not_covered=[],
        requirements=[item], expectation_questions=[question])
    validate_expectation_questions([question], context, ['status'], requirements=[item])
    validate_requirement_links([item], context, chosen)
    blocked, planning, scope, pending = permission_review_scope(chosen)
    assert not blocked and scope == dict(status=[None])
    assert planning[0]['actions'] == [dict(operation_id='status', role=None)]
    assert planning[0]['disposition'] == 'required'
    assert chosen.requirements[0].disposition == disposition
    assert pending[0]['role'] == 'reviewer'


@pytest.mark.asyncio
@pytest.mark.parametrize('disposition', ['required', 'needs_review'])
async def test_c02_same_clause_pending_question_does_not_hold_its_anonymous_action(
        tmp_path, monkeypatch, disposition):
    done, sent, _, _ = await run_case(tmp_path, monkeypatch, permission=True, same_clause=True,
                                    disposition=disposition)
    assert done['status'] == 'FINISHED', done['summary']
    assert sent == [('/status', False)]
    summary = done['summary']
    assert not summary['complete'] and len(summary['questions']) == 1
    assert summary['focus']['requirements'][0]['disposition'] == disposition
    assert summary['planning_requirements'][0]['disposition'] == 'required'
    assert summary['permission_requirements'][0]['role'] == 'reviewer'


@pytest.mark.parametrize('explicit_anonymous', [False, True])
def test_c02_other_review_gaps_cannot_promote_an_anonymous_action(explicit_anonymous):
    from agenttest.product_focus import permission_review_scope

    quote = '请确认 reviewer 权限' + ('并匿名访问状态' if explicit_anonymous else '')
    permission = dict(operation_id='status', kind='permission', role='reviewer',
        status_choices=[403, 404], source_quote=quote, question='请确认权限预期？',
        requested_behavior='确认 reviewer 权限')
    questions = [permission]
    if explicit_anonymous:
        questions.append(dict(operation_id='status', kind='business_rule', role=None,
            status_choices=[], source_quote=quote, question='请确认未批准内容预期？',
            requested_behavior='确认内容预期'))
    chosen = FocusSelection(interpretation='未知预期仍须保持待审', mode='selected',
        operation_ids=['status'], question=None, pending_questions=[], not_covered=[],
        requirements=[dict(source_id='request_1', source_quote=quote,
            **clause('status', disposition='needs_review'))], expectation_questions=questions)
    blocked, planning, scope, _ = permission_review_scope(chosen)
    assert blocked == {'status'} and scope == dict(status=[])
    assert planning[0]['disposition'] == 'needs_review'
