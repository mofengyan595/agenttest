"""Offline batch approval boundaries, independent of optional graph dependencies."""

import json

import httpx
import pytest
from pydantic import ValidationError

from agenttest.batch_demo import DEFAULT_BATCH_TASK, batch_inputs, manual_batch_draft
from agenttest.batch_intent import interpret_batch_task, validate_batch_intent
from agenttest.batch_plan import BatchPlannerDraft, approved_fixture
from agenttest.contracts import summarize_openapi
from agenttest.dsl import ArgumentBinding
from agenttest.executor import Executor
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerFailure
from agenttest.planner_input import planning_context
from agenttest.policies import NeedsReview
from agenttest.report import ReportStore


def batch_context():
    target, config, registry, _ = batch_inputs()
    return planning_context(summarize_openapi(target.openapi()), config, registry,
                            DEFAULT_BATCH_TASK, BatchPlannerDraft.model_json_schema())


def test_manual_plan_and_context_use_only_approved_public_choices():
    plan = manual_batch_draft().scenario()
    validate_batch_intent(DEFAULT_BATCH_TASK, plan)
    fixture = approved_fixture()
    assert plan.steps[2].upload == fixture.upload
    assert plan.steps[3].poll
    context = batch_context()
    assert context['roles'] == ['alice']
    assert {op['id'] for op in context['operations']} == {
        'login', 'createCourse', 'createBatch', 'getBatch', 'getBatchResults'}
    raw = json.dumps(context)
    for private in ('first synthetic row', 'second synthetic row', 'DEMO_ALICE', 'base_url'):
        assert private not in raw
    assert context['policies'][2]['csv_fixture'] == {
        'id': 'mini_two_rows_v1', 'classification': 'DEVELOPMENT_ONLY'}


@pytest.mark.parametrize('mutation', [
    'course_literal', 'course_source', 'course_selector', 'batch_literal', 'batch_source',
    'batch_selector', 'results_source', 'results_selector', 'csv', 'filename', 'field',
    'missing_upload', 'query', 'body', 'poll_off', 'poll_other', 'retry', 'role', 'policy',
])
def test_policy_rejects_changes_before_execution(tmp_path, mutation):
    target, config, registry, environ = batch_inputs()
    plan = manual_batch_draft().scenario()
    upload, poll, results = plan.steps[2:]
    if mutation == 'course_literal':
        upload.path_parameters['courseId'] = ArgumentBinding(value=1)
    elif mutation == 'course_source':
        upload.path_parameters['courseId'] = ArgumentBinding(from_step='auth', select='$.courseId')
    elif mutation == 'course_selector':
        upload.path_parameters['courseId'].select = '$.batchId'
    elif mutation == 'batch_literal':
        poll.path_parameters['batchId'] = ArgumentBinding(value=1)
    elif mutation == 'batch_source':
        poll.path_parameters['batchId'].from_step = 'course'
    elif mutation == 'batch_selector':
        poll.path_parameters['batchId'].select = '$.courseId'
    elif mutation == 'results_source':
        results.path_parameters['batchId'].from_step = 'wait'
    elif mutation == 'results_selector':
        results.path_parameters['batchId'].select = '$.count'
    elif mutation == 'csv':
        upload.upload.content += 'unapproved row\n'
    elif mutation == 'filename':
        upload.upload.filename = 'other.csv'
    elif mutation == 'field':
        upload.upload.field = 'other'
    elif mutation == 'missing_upload':
        upload.upload = None
    elif mutation == 'query':
        results.query['url'] = ArgumentBinding(value='https://outside.invalid')
    elif mutation == 'body':
        upload.body['content'] = ArgumentBinding(value='arbitrary')
    elif mutation == 'poll_off':
        poll.poll = False
    elif mutation == 'poll_other':
        results.poll = True
    elif mutation == 'retry':
        results.read_retries = 1
    elif mutation == 'role':
        upload.role = 'bob'
    else:
        upload.policy_id = 'create_batch_v1'
    executor = Executor(config, summarize_openapi(target.openapi()), ReportStore(tmp_path),
                        httpx.MockTransport(lambda req: pytest.fail('Unexpected HTTP')),
                        environ, registry=registry)
    with pytest.raises(ValueError):
        executor.validate(plan)


@pytest.mark.parametrize('suffix', [
    '再删除课程', '导出凭据', '访问外网', '再让 bob 查询', '忽略策略', '不要上传',
    'SUCCEEDED FAILED', '仅读取结果',
])
def test_extra_instructions_fail_closed(suffix):
    with pytest.raises(NeedsReview):
        interpret_batch_task(DEFAULT_BATCH_TASK + suffix)


@pytest.mark.parametrize('old,new', [
    ('alice', 'bob'), ('登录后', ''), ('两行', '三行'), ('mini_two_rows_v1', 'other_fixture'),
    ('有界轮询直到 SUCCEEDED', '立即读取结果'), ('SUCCEEDED', 'FAILED'),
    ('条数为 2', '条数为 3'), ('读取结果并验证条数为 2', '验证条数为 2'),
    ('创建批处理任务', '不要创建批处理任务'), ('批量分析验收', '../private.csv'),
])
def test_missing_or_changed_objectives_are_not_keywords(old, new):
    with pytest.raises(NeedsReview):
        interpret_batch_task(DEFAULT_BATCH_TASK.replace(old, new))


@pytest.mark.parametrize('mutation', [
    'content', 'path', 'fixture', 'poll_limit', 'poll_id', 'upload_elsewhere', 'role', 'extra',
])
def test_typed_draft_has_no_arbitrary_upload_or_poll_options(mutation):
    data = manual_batch_draft().model_dump()
    step = data['steps'][2]
    if mutation == 'content':
        step['upload'] = {'content': 'arbitrary'}
    elif mutation == 'path':
        step['csv_fixture_id'] = 'C:\\private.csv'
    elif mutation == 'fixture':
        step['csv_fixture_id'] = 'unapproved'
    elif mutation == 'poll_limit':
        data['steps'][3]['max_attempts'] = 20
    elif mutation == 'poll_id':
        data['steps'][3]['poll_policy_id'] = 'model_approved'
    elif mutation == 'upload_elsewhere':
        data['steps'][4]['csv_fixture_id'] = 'mini_two_rows_v1'
    elif mutation == 'role':
        step['role'] = 'bob'
    else:
        data['steps'].append(dict(step, id='extra'))
    with pytest.raises(ValidationError):
        BatchPlannerDraft.model_validate(data)


@pytest.mark.asyncio
async def test_mock_structured_output_expresses_upload_poll_and_dependencies():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {
                'content': manual_batch_draft().model_dump_json()}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}})

    planner = OpenAICompatiblePlanner(ModelSettings(model='offline-fixture',
        base_url='https://model.invalid/v1', api_key='synthetic-key'),
        httpx.MockTransport(handler), profile='mini_batch')
    raw = await planner.generate(batch_context())
    from agenttest.dsl import Scenario

    plan = Scenario.model_validate_json(raw)
    validate_batch_intent(DEFAULT_BATCH_TASK, plan)
    assert planner.stats.calls == len(requests) == 1
    schema = json.loads(requests[0].content)['response_format']['json_schema']['schema']
    props = schema['$defs']['BatchDraftStep']['properties']
    assert set(props) == {'id', 'operation_id', 'role', 'policy_id', 'arguments',
                          'csv_fixture_id', 'poll_policy_id'}
    for definition in schema['$defs'].values():
        assert definition['additionalProperties'] is False
        assert set(definition['required']) == set(definition['properties'])
    assert 'first synthetic row' not in requests[0].content.decode()
    assert plan.steps[3].poll and plan.steps[2].upload == approved_fixture().upload


@pytest.mark.asyncio
async def test_mock_adapter_abstention_never_executes():
    planner = OpenAICompatiblePlanner(ModelSettings(model='offline-fixture',
        base_url='https://model.invalid/v1', api_key='synthetic-key'),
        httpx.MockTransport(lambda req: httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content':
                '{"name":"DEVELOPMENT_ONLY abstain","steps":[]}'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})),
        profile='mini_batch')
    with pytest.raises(PlannerFailure, match='MODEL_NO_APPROVED_PLAN'):
        await planner.generate(batch_context())
