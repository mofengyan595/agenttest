"""Offline reproductions of scope, data provenance and held-input planning failures."""

import json
from uuid import uuid4

import httpx
import pytest
from test_focus_references import clause
from test_mixed_planning_gaps import read_setup
from test_product_tasks import finished, response, service
from test_request_requirements import design, step

from agenttest.diagnostics import ValidationFailure
from agenttest.dsl import FieldAssertion
from agenttest.generic_intake import GenericSetup, IntakeArgument, IntakeRole
from agenttest.product_focus import FocusSelection, permission_review_scope
from agenttest.project_intake import IntakeInput
from agenttest.report import ModelCallStats
from agenttest.request_requirements import (
    RequestRequirement,
    request_atoms,
    validate_plan_requirements,
)
from agenttest.requirement_ledger import (
    data_reference_candidates,
    explicit_data_references,
    explicit_response_checks,
    validate_assignment_coverage,
    validate_data_witnesses,
)


async def run_mixed(tmp_path, monkeypatch, fault, *, extra='', bad_plan=False,
                    disposition='needs_review', data_selector='$.link.id', dual_use=False):
    document, setup = read_setup()
    sent, contexts = [], []
    if fault == 'permission':
        setup.roles = [IntakeRole(alias='reviewer', bearer_env='AGENTTEST_TARGET_REVIEWER')]
        setup.operations[0].role = 'reviewer'
        parsed = json.loads(document)
        parsed['paths']['/status'] = parsed['paths'].pop('/status/{statusId}')
        parsed['paths']['/status']['get']['parameters'] = []
        document = json.dumps(parsed)
        setup.operations[1].arguments = []
        text = ('reviewer 读取状态时应该返回 403 还是 404；'
                '独立读取详情 detailId=2。' + extra)
    elif fault == 'data':
        setup.operations[1].arguments = [IntakeArgument(location='path_parameters', name='statusId',
            source_operation='detail', selector='$.link.id')]
        text = ('先读取详情 detailId=2；' + ('验证 $.link.id 存在；' if dual_use else '')
                + f'再用这次响应的 {data_selector} 读取状态。' + extra)
        if dual_use:
            setup.operations[0].checks.append(FieldAssertion(select='$.link.id', operator='exists'))
    else:
        setup.operations[0].enabled = False
        text = '读取详情 detailId=2；独立读取状态 statusId=2。' + extra

    class Focus:
        stats = ModelCallStats(calls=1, input_tokens=1, output_tokens=1, total_tokens=2)

        async def generate(self, context):
            requirements, inputs, questions = [], [], []
            for atom in context['request_atoms']:
                source = atom['text']
                op = 'detail' if '详情' in source or source == '验证 $.link.id 存在' else 'status'
                role = 'reviewer' if fault == 'permission' and (
                    op == 'detail' or 'reviewer' in source) else None
                held = fault == 'blocked' and op == 'detail'
                values = clause(op, role, disposition=('needs_review' if held else
                    disposition if '403' in source else 'required'))
                if held:
                    values.update(actions=[], blocked_operation_ids=['detail'])
                if fault == 'data' and op == 'status':
                    values['same_resource'] = [dict(producer='detail', consumer='status')]
                requirements.append(dict(source_id=atom['source_id'], source_quote=source,
                                         **values))
                name = 'detailId' if op == 'detail' else 'statusId'
                if name + '=2' in source and not held:
                    inputs.append(dict(operation_id=op, location='path_parameters', name=name,
                        path=[], value=2, source_quote=source))
                if '403' in source:
                    questions.append(dict(operation_id='status', source_quote=source,
                        kind='permission', role='reviewer', status_choices=[403, 404],
                        question='reviewer 读取状态应该返回 403 还是 404？',
                        requested_behavior='确认 reviewer 对状态接口的权限预期'))
            return FocusSelection(interpretation='分别保留待审项与独立读取', mode='selected',
                operation_ids=['status'] if fault == 'blocked' else ['detail', 'status'],
                normal_operation_ids=['status'] if fault == 'blocked' else ['detail', 'status'],
                blocked_operation_ids=['detail'] if fault == 'blocked' else [],
                question=None, pending_questions=[], not_covered=[], requirements=requirements,
                input_requirements=inputs, expectation_questions=questions).model_dump_json()

    def designer(payload):
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        contexts.append(context)
        policies = {p['operation_id']: p for p in context['policies']}
        operations = list(context['primary_operations'])
        steps = []
        for op in operations:
            dynamic = fault == 'data' and op == 'status'
            steps.append(dict(id=op, operation_id=op,
                role='reviewer' if fault == 'permission' and (
                    op == 'detail' or bad_plan) else None,
                policy_id=policies[op]['id'], arguments=[dict(location='path_parameters',
                    name='detailId' if op == 'detail' else 'statusId',
                    value=None if dynamic else 2, from_step='detail' if dynamic else None,
                    select=('$.wrong.id' if bad_plan else '$.link.id') if dynamic else None)]))
            if fault == 'permission' and op == 'status':
                steps[-1]['arguments'] = []
        return response(dict(interpretation='按批准来源执行独立合法读取', questions=[],
            not_covered=[], cases=[dict(title='混合读取', kind='flow',
                                       plan=dict(name='reads', steps=steps))]))

    def target(payload):
        sent.append((payload.url.path, 'authorization' in payload.headers))
        return httpx.Response(200, json=dict(name='approved', link=dict(id=2)))

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.environ['AGENTTEST_TARGET_REVIEWER'] = 'synthetic-fixture-only'
    svc.focus_factory = lambda _: Focus()
    try:
        setup = GenericSetup.model_validate(setup.model_dump())
        saved = svc.intake.save(IntakeInput(name='混合需求离线反例', document=document,
                                           generic=setup, confirmed=True), 'operator')
        row = await svc.submit('operator', saved['id'], text, uuid4().hex)
        done = await finished(svc, row)
        return done, sent, contexts, text
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('disposition', ['needs_review', 'required'])
async def test_pending_permission_cannot_be_replaced_by_anonymous_policy(
        tmp_path, monkeypatch, disposition):
    done, sent, contexts, text = await run_mixed(tmp_path, monkeypatch, 'permission',
                                               disposition=disposition)
    assert done['status'] == 'FINISHED', done['summary']
    assert sent == [('/detail/2', False)]
    assert contexts[0]['primary_operations'] == ['detail']
    assert not done['summary']['complete'] and len(done['summary']['questions']) == 1
    hold, = done['summary']['permission_requirements']
    assert hold['operation_id'] == 'status' and hold['role'] == 'reviewer'
    assert hold['source_id'] == 'request_1' and hold['source_quote'] in text
    assert done['summary']['focus']['requirements'][0]['disposition'] == disposition


@pytest.mark.asyncio
async def test_separately_requested_anonymous_read_is_not_held(tmp_path, monkeypatch):
    done, sent, _, _ = await run_mixed(tmp_path, monkeypatch, 'permission',
                                      extra='另外匿名读取状态。')
    assert done['status'] == 'FINISHED', done['summary']
    assert sent == [('/detail/2', False), ('/status', False)]
    assert done['summary']['permission_action_scope'] == dict(status=[None])
    assert not done['summary']['complete']


@pytest.mark.asyncio
async def test_pending_role_is_not_repaired_into_the_public_policy(tmp_path, monkeypatch):
    done, sent, _, _ = await run_mixed(tmp_path, monkeypatch, 'permission',
        extra='另外匿名读取状态。', bad_plan=True)
    assert not sent and not done['summary']['complete']
    assert any(record['status'] == 'REJECTED' for record in done['summary']['design_records'])


@pytest.mark.asyncio
@pytest.mark.parametrize('dual_use', [False, True])
async def test_dynamic_response_reference_is_not_an_assertion(tmp_path, monkeypatch, dual_use):
    done, sent, _, text = await run_mixed(tmp_path, monkeypatch, 'data', dual_use=dual_use)
    assert done['status'] == 'FINISHED', done['summary']
    assert sent == [('/detail/2', False), ('/status/2', False)]
    assert done['summary']['complete']
    predicates = done['summary'].get('response_requirements', [])
    assert len(predicates) == int(dual_use)
    if dual_use:
        assert predicates[0]['status'] == 'PASS'
    reference, = done['summary']['data_references']
    assert reference['status'] == 'EXECUTED' and reference['selector'] == '$.link.id'
    assert text[reference['start']:reference['end']] == '$.link.id'
    assert reference['witnesses'][0]['source_step_id'] == 'detail'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unapproved_source', 'unapproved_check', 'bad_binding'])
async def test_unapproved_data_or_assertions_are_never_replaced(tmp_path, monkeypatch, fault):
    done, sent, _, _ = await run_mixed(tmp_path, monkeypatch, 'data',
        data_selector='$.unknown.id' if fault == 'unapproved_source' else '$.link.id',
        extra='；验证 $.name 等于 "pending"。' if fault == 'unapproved_check' else '',
        bad_plan=fault == 'bad_binding')
    assert not done['summary']['complete']
    assert sent == ([] if fault == 'bad_binding' else [('/detail/2', False)])
    if fault == 'unapproved_source':
        reference, = done['summary']['data_references']
        assert reference['status'] == 'NEEDS_REVIEW'
        assert reference['selector'] == '$.unknown.id'
        assert reference['blocked_operations'] == ['status']
    if fault == 'unapproved_check':
        assert done['summary']['response_requirements'][0]['status'] == 'NEEDS_REVIEW'


@pytest.mark.asyncio
async def test_unavailable_operation_keeps_input_pending_and_independent_read_runs(
        tmp_path, monkeypatch):
    done, sent, _, text = await run_mixed(tmp_path, monkeypatch, 'blocked')
    assert done['status'] == 'FINISHED', done['summary']
    assert sent == [('/status/2', False)]
    held = done['summary']['requirement_ledger'][0]
    assert held['status'] == 'NEEDS_REVIEW'
    assert held['name'] == 'detailId' and held['value'] == 2
    assert held['candidate_operations'] == ['detail']
    assert text[held['start']:held['end']] == 'detailId=2'
    assert held['source_id'] == 'request_1'
    assert not done['summary']['complete']


def test_data_reference_and_predicate_classification_is_per_occurrence():
    text = ('读取详情；验证 $.link.id 存在；再用这次响应的 $.link.id 读取状态；'
            '用这次响应的 $.link.id 读取状态并验证该字段存在；'
            '核对响应 $.unapproved 的业务含义。')
    refs, checks = explicit_data_references(text), explicit_response_checks(text)
    assert len(refs) == 2
    # A transferred value is a source even when the same clause later asserts that value.
    # The unnamed assertion keeps its own pending record instead of borrowing a selector.
    assert [(check['selector'], check['operator']) for check in checks] == [
        ('$.link.id', 'exists'), (None, None), ('$.unapproved', None)]
    assert checks[1]['reason'] == 'RESPONSE_ANAPHOR_UNRESOLVED'


@pytest.mark.parametrize('fault', ['source_op', 'source_role', 'use_role', 'selector', 'relation'])
def test_data_reference_does_not_borrow_another_producer_or_identity(fault):
    text = 'owner 读取详情；再用这次响应的 $.link.id 读取状态'
    requirements = [RequestRequirement(source_id=a['source_id'], source_quote=a['text'],
        **clause('detail' if i == 0 else 'status', 'owner' if i == 0 else 'reviewer'))
        for i, a in enumerate(request_atoms(text))]
    edge = dict(operation_id='status', from_operation='detail', selector='$.link.id',
                roles=['reviewer'], source_roles=['owner'], role_relation='different')
    assert data_reference_candidates(explicit_data_references(text)[0], requirements, [edge])
    key, value = dict(source_op=('from_operation', 'unrelated'),
        source_role=('source_roles', ['reviewer']), use_role=('roles', ['owner']),
        selector=('selector', '$.other.id'), relation=('role_relation', 'same'))[fault]
    edge[key] = value
    assert not data_reference_candidates(explicit_data_references(text)[0], requirements, [edge])


@pytest.mark.parametrize('fault', [
    'none', 'source', 'unavailable', 'slot', 'ambiguous', 'collision'])
def test_held_input_requires_exact_source_operation_and_unique_schema_slot(fault):
    text = '详情暂不可用 detailId=2；独立状态 statusId=2'
    atoms = request_atoms(text)
    chosen = FocusSelection(interpretation='保留输入并读取状态', mode='selected',
        operation_ids=['status'], blocked_operation_ids=['detail'], question=None,
        pending_questions=[], not_covered=[], requirements=[dict(
            source_id=a['source_id'], source_quote=a['text'], **clause('detail',
                disposition='needs_review', actions=[], blocked_operation_ids=['detail']))
            for a in atoms[:1]], input_requirements=[dict(operation_id='status',
                location='path_parameters', name='statusId', path=[], value=2,
                source_quote=atoms[1]['text'])])
    context = dict(user_task=text, unavailable_operations=[dict(operation_id='detail')],
        unavailable_input_fields=[dict(operation_id='detail', location='path_parameters',
                                       name='detailId')],
        input_fields=[dict(operation_id='status', name='statusId')])
    if fault == 'source':
        chosen.requirements[0].source_id = 'request_2'
        chosen.requirements[0].source_quote = atoms[1]['text']
    elif fault == 'unavailable':
        context['unavailable_operations'] = []
    elif fault == 'slot':
        context['unavailable_input_fields'][0]['name'] = 'otherId'
    elif fault == 'ambiguous':
        context['unavailable_input_fields'].append(dict(operation_id='detail',
            location='query', name='detailId'))
    elif fault == 'collision':
        context['input_fields'].append(dict(operation_id='status', name='detailId'))
    if fault in {'none', 'collision'}:
        ledger = validate_assignment_coverage(context, chosen)
        assert ledger[0]['status'] == 'NEEDS_REVIEW'
        assert ledger[0]['candidate_operations'] == ['detail']
        assert ledger[1]['status'] == 'ACCOUNTED'
    else:
        with pytest.raises(ValidationFailure, match='FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED'):
            validate_assignment_coverage(context, chosen)


@pytest.mark.parametrize('fault', ['none', 'source_role', 'consumer_role', 'selector', 'producer'])
def test_data_plan_witness_retains_exact_identity_and_binding(fault):
    proposal = design([step('producer', 'detail', role='owner', policy='detail_policy'),
        step('consumer', 'status', role='reviewer', source='producer', policy='status_policy')])
    argument = proposal.cases[0].plan.steps[1].arguments[0]
    argument.name, argument.select = 'statusId', '$.link.id'
    binding = dict(operation_id='status', policy_id='status_policy', role='reviewer',
        from_operation='detail', source_policy_id='detail_policy', source_role='owner',
        location='path_parameters', name='statusId')
    entry = dict(id='data_1', selector='$.link.id', approved_bindings=[binding])
    if fault == 'source_role':
        proposal.cases[0].plan.steps[0].role = 'other'
    elif fault == 'consumer_role':
        proposal.cases[0].plan.steps[1].role = 'owner'
    elif fault == 'selector':
        argument.select = '$.wrong.id'
    elif fault == 'producer':
        argument.from_step = 'unrelated'
    original = proposal.model_dump()
    if fault == 'none':
        validate_data_witnesses(proposal, ['status'], [entry])
        assert proposal.cases[0]._data_source_steps['data_1'][0]['source_step_id'] == 'producer'
    else:
        with pytest.raises(ValidationFailure, match='DESIGN_REQUIRED_DATA_SOURCE_MISSING'):
            validate_data_witnesses(proposal, ['status'], [entry])
    assert proposal.model_dump() == original


@pytest.mark.parametrize('anonymous', [False, True])
def test_permission_pending_source_cannot_add_another_actor(anonymous):
    quote = 'reviewer 读取状态时应该返回 403 还是 404' + ('并匿名读取状态' if anonymous else '')
    values = clause('status', 'reviewer', actions=[dict(operation_id='status', role=role)
        for role in ('reviewer', None, 'owner')])
    chosen = FocusSelection(interpretation='权限待审并保留独立动作', mode='selected',
        operation_ids=['status'], question=None, pending_questions=[], not_covered=[],
        requirements=[dict(source_id='request_1', source_quote=quote, **values)],
        expectation_questions=[dict(operation_id='status', kind='permission', role='reviewer',
            status_choices=[403, 404], source_quote=quote, question='请确认权限预期',
            requested_behavior='确认 reviewer 权限预期')])
    blocked, planning, scope, holds = permission_review_scope(chosen)
    assert scope == dict(status=[None] if anonymous else [])
    assert blocked == (set() if anonymous else {'status'})
    assert holds[0]['source_id'] == 'request_1' and holds[0]['role'] == 'reviewer'
    assert [a['role'] for a in planning[0]['actions']] == ([None] if anonymous else [])
    assert len(chosen.requirements[0].actions) == 3


@pytest.mark.asyncio
async def test_permission_scope_rejects_a_model_that_ignores_generation_constraints():
    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import build_generic_project
    from agenttest.product_design import design_groups, propose_design

    document, setup = read_setup()
    project = build_generic_project('scope', document, load_openapi(document), setup)

    class UntrustedDesign:
        async def generate(self, context):
            # Anonymous is approved globally, but this sourced pending scope allows no reader.
            assert not any(p['operation_id'] == 'status' for p in context['policies'])
            proposal = design([step('s', 'status', role=None, policy='web_rule_2')])
            return proposal.model_dump_json()

    with pytest.raises(ValidationFailure, match='DESIGN_PERMISSION_SCOPE_NOT_REQUESTED'):
        await propose_design(project, '确认身份权限预期', design_groups(project)[0],
            UntrustedDesign(), environ={}, remaining_requests=5, inventory_total=2,
            permission_action_scope=dict(status=[]))


@pytest.mark.parametrize('fault', ['none', 'missing_read', 'wrong_order', 'wrong_role'])
def test_permission_hold_preserves_independent_repetition_and_order(fault):
    text = ('reviewer 读取状态时应该返回 403 还是 404；'
            '连续读取两次状态（匿名）；然后读取详情。')
    atoms = request_atoms(text)
    chosen = FocusSelection(interpretation='保留待审权限与明确读取顺序', mode='selected',
        operation_ids=['status', 'detail'], question=None, pending_questions=[], not_covered=[],
        requirements=[dict(source_id=a['source_id'], source_quote=a['text'],
            **clause('detail' if i == 2 else 'status', 'reviewer' if i == 0 else None,
                before=[dict(earlier='status', later='detail')] if i == 2 else []))
            for i, a in enumerate(atoms)], expectation_questions=[dict(operation_id='status',
                kind='permission', role='reviewer', status_choices=[403, 404],
                source_quote=atoms[0]['text'], question='请确认权限预期',
                requested_behavior='确认 reviewer 权限预期')])
    _, planning, _, _ = permission_review_scope(chosen)
    first, second, final = (step('s1', 'status', None), step('s2', 'status', None),
                            step('d', 'detail', None))
    steps = [first, second, final]
    if fault == 'missing_read':
        steps.remove(second)
    elif fault == 'wrong_order':
        steps = [first, final, second]
    elif fault == 'wrong_role':
        second['role'] = 'reviewer'
    requirements = [RequestRequirement.model_validate(r) for r in planning]
    if fault == 'none':
        validate_plan_requirements(design(steps), requirements, ['status', 'detail'],
                                   read_operations={'status', 'detail'})
    else:
        with pytest.raises(ValidationFailure):
            validate_plan_requirements(design(steps), requirements, ['status', 'detail'],
                                       read_operations={'status', 'detail'})
