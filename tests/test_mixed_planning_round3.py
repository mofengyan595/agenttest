"""Round-3 counterexamples: data sources, response assertions and permission witnesses.

Every case here is deterministic: no network, no external target and no model. The
fixture mirrors the round-3 loopback project (indexes, entries, receipts, health, build,
account, tags, pages) so the same original clauses can be replayed offline.
"""

import json
from uuid import uuid4

import httpx
import pytest
from test_product_design import settings
from test_product_tasks import finished, response, service

from agenttest.diagnostics import ValidationFailure
from agenttest.generic_intake import GenericSetup, IntakeOperation, IntakeRole
from agenttest.product_focus import (
    ExpectationQuestion,
    permission_review_scope,
)
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.product_sources import input_sources
from agenttest.project_intake import IntakeInput
from agenttest.request_requirements import (
    RequestRequirement,
    permission_inquiry,
    request_atoms,
)
from agenttest.requirement_ledger import (
    explicit_data_references,
    explicit_response_checks,
)

ROLES = ('owner', 'reader', 'archivist', 'reviewer')
RESPONSES = {
    '/indexes/41': {'id': 41, 'links': [{'id': 73, 'code': 'R73'}, {'id': 88, 'code': 'R88'}]},
    '/entries/73': {'id': 73, 'label': 'ready-for-review'},
    '/receipts/R73': {'code': 'R73'},
    '/health': {'status': 'ok'},
    '/build': {'build': 'fixture-r1'},
    '/account': {'alias': 'archive-reader'},
    '/tags': {'tags': ['fixture']},
    '/pages': {'page': 3},
}


def document():
    def read(path, operation, summary, parameters=(), query=False):
        return {path: {'get': dict(operationId=operation, summary=summary,
            parameters=list(parameters), responses={'200': dict(description='ok')})}}

    paths = {}
    for part in (
            read('/indexes/{indexId}', 'readIndex', '索引', [
                dict(name='indexId', **{'in': 'path'}, required=True,
                     schema=dict(type='integer'))]),
            read('/entries/{entryId}', 'readEntry', '条目', [
                dict(name='entryId', **{'in': 'path'}, required=True,
                     schema=dict(type='integer'))]),
            read('/receipts/{code}', 'readReceipt', '回执', [
                dict(name='code', **{'in': 'path'}, required=True, schema=dict(type='string'))]),
            read('/health', 'readHealth', '健康状态'),
            read('/build', 'readBuild', '构建信息'),
            read('/account', 'readAccount', '当前账号'),
            read('/tags', 'readTags', '标签'),
            read('/pages', 'readPage', '分页结果', [
                dict(name='page', **{'in': 'query'}, schema=dict(type='integer',
                                                                 minimum=1, maximum=9))]),
    ):
        paths.update(part)
    return json.dumps(dict(openapi='3.0.3', info=dict(title='Round three reads', version='1'),
                           paths=paths))


def setup():
    def declared(*checks):
        return dict(statuses=[200], checks=[dict(select=select, operator=operator,
            expected=expected) for select, operator, expected in checks],
            check_source='Operator-confirmed immutable read-only fixture.')

    return GenericSetup(base_url='http://127.0.0.1:18997', isolated_confirmed=True,
        assisted=True, roles=[IntakeRole(alias=role,
            bearer_env=f'AGENTTEST_TARGET_{role.upper()}_TOKEN') for role in ROLES],
        operations=[
            IntakeOperation(operation_id='readIndex', alias='索引', role='owner',
                arguments=[dict(location='path_parameters', name='indexId', value=41)],
                **declared(('$.links[0].id', 'exists', None))),
            IntakeOperation(operation_id='readEntry', alias='条目', role='reader',
                arguments=[dict(location='path_parameters', name='entryId',
                    source_operation='readIndex', selector='$.links[0].id')],
                **declared(('$.id', 'equals', 73))),
            IntakeOperation(operation_id='readReceipt', alias='回执', role='owner',
                arguments=[dict(location='path_parameters', name='code',
                    source_operation='readIndex', selector='$.links[0].code')],
                **declared(('$.code', 'equals', 'R73'))),
            IntakeOperation(operation_id='readHealth', alias='健康状态', role=None,
                arguments=[], **declared()),
            IntakeOperation(operation_id='readBuild', alias='构建信息', role=None,
                arguments=[], **declared(('$.build', 'equals', 'fixture-r1'))),
            IntakeOperation(operation_id='readAccount', alias='当前账号', role='archivist',
                arguments=[], **declared(('$.alias', 'equals', 'archive-reader'))),
            IntakeOperation(operation_id='readTags', alias='标签', role=None,
                arguments=[], **declared()),
            IntakeOperation(operation_id='readPage', alias='分页结果', role=None,
                arguments=[dict(location='query', name='page', value=3)], **declared()),
        ])


def bundle(**overrides):
    return dict(dict(input_requirements=[], input_questions=[], requested_checks=[],
                     expectation_questions=[], test_normal=False), **overrides)


def clause(operation, role=None, **overrides):
    return dict(dict(disposition='required', actions=[dict(operation_id=operation, role=role)],
                     before=[], same_resource=[], response_checks=[],
                     forbidden_operation_ids=[], blocked_operation_ids=[]), **overrides)


def ref_of(context, needle):
    """The exact clause source the wire offers; the whole task is only a fallback."""
    entries = [entry for entry in input_sources(context) if needle in entry['text']]
    clauses = [entry for entry in entries if entry['origin'] == 'current_clause']
    return (clauses or entries)[0]['source_id']


def declared(field, value, source):
    """An explicit clause value must be declared, not inferred from a policy default."""
    return dict(field_ref=field, value=value, source_ref=source)


def wire(operations, requirements, *, interpretation='按原文和已确认配置核对'):
    return dict(interpretation=interpretation, blocked_operations={},
                scope=dict(mode='selected', operations=operations, question=None),
                requirement_map=requirements)


def close(context, reply):
    """The wire must decide every approved operation and every inventory flag."""
    reply['scope']['operations'] = {
        key: (None if bundle_ is None else {**bundle_, 'existing_resource': False})
        for key, bundle_ in {key: reply['scope']['operations'].get(key)
                             for key in context['approved_ids']}.items()}
    reply['blocked_operations'] = {operation['operation_id']: False
                                   for operation in context['unavailable_operations']}
    return reply


def expectation(context, operation, select):
    return next(reference for reference, rule in context['response_expectations'].items()
                if rule['operation_id'] == operation and rule['select'] == select)


def policy_id(context, operation, role):
    return next(policy['id'] for policy in context['policies']
                if policy['operation_id'] == operation and role in policy['roles'])


def step(context, key, operation, role, arguments=()):
    return dict(id=key, operation_id=operation, role=role,
                policy_id=policy_id(context, operation, role),
                arguments=[dict(location=location, name=name, value=value,
                                from_step=source, select=select)
                           for location, name, value, source, select in arguments])


def case(title, steps):
    return dict(title=title, kind='normal', plan=dict(name=title, steps=steps))


async def run_task(tmp_path, monkeypatch, text, focus_reply, design_reply):
    sent, seen = [], {}

    def target(request):
        sent.append(request.url.path)
        return httpx.Response(200, json=RESPONSES.get(request.url.path, {}))

    def focus(payload):
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        seen['focus'] = context
        return response(close(context, focus_reply(context)))

    def designer(payload):
        context = json.loads(json.loads(payload.content)['messages'][1]['content'])
        seen['design'] = context
        return response(design_reply(context))

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(),
        transport=httpx.MockTransport(focus), profile='generic')
    svc.environ.update({f'AGENTTEST_TARGET_{role.upper()}_TOKEN': f'synthetic-{role}-token'
                        for role in ROLES})
    saved = svc.intake.save(IntakeInput(name='第3轮离线反例', document=document(),
                                       generic=setup(), confirmed=True), 'operator')
    row = await svc.submit('operator', saved['id'], text, str(uuid4()))
    return await finished(svc, row), sent, seen


def assert_finished(done):
    """Surface the closed diagnostics instead of a 60-key summary diff."""
    focus = next((record for record in done['summary'].get('design_records', [])
                  if record.get('group_id') == 'focus'), {})
    detail = dict(status=done['status'], reason=done['summary'].get('reason'),
                  focus=(focus.get('status'), focus.get('reason'), focus.get('diagnostic')),
                  records=[(record.get('status'), record.get('reason'),
                            (record.get('diagnostic') or {}).get('code'),
                            (record.get('diagnostic') or {}).get('fields'))
                           for record in done['summary'].get('design_records', [])],
                  groups=[(group.get('id'), group.get('status'), group.get('reason'),
                           (group.get('record') or {}).get('reason'))
                          for group in done['summary'].get('groups', [])],
                  blocked=done['summary'].get('input_blocked_operations'),
                  questions=[(q.get('operation_id'), q.get('role'), q.get('kind'))
                             for q in done['summary'].get('questions', [])])
    if done['status'] != 'FINISHED':
        print('DETAIL ' + json.dumps(detail, ensure_ascii=False))
    assert done['status'] == 'FINISHED', detail

# --- P0-1: a response value source is not a response assertion -----------------------

D01_TEXT = ('owner 查询 indexId=41 的索引；将它响应里的 $.links[0].id 交给 reader 打开条目；'
            '验证 $.id 等于 73；最后匿名读取健康状态')
D02_TEXT = ('owner 先读取 indexId=41 的索引；依据该响应中的 $.links[0].code 获取对应回执；'
            '核对回执 $.code 等于 "R73" 后匿名读取健康状态')
D03_TEXT = ('owner 获取 indexId=41 的索引；用这份响应中的 $.links[0].id 由 reader 访问条目'
            '并确认 $.label 等于 "ready-for-review"；另匿名读取健康状态')
D04_TEXT = ('owner 读取 indexId=41 的索引；随后用响应中的 $.links[1].id 让 reader 打开条目；'
            '不依赖这条链路的健康读取仍要执行')


@pytest.mark.parametrize('text', [D01_TEXT, D04_TEXT, D03_TEXT])
def test_response_anaphora_is_a_data_source_not_an_assertion(text):
    references = explicit_data_references(text)
    assert [entry['selector'] for entry in references] == [
        '$.links[1].id' if 'links[1]' in text else '$.links[0].id']
    # No assertion may be attributed to the transferred selector itself.
    assert not [entry for entry in explicit_response_checks(text)
                if entry['selector'] == references[0]['selector']]


def test_response_predicate_survives_a_following_independent_action():
    checks = explicit_response_checks(D02_TEXT)
    assert [(entry['selector'], entry['operator'], entry['expected']) for entry in checks] == [
        ('$.code', 'equals', 'R73')]
    assert checks[0]['continues'] is True


def test_content_assertion_after_a_data_source_stays_its_own_check():
    references, checks = explicit_data_references(D03_TEXT), explicit_response_checks(D03_TEXT)
    assert [entry['selector'] for entry in references] == ['$.links[0].id']
    assert [(entry['selector'], entry['operator']) for entry in checks] == [
        ('$.label', 'equals')]


def test_assertion_on_the_transferred_value_has_no_borrowed_selector():
    text = '读取详情；再用这次响应的 $.link.id 读取状态并验证该字段存在。'
    references, checks = explicit_data_references(text), explicit_response_checks(text)
    assert len(references) == 1
    assert [(entry['selector'], entry['operator']) for entry in checks] == [(None, None)]
    assert checks[0]['reason'] == 'RESPONSE_ANAPHOR_UNRESOLVED'


@pytest.mark.asyncio
async def test_requested_data_source_flow_executes_its_binding(tmp_path, monkeypatch):
    def focus(context):
        references = explicit_data_references(D01_TEXT)
        assert [entry['selector'] for entry in references] == ['$.links[0].id']
        return wire(dict(
            readIndex=bundle(test_normal=True, input_requirements=[
                declared('path_parameters/indexId', 41, ref_of(context, 'indexId=41'))]),
            readEntry=bundle(test_normal=True, existing_resource=False),
            readHealth=bundle(test_normal=True)), {
                'request_1': clause('readIndex', 'owner', response_checks=[
                    expectation(context, 'readIndex', '$.links[0].id')]),
                'request_2': clause('readEntry', 'reader',
                    same_resource=[dict(producer='readIndex', consumer='readEntry')]),
                'request_3': clause('readEntry', 'reader',
                    same_resource=[dict(producer='readIndex', consumer='readEntry')],
                    response_checks=[expectation(context, 'readEntry', '$.id')]),
                'request_4': clause('readHealth', None, before=[
                    dict(earlier='readEntry', later='readHealth')]),
            })

    def designer(context):
        return dict(interpretation='索引后打开条目并读取健康状态', relevant=True,
            questions=[], not_covered=[], cases=[case('索引到条目流程', [
                step(context, 'index', 'readIndex', 'owner', [
                    ('path_parameters', 'indexId', 41, None, None)]),
                step(context, 'entry', 'readEntry', 'reader', [
                    ('path_parameters', 'entryId', None, 'index', '$.links[0].id')]),
                step(context, 'health', 'readHealth', None)])])

    done, sent, seen = await run_task(tmp_path, monkeypatch, D01_TEXT, focus, designer)
    assert_finished(done)
    assert sent == ['/indexes/41', '/entries/73', '/health']
    assert not done['summary'].get('source_blocked_operations')
    assert not done['summary'].get('input_blocked_operations')
    data, = done['summary']['data_references']
    assert data['status'] == 'EXECUTED' and data['approved_bindings']
    check, = done['summary']['response_requirements']
    assert (check['selector'], check['status']) == ('$.id', 'PASS')


@pytest.mark.asyncio
async def test_approved_predicate_and_independent_clause_action_both_execute(tmp_path, monkeypatch):
    def focus(context):
        references = explicit_data_references(D02_TEXT)
        assert [entry['selector'] for entry in references] == ['$.links[0].code']
        return wire(dict(
            readIndex=bundle(test_normal=True, input_requirements=[
                declared('path_parameters/indexId', 41, ref_of(context, 'indexId=41'))]),
            readReceipt=bundle(test_normal=True, existing_resource=False),
            readHealth=bundle(test_normal=True)), {
                'request_1': clause('readIndex', 'owner', response_checks=[
                    expectation(context, 'readIndex', '$.links[0].id')]),
                'request_2': clause('readReceipt', 'owner',
                    before=[dict(earlier='readIndex', later='readReceipt')],
                    same_resource=[dict(producer='readIndex', consumer='readReceipt')]),
                'request_3': clause('readReceipt', 'owner',
                    before=[dict(earlier='readReceipt', later='readHealth')],
                    actions=[dict(operation_id='readReceipt', role='owner'),
                             dict(operation_id='readHealth', role=None)],
                    response_checks=[expectation(context, 'readReceipt', '$.code')]),
            })

    def designer(context):
        return dict(interpretation='索引后取回执并读取健康状态', relevant=True,
            questions=[], not_covered=[], cases=[case('索引到回执流程', [
                step(context, 'index', 'readIndex', 'owner', [
                    ('path_parameters', 'indexId', 41, None, None)]),
                step(context, 'receipt', 'readReceipt', 'owner', [
                    ('path_parameters', 'code', None, 'index', '$.links[0].code')]),
                step(context, 'health', 'readHealth', None)])])

    done, sent, seen = await run_task(tmp_path, monkeypatch, D02_TEXT, focus, designer)
    assert_finished(done)
    assert sent == ['/indexes/41', '/receipts/R73', '/health']
    assert not done['summary'].get('input_blocked_operations')
    check, = done['summary']['response_requirements']
    assert check['status'] == 'PASS'
    assert check['approved_action'] == dict(operation_id='readReceipt', role='owner')
    assert [witness['step_id'] for witness in check['witnesses']] == ['receipt']


@pytest.mark.asyncio
async def test_unapproved_content_assertion_still_holds_its_read(tmp_path, monkeypatch):
    def focus(context):
        return wire(dict(
            readIndex=bundle(test_normal=True, input_requirements=[
                declared('path_parameters/indexId', 41, ref_of(context, 'indexId=41'))]),
            readEntry=bundle(test_normal=True, existing_resource=False,
                expectation_questions=[dict(kind='business_rule', role=None,
                    status_choices=[], source_ref=ref_of(context, 'ready-for-review'),
                    question='条目返回的 $.label 是否应等于 ready-for-review？',
                    requested_behavior='确认条目内容规则')]),
            readHealth=bundle(test_normal=True)), {
                'request_1': clause('readIndex', 'owner', response_checks=[
                    expectation(context, 'readIndex', '$.links[0].id')]),
                'request_2': clause('readEntry', 'reader', disposition='needs_review',
                    before=[dict(earlier='readIndex', later='readEntry')],
                    same_resource=[dict(producer='readIndex', consumer='readEntry')]),
                'request_3': clause('readHealth', None),
            })

    def designer(context):
        return dict(interpretation='只执行已批准的索引与健康读取', relevant=True,
            questions=[], not_covered=[], cases=[case('索引与健康读取', [
                step(context, 'index', 'readIndex', 'owner', [
                    ('path_parameters', 'indexId', 41, None, None)]),
                step(context, 'health', 'readHealth', None)])])

    done, sent, seen = await run_task(tmp_path, monkeypatch, D03_TEXT, focus, designer)
    assert_finished(done)
    assert sorted(sent) == ['/health', '/indexes/41']
    assert 'readEntry' in done['summary']['input_blocked_operations']
    held, = done['summary']['response_requirements']
    assert held['selector'] == '$.label' and held['status'] == 'NEEDS_REVIEW'
    assert held['blocked_operations'] == ['readEntry']
    assert not done['summary']['complete']


# --- P0-2/P0-3: permission inquiries keep their own sourced witness -------------------


def test_permission_inquiry_wording_covers_refusal_rules():
    assert permission_inquiry('先确认 reviewer 查看构建信息的拒绝规则')
    assert permission_inquiry('本轮还要确认 reviewer 对构建接口应返回 403 还是 404')
    assert permission_inquiry('分别确认 reviewer 对构建接口和账号接口的权限规则')
    assert not permission_inquiry('匿名读取分页结果，再读取标签')


def context_for(text, roles=None):
    """A miniature focus context: trusted labels plus question roles."""
    return dict(user_task=text, request_atoms=request_atoms(text),
        grouped_index=[dict(group_id='group_1', operations=[
            dict(id=operation, method='GET', path=f'/{operation}', summary=summary,
                 parameters=[])
            for operation, summary in (('readBuild', '构建信息'), ('readAccount', '当前账号'),
                                       ('readIndex', '索引'), ('readEntry', '条目'),
                                       ('readTags', '标签'), ('readPage', '分页结果'))])],
        permission_question_roles=roles if roles is not None else {
            'readBuild': ['archivist', 'reviewer', 'owner', 'reader'],
            'readAccount': ['reviewer', 'owner', 'reader'],
            'readTags': ['archivist', 'reviewer', 'owner', 'reader'],
            'readPage': ['archivist', 'reviewer', 'owner', 'reader']})


def test_permission_inquiry_targets_come_from_trusted_labels():
    from agenttest.request_requirements import permission_inquiry_operations

    assert permission_inquiry_operations('本轮还要确认 reviewer 对构建接口应返回 403 还是 404',
        context_for('')) == ['readBuild']
    assert permission_inquiry_operations('另确认 reviewer 的构建访问权限',
        context_for('')) == ['readBuild']
    assert permission_inquiry_operations('分别确认 reviewer 对构建接口和账号接口的权限规则',
        context_for('')) == ['readAccount', 'readBuild']
    # An unnamed "it" reference names no operation, and a shared label names none either.
    assert permission_inquiry_operations('别为确认权限再访问它', context_for('')) == []
    shared = context_for('', roles={'readBuild': ['reviewer'], 'readAccount': ['reviewer']})
    shared['grouped_index'][0]['operations'][1]['summary'] = '构建账号'
    assert permission_inquiry_operations('确认 reviewer 的构建账号权限', shared) == [
        'readAccount']
    twin = context_for('', roles={'readBuild': ['reviewer'], 'readAccount': ['reviewer']})
    twin['grouped_index'][0]['operations'][1]['summary'] = '构建信息'
    assert permission_inquiry_operations('确认 reviewer 的构建信息权限', twin) == []


def test_named_role_requires_the_exact_alias():
    from agenttest.request_requirements import named_role

    assert named_role('确认 reviewer 的构建权限', 'reviewer')
    assert not named_role('确认 reviewers 的构建权限', 'reviewer')


@pytest.mark.asyncio
async def test_permission_inquiry_without_a_model_question_keeps_its_own_witness(
        tmp_path, monkeypatch):
    text = ('匿名查一次构建信息后；让 archivist 读取账号；'
            '本轮还要确认 reviewer 对构建接口应返回 403 还是 404；别为确认权限再访问它')

    def focus(context):
        return wire(dict(
            readBuild=bundle(test_normal=True),
            readAccount=bundle(test_normal=True)), {
                'request_1': clause('readBuild', None),
                'request_2': clause('readAccount', 'archivist'),
                'request_3': clause(None, None, disposition='needs_review', actions=[]),
                'request_4': clause('readBuild', 'reviewer'),
            })

    def designer(context):
        assert not any(s['operation_id'] == 'readBuild' and s['role'] == 'reviewer'
                       for case_ in context['required_cases']
                       for s in case_['before'] + case_['after']) if context.get(
                           'required_cases') else True
        return dict(interpretation='匿名读取构建信息后由归档身份读取账号', relevant=True,
            questions=[], not_covered=[], cases=[case('构建与账号读取', [
                step(context, 'build', 'readBuild', None),
                step(context, 'account', 'readAccount', 'archivist')])])

    done, sent, seen = await run_task(tmp_path, monkeypatch, text, focus, designer)
    assert_finished(done)
    assert sorted(sent) == ['/account', '/build']
    questions = done['summary']['questions']
    assert [(q['operation_id'], q['role'], q['kind']) for q in questions] == [
        ('readBuild', 'reviewer', 'permission')]
    assert questions[0]['status_choices'] == [403, 404]
    assert questions[0]['requested_behavior'] in text or questions[0]['id']
    asked, = done['summary']['focus']['expectation_questions']
    assert asked['operation_id'] == 'readBuild' and asked['role'] == 'reviewer'
    # The pending identity is never executed, and its action is not part of the plan.
    assert done['summary']['permission_action_scope'] == {'readBuild': [None]}
    held = {entry['operation_id'] for entry in done['summary']['permission_requirements']}
    assert held == {'readBuild'}
    assert not done['summary']['complete']


@pytest.mark.asyncio
async def test_declared_and_derived_permission_questions_are_both_kept(tmp_path, monkeypatch):
    text = ('分别确认 reviewer 对构建接口和账号接口的权限规则；'
            '本轮实际只匿名读取构建接口并验证 $.build 等于 "fixture-r1"')

    def focus(context):
        return wire(dict(
            readBuild=bundle(test_normal=True, expectation_questions=[dict(
                kind='permission', role='reviewer', status_choices=[403, 404],
                source_ref=ref_of(context, '权限规则'),
                question='reviewer 访问构建接口的权限结果是什么？',
                requested_behavior='确认 reviewer 构建接口权限')]),
        ), {
                'request_1': clause(None, None, disposition='needs_review', actions=[]),
                'request_2': clause('readBuild', None, response_checks=[
                    expectation(context, 'readBuild', '$.build')]),
            })

    def designer(context):
        return dict(interpretation='匿名读取构建信息并核对已批准字段', relevant=True,
            questions=[], not_covered=[], cases=[case('构建读取', [
                step(context, 'build', 'readBuild', None)])])

    done, sent, seen = await run_task(tmp_path, monkeypatch, text, focus, designer)
    assert_finished(done)
    assert sent == ['/build']
    pairs = [(q['operation_id'], q['role']) for q in
             done['summary']['focus']['expectation_questions']]
    assert sorted(pairs) == [('readAccount', 'reviewer'), ('readBuild', 'reviewer')]
    assert sorted((q['operation_id'], q['role']) for q in done['summary']['questions']) == [
        ('readAccount', 'reviewer'), ('readBuild', 'reviewer')]
    assert 'readAccount' in done['summary']['input_blocked_operations']
    assert not done['summary']['complete']


@pytest.mark.asyncio
async def test_pending_permission_does_not_block_independent_legal_reads(tmp_path, monkeypatch):
    text = ('reviewer 的构建访问权限尚待确认；本轮不要求读取构建信息；'
            '匿名读取 page=3 的分页结果；再读取标签')

    def focus(context):
        return wire(dict(
            readPage=bundle(test_normal=True, input_requirements=[
                declared('query/page', 3, ref_of(context, 'page=3'))]),
            readTags=bundle(test_normal=True), expectation_questions=None), {
                'request_1': clause(None, None, disposition='needs_review', actions=[]),
                'request_2': clause(None, None, disposition='constraint', actions=[],
                                    forbidden_operation_ids=['readBuild']),
                'request_3': clause('readPage', None),
                'request_4': clause('readTags', None,
                                    before=[dict(earlier='readPage', later='readTags')]),
            })

    def designer(context):
        return dict(interpretation='匿名读取分页结果与标签', relevant=True, questions=[],
            not_covered=[], cases=[case('分页与标签读取', [
                step(context, 'page', 'readPage', None, [
                    ('query', 'page', 3, None, None)]),
                step(context, 'tags', 'readTags', None)])])

    done, sent, seen = await run_task(tmp_path, monkeypatch, text, focus, designer)
    assert_finished(done)
    assert sent == ['/pages', '/tags']
    asked, = done['summary']['focus']['expectation_questions']
    assert (asked['operation_id'], asked['role']) == ('readBuild', 'reviewer')
    assert 'readBuild' in done['summary']['input_blocked_operations']
    assert not done['summary']['complete']


# --- P0-3: a required order may span execution contexts ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("can_repair", [True, False])
async def test_focus_order_correction_is_structured_and_used_at_most_once(
        tmp_path, monkeypatch, can_repair):
    text = '先匿名读取构建信息；然后匿名读取标签'
    feedback = []

    def focus(context):
        previous = context.get('previous_order_error')
        feedback.append(previous)
        correct = previous is not None and can_repair
        return wire(dict(readBuild=bundle(test_normal=True), readTags=bundle(test_normal=True)),
            dict(request_1=clause('readBuild'), request_2=clause('readTags', before=[dict(
                earlier='readBuild', later='readTags',
                earlier_source_id='request_1' if correct else 'request_2', earlier_index=0,
                later_source_id='request_2', later_index=0)])))

    def designer(context):
        return dict(interpretation='依序读取', relevant=True, questions=[], not_covered=[],
            cases=[case('构建及标签', [step(context, 'build', 'readBuild', None),
                                      step(context, 'tags', 'readTags', None)])])

    done, sent, _ = await run_task(tmp_path, monkeypatch, text, focus, designer)
    first, correction = feedback
    assert first is None
    assert correction['code'] == 'FOCUS_REQUIREMENT_ORDER_INSTANCE_INVALID'
    assert correction['detail']['order']['earlier_source_id'] == 'request_2'
    assert correction['detail']['available']['earlier'] == [
        dict(source_id='request_1', action_index=0)]
    assert '*_source_id' in correction['detail']['hint']
    records = [r for r in done['summary']['design_records'] if r['group_id'] == 'focus']
    assert [r['model_call']['calls'] for r in records] == [1, 1]
    assert [r['status'] for r in records] == ['REJECTED', 'SELECTED' if can_repair else 'REJECTED']
    if can_repair:
        assert_finished(done)
        assert sent == ['/build', '/tags']
    else:
        assert done['status'] == 'NEEDS_REVIEW'
        assert sent == []


@pytest.mark.asyncio
async def test_required_order_across_independent_cases_still_executes_in_order(
        tmp_path, monkeypatch):
    text = ('先确认 reviewer 查看构建信息的拒绝规则；构建接口另以匿名身份连续读取两次；'
            '两次都核对 HTTP 200；最后用 archivist 读取当前账号并核对别名为 archive-reader')

    def focus(context):
        return wire(dict(
            readBuild=bundle(test_normal=True, expectation_questions=[dict(
                kind='permission', role='reviewer', status_choices=[403, 404],
                source_ref=ref_of(context, '拒绝规则'),
                question='reviewer 查看构建信息时应当返回 403 还是 404？',
                requested_behavior='确认 reviewer 构建接口拒绝规则')]),
            readAccount=bundle(test_normal=True)), {
                'request_1': clause('readBuild', 'reviewer', disposition='needs_review',
                    actions=[]),
                'request_2': clause('readBuild', None),
                'request_3': clause('readBuild', None, response_checks=[
                    expectation(context, 'readBuild', '$.build')]),
                'request_4': clause('readAccount', 'archivist',
                    before=[dict(earlier='readBuild', later='readAccount')],
                    response_checks=[expectation(context, 'readAccount', '$.alias')]),
            })

    def designer(context):
        return dict(interpretation='匿名读取两次构建，再由归档身份读取账号', relevant=True,
            questions=[], not_covered=[], cases=[
                case('匿名读取构建信息两次', [
                    step(context, 'build_1', 'readBuild', None),
                    step(context, 'build_2', 'readBuild', None)]),
                case('归档身份读取账号', [step(context, 'account', 'readAccount', 'archivist')]),
            ])

    done, sent, seen = await run_task(tmp_path, monkeypatch, text, focus, designer)
    assert_finished(done)
    assert sent == ['/build', '/build', '/account']
    assert done['summary']['permission_action_scope'] == {'readBuild': [None]}
    # The pending reviewer identity never becomes a request, only a question.
    assert not [case_ for case_ in done['summary']['groups']
                if 'reviewer' in json.dumps(case_.get('diagnostic', {}))]
    assert not done['summary']['complete']


# --- P1: presentation language is not a structural gate ------------------------------


def test_presentation_language_is_reported_without_rewriting():
    from agenttest.diagnostics import presentation_language

    assert presentation_language('按原文核对')['state'] == 'CHINESE'
    assert presentation_language('Checked only the approved reads.') == dict(
        state='NON_CHINESE', has_latin=True, preserved=True)
    assert presentation_language('   ')['state'] == 'EMPTY'


def test_non_chinese_focus_interpretation_is_accepted_and_preserved():
    from agenttest.product_focus import FocusSelection

    selection = FocusSelection.model_validate(dict(
        interpretation='Owner reads the index, then the reader opens the entry.',
        mode='selected', operation_ids=['readIndex'], question=None,
        pending_questions=[], not_covered=[], blocked_operation_ids=[]))
    assert selection.interpretation == (
        'Owner reads the index, then the reader opens the entry.')
    from agenttest.diagnostics import presentation_language

    assert presentation_language(selection.interpretation)['state'] == 'NON_CHINESE'


@pytest.mark.asyncio
async def test_non_chinese_design_prose_does_not_become_a_schema_failure(tmp_path, monkeypatch):
    text = 'owner 读取 indexId=41 的索引'

    def focus(context):
        return wire(dict(readIndex=bundle(test_normal=True, input_requirements=[
            declared('path_parameters/indexId', 41, ref_of(context, 'indexId=41'))])), {
            'request_1': clause('readIndex', 'owner', response_checks=[
                expectation(context, 'readIndex', '$.links[0].id')])})

    def designer(context):
        return dict(interpretation='Read the index once under the approved owner identity.',
            relevant=True, questions=[], not_covered=[], cases=[dict(
                title='Index read under owner', kind='normal', plan=dict(name='index',
                    steps=[step(context, 'index', 'readIndex', 'owner', [
                        ('path_parameters', 'indexId', 41, None, None)])]))])

    done, sent, seen = await run_task(tmp_path, monkeypatch, text, focus, designer)
    assert_finished(done)
    assert sent == ['/indexes/41']
    record, = [entry for entry in done['summary']['design_records']
               if entry['group_id'] == 'group_1']
    assert record['presentation'] == dict(state='NON_CHINESE', has_latin=True, preserved=True)
    assert record['interpretation'] == (
        'Read the index once under the approved owner identity.')
    assert any('Index read under owner' in (case.get('title') or '')
               for case in done['summary']['cases'])


# --- Safety negatives ----------------------------------------------------------------


def test_pending_clause_without_a_witness_is_still_refused():
    from agenttest.request_requirements import decode_requirements, requirement_schema

    text = '读取索引 indexId=41；顺便看看构建接口'
    context = dict(user_task=text, request_atoms=request_atoms(text),
        approved_ids=['readIndex', 'readBuild'], request_atoms_present=True,
        managed_authentication=[dict(identity=role, credential_status='READY')
                                for role in ROLES],
        permission_question_roles={'readBuild': ['reviewer']},
        grouped_index=[dict(group_id='group_1', operations=[
            dict(id='readIndex', method='GET', path='/indexes/{indexId}', summary='索引'),
            dict(id='readBuild', method='GET', path='/build', summary='构建信息')])],
        response_expectations={}, resource_dependencies=[])
    from agenttest.product_focus import FocusSelection
    from agenttest.request_requirements import validate_requirement_links

    selection = FocusSelection.model_validate(dict(
        interpretation='只读取索引并保留构建接口问题', mode='selected',
        operation_ids=['readIndex'], question=None, pending_questions=[], not_covered=[],
        blocked_operation_ids=[], requirements=[
            RequestRequirement(source_id='request_1', source_quote='读取索引 indexId=41',
                **clause('readIndex')),
            RequestRequirement(source_id='request_2', source_quote='顺便看看构建接口',
                **clause(None, actions=[], disposition='needs_review'))]))
    with pytest.raises(ValidationFailure, match='FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE'):
        validate_requirement_links(selection.requirements, context, selection)
    assert requirement_schema(context)['properties'].keys() == {'request_1', 'request_2'}
    assert decode_requirements is not None


def test_review_scope_removes_a_pending_identity_action_even_in_another_clause():
    from agenttest.product_focus import FocusSelection

    selection = FocusSelection.model_validate(dict(
        interpretation='确认权限并匿名读取构建信息', mode='selected',
        operation_ids=['readBuild'], question=None, pending_questions=[], not_covered=[],
        blocked_operation_ids=[], requirements=[
            RequestRequirement(source_id='request_1', source_quote='确认 reviewer 对构建接口的权限',
                **clause('readBuild', 'reviewer', disposition='needs_review', actions=[])),
            RequestRequirement(source_id='request_2', source_quote='别为确认权限再访问它',
                **clause('readBuild', 'reviewer'))],
        expectation_questions=[ExpectationQuestion(operation_id='readBuild',
            kind='permission', role='reviewer', status_choices=[403, 404],
            question='reviewer 访问构建接口应返回 403 还是 404？',
            requested_behavior='确认 reviewer 构建接口权限',
            source_quote='确认 reviewer 对构建接口的权限')]))
    blocked, planning, scope, holds = permission_review_scope(selection)
    assert planning[1]['disposition'] == 'needs_review' and planning[1]['actions'] == []
    assert scope == {'readBuild': []} and blocked == {'readBuild'}
    assert {hold['role'] for hold in holds} == {'reviewer'}
