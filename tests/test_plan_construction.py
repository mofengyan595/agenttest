"""The input encoding is unordered; construction happens once before execution gates."""

from copy import deepcopy

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.plan_construction import construct_design, construct_plan


def node(key, operation, role=None, source=None):
    return dict(id=key, operation_id=operation, role=role, policy_id=operation, arguments=[]
        if source is None else [dict(location='path_parameters', name='id', value=None,
                                    from_step=source, select='$.id')])


def context():
    return dict(policies=[dict(id=op, operation_id=op, roles=[None, 'owner'])
                          for op in ('login', 'create', 'read', 'delete', 'health')],
        authentication=[dict(role='owner', login_operation_id='login',
                             before_operation_ids=['create', 'read', 'delete'])])


def test_constructs_data_and_managed_identity_order_without_changing_business_values():
    raw = dict(name='flow', steps=[node('r', 'read', 'owner', source='c'),
                                   node('c', 'create', 'owner')])
    before = deepcopy(raw)
    plan, stats = construct_plan(raw, context())
    assert [s['operation_id'] for s in plan['steps']] == ['login', 'create', 'read']
    assert raw == before
    assert plan['steps'][-1] == raw['steps'][0]
    assert stats['managed_authentication_nodes'] == 1


@pytest.mark.parametrize('steps,code', [
    ([node('r', 'read', source='missing')], 'CONSTRUCTION_REFERENCE_INVALID'),
    ([node('x', 'read'), node('x', 'health')], 'CONSTRUCTION_DUPLICATE_NODE'),
    ([node('a', 'read', source='b'), node('b', 'create', source='a')],
     'CONSTRUCTION_DEPENDENCY_CYCLE'),
])
def test_missing_duplicate_and_cyclic_nodes_stop_without_inventing_business_steps(steps, code):
    with pytest.raises(ValidationFailure) as error:
        construct_plan(dict(name='invalid', steps=steps), context())
    assert error.value.diagnostic.code == code


def test_state_prerequisite_and_source_order_construct_before_first_validation():
    ctx = context()
    next(p for p in ctx['policies'] if p['id'] == 'read')['prerequisite_policy_ids'] = ['delete']
    plan, _ = construct_plan(dict(name='state', steps=[node('r', 'read'),
                                                     node('d', 'delete')]), ctx)
    assert [n['operation_id'] for n in plan['steps']] == ['delete', 'read']
    with pytest.raises(ValidationFailure, match='CONSTRUCTION_PREREQUISITE_AMBIGUOUS'):
        construct_plan(dict(name='missing', steps=[node('r', 'read')]), ctx)


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_single_approved_read_prerequisite_can_join_two_proposed_cases(method):
    ctx = context()
    ctx['operations'] = [dict(id='health', method=method), dict(id='read', method='GET')]
    next(p for p in ctx['policies'] if p['id'] == 'read')['prerequisite_policy_ids'] = ['health']
    wire = dict(cases=[dict(title='独立提议', kind='normal', plan=dict(name=op,
        steps=[node('check', op)])) for op in ('read', 'health')])
    if method == 'POST':
        with pytest.raises(ValidationFailure, match='CONSTRUCTION_PREREQUISITE_AMBIGUOUS'):
            construct_design(wire, ctx)
    else:
        records = construct_design(wire, ctx)
        case, = wire['cases']
        assert [step['operation_id'] for step in case['plan']['steps']] == ['health', 'read']
        assert records[0]['joined_read_cases'] == 2


def test_all_identities_finish_reading_before_cleanup_without_picking_one_predecessor():
    ctx = context()
    ctx['policies'][2]['roles'].append('other')
    ctx['request_requirements'] = [dict(disposition='required',
        before=[dict(earlier='read', later='delete')])]
    plan, _ = construct_plan(dict(name='both reads', steps=[node('d', 'delete'),
        node('one', 'read', 'owner'), node('two', 'read', 'other')]), ctx)
    operations = [step['operation_id'] for step in plan['steps']]
    assert operations.count('read') == 2 and operations[-1] == 'delete'


def test_independent_nodes_get_separate_contexts_and_each_prepares_its_identity():
    wire = dict(cases=[dict(title='两个独立检查', kind='normal', plan=dict(name='independent',
        steps=[node('a', 'read', 'owner'), node('b', 'delete', 'owner')]))])
    records = construct_design(wire, context())
    assert len(wire['cases']) == 2
    assert all(c['plan']['steps'][0]['operation_id'] == 'login' for c in wire['cases'])
    assert [[s['operation_id'] for s in c['plan']['steps'][1:]]
            for c in wire['cases']] == [['read'], ['delete']]
    assert records[0]['independent_contexts'] == 2


def test_same_resource_chain_is_never_split_or_bound_to_another_context():
    wire = dict(cases=[dict(title='同一资源链', kind='flow', plan=dict(name='flow', steps=[
        node('r', 'read', 'owner', source='c'), node('c', 'create', 'owner'),
        node('d', 'delete', 'owner', source='c')]))])
    records = construct_design(wire, context())
    assert len(wire['cases']) == records[0]['independent_contexts'] == 1
    assert [s['operation_id'] for s in wire['cases'][0]['plan']['steps']] == [
        'login', 'create', 'delete', 'read']  # Independent consumers have no inferred order.


def test_approved_cleanup_follows_consumers_but_preserves_explicit_post_delete_read():
    ctx = context()
    ctx['resource_lifecycles'] = [dict(create_operation='create', delete_operation='delete',
        path_parameter='id', id_selector='$.id')]
    raw = dict(name='cleanup', steps=[node('d', 'delete', source='c'),
        node('r', 'read', source='c'), node('c', 'create')])
    plan, _ = construct_plan(raw, ctx)
    assert [s['operation_id'] for s in plan['steps']] == ['create', 'read', 'delete']
    ctx['request_requirements'] = [dict(disposition='required',
        before=[dict(earlier='delete', later='read')])]
    plan, _ = construct_plan(raw, ctx)
    assert [s['operation_id'] for s in plan['steps']] == ['create', 'delete', 'read']


@pytest.mark.asyncio
async def test_independent_failed_assertion_does_not_suppress_another_actual_request(
        tmp_path, monkeypatch):
    import json

    import httpx
    from test_product_tasks import finished, response, service

    from agenttest.generic_intake import GenericSetup
    from agenttest.project_intake import IntakeInput

    document = json.dumps(dict(openapi='3.0.3', info=dict(title='Independent', version='1'),
        paths={f'/{op}': dict(get=dict(operationId=op, responses={
            '200': dict(description='ok')})) for op in ('first', 'second')}))
    setup = GenericSetup.model_validate(dict(base_url='http://127.0.0.1:18998',
        isolated_confirmed=True, assisted=True, operations=[dict(operation_id=op,
            alias='独立状态' + op, statuses=[200], checks=[dict(select='$.ok', expected=True)])
            for op in ('first', 'second')]))
    requests = []

    def target(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=dict(ok=request.url.path == '/second'))

    def model(request):
        steps = [dict(id=op, operation_id=op, role=None, policy_id=f'web_rule_{i+1}',
                      arguments=[]) for i, op in enumerate(('first', 'second'))]
        return response(dict(interpretation='检查两个独立的状态', cases=[dict(title='独立状态检查',
            kind='normal', plan=dict(name='independent', steps=steps))],
            questions=[], not_covered=[]))

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    try:
        saved = svc.intake.save(IntakeInput(name='独立检查', document=document,
            generic=setup, confirmed=True), 'operator')
        done = await finished(svc, await svc.submit('operator', saved['id'],
            '检查两个独立状态', 'independent-failure'))
        assert requests[0] == '/first' and requests[-1] == '/second'
        assert requests.count('/second') == 1  # The failed read may receive its approved probe.
        assert [c['outcome'] for c in done['summary']['cases']] == ['ASSERTION_FAILED', 'PASS']
        assert done['summary']['complete'] and not done['summary']['checks_passed']
    finally:
        await svc.close()


def test_cycle_reports_the_concrete_path_and_the_rule_behind_each_edge():
    raw = dict(name="cycle", steps=[node("a", "read", source="b"),
                                    node("b", "create", source="a")])
    with pytest.raises(ValidationFailure) as error:
        construct_plan(raw, context())
    assert error.value.diagnostic.code == "CONSTRUCTION_DEPENDENCY_CYCLE"
    assert set(error.value.diagnostic.fields) == {"plan.steps.0", "plan.steps.1"}
    detail = error.value.construction
    assert sorted(detail["cycle"]) == ["a", "b"]
    assert {edge["reason"] for edge in detail["edges"]} == {"MODEL_REFERENCE"}


def test_repeated_operation_order_pairs_instances_instead_of_chaining_every_node():
    ctx = context()
    ctx["request_requirements"] = [dict(disposition="required",
                                        before=[dict(earlier="read", later="read")])]
    repeated = dict(name="repeat", steps=[node("r1", "read", source="c"),
                                          node("c", "create"), node("r2", "read", source="c")])
    plan, stats = construct_plan(repeated, ctx)
    assert [step["id"] for step in plan["steps"]] == ["c", "r1", "r2"]
    assert stats["dependency_edges"] >= 2
    # A node-level chain in the opposite direction is still a real cycle.
    contradictory = dict(name="repeat", steps=[node("r2", "read", source="r1"),
                                               node("r1", "read", source="c"),
                                               node("c", "create")])
    with pytest.raises(ValidationFailure) as error:
        construct_plan(contradictory, ctx)
    assert error.value.diagnostic.code == "CONSTRUCTION_DEPENDENCY_CYCLE"


def body(name):
    return [dict(location="body", name="name", value=name, from_step=None, select=None)]


def test_repeated_create_instances_chain_in_proposal_order_without_a_self_edge():
    """A replay and a conflicting replay share one operation; each send is its own instance."""
    ctx = context()
    ctx["request_requirements"] = [dict(disposition="required",
                                        before=[dict(earlier="create", later="create")])]
    raw = dict(name="replay", steps=[
        dict(id="c1", operation_id="create", role="owner", policy_id="create",
             arguments=body("first")),
        dict(id="c2", operation_id="create", role="owner", policy_id="create",
             arguments=body("first")),
        dict(id="c3", operation_id="create", role="owner", policy_id="create",
             arguments=body("second"))])
    plan, stats = construct_plan(raw, ctx)
    order = [step["id"] for step in plan["steps"]]
    assert order.index("c1") < order.index("c2") < order.index("c3")
    assert stats["dependency_edges"] >= 2


def test_same_operation_by_two_identities_stays_two_distinct_instances():
    ctx = context()
    ctx["request_requirements"] = [dict(disposition="required",
                                        before=[dict(earlier="read", later="read")])]
    shared = [dict(location="path_parameters", name="id", value=None, from_step="c",
                   select="$.id")]
    raw = dict(name="identities", steps=[
        dict(id="c", operation_id="create", role="owner", policy_id="create",
             arguments=body("first")),
        dict(id="r1", operation_id="read", role="owner", policy_id="read", arguments=shared),
        dict(id="r2", operation_id="read", role=None, policy_id="read", arguments=shared)])
    plan, _ = construct_plan(raw, ctx)
    order = [step["id"] for step in plan["steps"]]
    assert order.index("c") < order.index("r1") < order.index("r2")
