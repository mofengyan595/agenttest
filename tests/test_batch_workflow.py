"""Graph, multipart, bounded polling and failure evidence on disposable offline targets."""

import json

import httpx
import pytest

pytest.importorskip('langgraph', reason='Batch NL workflow requires the existing agent extra')

from agenttest.batch_demo import (
    DEFAULT_BATCH_TASK,
    BatchDraftPlanner,
    batch_inputs,
    manual_batch_draft,
    run_batch_demo,
    run_manual_batch,
)
from agenttest.batch_intent import validate_batch_intent
from agenttest.dsl import ArgumentBinding
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import Outcome, ReportStore
from agenttest.workflow import FakeLLM, Workflow


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['manual', 'fake', 'mock_transport'])
@pytest.mark.parametrize('fault,outcome,operations', [
    ('none', Outcome.PASS, 5), ('terminal', Outcome.ASSERTION_FAILED, 4),
    ('result_count', Outcome.ASSERTION_FAILED, 5),
])
async def test_three_sources_keep_real_outcomes(tmp_path, source, fault, outcome, operations):
    store = ReportStore(tmp_path)
    if source == 'manual':
        report = await run_manual_batch(store, fault=fault)
    else:
        fake = FakeLLM(manual_batch_draft().model_dump_json())
        planner = BatchDraftPlanner(fake)
        if source == 'mock_transport':
            planner = OpenAICompatiblePlanner(ModelSettings(model='offline-fixture',
                base_url='https://model.invalid/v1', api_key='synthetic-key'),
                httpx.MockTransport(lambda req: httpx.Response(200, json={
                    'choices': [{'finish_reason': 'stop', 'message': {
                        'content': manual_batch_draft().model_dump_json()}}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 50,
                              'total_tokens': 150}})), profile='mini_batch')
        report = await run_batch_demo(DEFAULT_BATCH_TASK, planner, store, fault=fault)
        assert report.intent_verified and report.workflow_completed
        assert report.task_satisfied == (fault == 'none')
        if source == 'fake':
            assert fake.calls == 1
        else:
            assert report.model_call.calls == 1
    assert report.outcome == outcome and len(report.steps) == operations
    assert report == store.read(report.run_id)
    poll = report.steps[3]
    assert len(poll.attempts) == 2
    assert [a.status_code for a in poll.attempts] == [200, 200]
    if fault == 'terminal':
        failed = [c for c in poll.checks if not c.passed]
        assert any(c.selector == '$.state' and c.kind == 'equals' for c in failed)
        assert all(s.operation_id != 'getBatchResults' for s in report.steps)
    elif fault == 'result_count':
        assert any(c.kind == 'count' and c.selector == '$.items' and c.actual == 3
                   and not c.passed for c in report.steps[-1].checks)
    else:
        assert all(s.outcome == Outcome.PASS for s in report.steps)
        assert report.steps[-1].checks[-1].actual == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', [
    'course_source', 'course_selector', 'batch_source', 'batch_selector', 'results_source',
    'csv', 'role', 'extra', 'name', 'poll', 'retry', 'results_before_poll',
])
async def test_untrusted_dsl_cannot_bypass_policy_and_intent(tmp_path, mutation):
    plan = manual_batch_draft().scenario()
    if mutation == 'course_source':
        plan.steps[2].path_parameters['courseId'].from_step = 'auth'
    elif mutation == 'course_selector':
        plan.steps[2].path_parameters['courseId'].select = '$.batchId'
    elif mutation == 'batch_source':
        plan.steps[3].path_parameters['batchId'].from_step = 'course'
    elif mutation == 'batch_selector':
        plan.steps[3].path_parameters['batchId'].select = '$.courseId'
    elif mutation == 'results_source':
        plan.steps[4].path_parameters['batchId'].from_step = 'wait'
    elif mutation == 'csv':
        plan.steps[2].upload.content += 'third row\n'
    elif mutation == 'role':
        plan.steps[2].role = 'bob'
    elif mutation == 'extra':
        plan.steps.append(plan.steps[4].model_copy(update={'id': 'extra'}))
    elif mutation == 'name':
        plan.steps[1].body['name'] = ArgumentBinding(value='wrong name')
    elif mutation == 'poll':
        plan.steps[3].poll = False
    elif mutation == 'retry':
        plan.steps[4].read_retries = 1
    else:
        plan.steps[3], plan.steps[4] = plan.steps[4], plan.steps[3]
    report = await run_batch_demo(DEFAULT_BATCH_TASK, FakeLLM(plan.model_dump_json()),
                                  ReportStore(tmp_path))
    assert report.outcome in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}
    assert report.tool_calls == 0 and not report.steps and not report.task_satisfied
    if mutation in {'role', 'extra'}:
        assert report.outcome == Outcome.TOOL_ERROR and report.reason == 'PLAN_REJECTED'
        assert report.diagnostic.layer.value == 'dsl'
        assert report.diagnostic.code == (
            'DSL_UNKNOWN_ROLE' if mutation == 'role' else 'DSL_STEP_LIMIT')


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['删除课程', '导出凭据', '再使用 bob', '访问外网'])
async def test_valid_plan_cannot_hide_extra_user_objectives(tmp_path, suffix):
    fake = FakeLLM(manual_batch_draft().model_dump_json())
    report = await run_batch_demo(DEFAULT_BATCH_TASK + suffix, BatchDraftPlanner(fake),
                                  ReportStore(tmp_path))
    assert report.outcome == Outcome.NEEDS_REVIEW and not report.intent_verified
    assert report.diagnostic.code == 'INTENT_BATCH_UNSUPPORTED_OBJECTIVE'
    assert report.tool_calls == 0 and report.steps == [] and fake.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('behavior,expected,reason', [
    ('normal', Outcome.PASS, 'POLICY_EVALUATED'),
    ('stuck', Outcome.TEST_INFRA_ERROR, 'POLL_EXHAUSTED'),
    ('unknown', Outcome.ASSERTION_FAILED, 'POLL_CONTRACT_VIOLATION'),
    ('http_error', Outcome.ASSERTION_FAILED, 'POLL_CONTRACT_VIOLATION'),
    ('bad_count', Outcome.ASSERTION_FAILED, 'POLL_CONTRACT_VIOLATION'),
    ('create_error', Outcome.ASSERTION_FAILED, 'POLICY_EVALUATED'),
    ('result_409', Outcome.ASSERTION_FAILED, 'POLICY_EVALUATED'),
    ('timeout', Outcome.TEST_INFRA_ERROR, 'ReadTimeout'),
])
async def test_mock_target_multipart_binding_limits_and_no_write_retries(
        tmp_path, behavior, expected, reason):
    target, config, registry, environ = batch_inputs()
    requests = []

    def handler(req):
        requests.append(req)
        path = req.url.path
        if path == '/api/auth/login':
            assert json.loads(req.content)['password'] == environ['DEMO_ALICE_PASSWORD']
            return httpx.Response(200, json={'access_token': 'local-synthetic-token'})
        assert req.headers['authorization'] == 'Bearer local-synthetic-token'
        if path == '/api/courses':
            if behavior == 'create_error':
                return httpx.Response(500, json={})
            return httpx.Response(201, json={'courseId': 37, 'name': '批量分析验收'})
        if path == '/api/courses/37/analysis-batches':
            assert 'multipart/form-data' in req.headers['content-type']
            assert b'first synthetic row\nsecond synthetic row\n' in req.content
            assert b'name="file"; filename="input.csv"' in req.content
            return httpx.Response(202, json={'batchId': 91, 'state': 'QUEUED'})
        if path == '/api/analysis-batches/91':
            if behavior == 'timeout':
                raise httpx.ReadTimeout('private error')
            state = {'stuck': 'RUNNING', 'unknown': 'UNAPPROVED'}.get(behavior, 'SUCCEEDED')
            return httpx.Response(503 if behavior == 'http_error' else 200,
                                  json={'state': state, 'count': 3 if behavior == 'bad_count'
                                        else 2})
        assert path == '/api/analysis-batches/91/results'
        if behavior == 'normal':
            return httpx.Response(200, json={'items': [{'row': 1}, {'row': 2}]})
        return httpx.Response(409, json={'detail': 'not ready'})

    report = await Workflow(json.dumps(target.openapi()).encode(), config, registry,
        FakeLLM(manual_batch_draft().scenario().model_dump_json()), ReportStore(tmp_path),
        httpx.MockTransport(handler), environ, user_task=DEFAULT_BATCH_TASK,
        intent_validator=validate_batch_intent).run()
    assert report.outcome == expected and report.steps[-1].reason == reason
    assert report.task_satisfied == (behavior == 'normal') and report.workflow_completed
    assert sum(r.method == 'POST' and r.url.path == '/api/courses' for r in requests) == 1
    assert sum(r.method == 'POST' and 'analysis-batches' in r.url.path for r in requests) <= 1
    if behavior == 'stuck':
        assert len(report.steps[3].attempts) == 4
        assert len(requests) == 7 and len(report.steps) == 4
    if behavior == 'result_409':
        assert report.steps[-1].attempts[0].status_code == 409
    assert 'private error' not in report.model_dump_json()


@pytest.mark.asyncio
async def test_expected_409_is_not_batch_success(tmp_path):
    # The existing manual demo policy pattern can approve a business 409, but the NL
    # success path requires completed polling and 200 results and cannot select it.
    from agenttest.contracts import summarize_openapi
    from agenttest.dsl import ExpectedStatus
    from agenttest.executor import Executor
    from agenttest.policies import PolicyRegistry

    target, config, registry, environ = batch_inputs()
    plan = manual_batch_draft().scenario()
    plan.steps.pop(3)
    definitions = registry.definitions()
    results = next(p for p in definitions if p.id == 'batch_results_v1')
    results.id = 'development_results_not_ready'
    results.assertions.status = ExpectedStatus(codes=[409])
    results.assertions.fields = []
    plan.steps[-1].policy_id = results.id
    report = await Executor(config, summarize_openapi(target.openapi()), ReportStore(tmp_path),
        httpx.ASGITransport(target), environ, registry=PolicyRegistry(definitions)).run(plan)
    assert report.outcome == Outcome.PASS and report.steps[-1].attempts[0].status_code == 409
    assert report.task_mode == 'operator_plan' and report.task_satisfied is None
    rejected = await run_batch_demo(DEFAULT_BATCH_TASK, FakeLLM(plan.model_dump_json()),
                                     ReportStore(tmp_path / 'nl'))
    assert rejected.outcome == Outcome.NEEDS_REVIEW and not rejected.steps
    assert not rejected.task_satisfied


@pytest.mark.asyncio
async def test_course_name_and_step_ids_are_planned_data(tmp_path):
    draft = manual_batch_draft().model_copy(deep=True)
    ids = {step.id: 'planned_' + str(n) for n, step in enumerate(draft.steps)}
    for step in draft.steps:
        step.id = ids[step.id]
        for argument in step.arguments:
            if argument.from_step:
                argument.from_step = ids[argument.from_step]
    draft.steps[1].arguments[0].value = '离散数学批处理'
    fake = FakeLLM(draft.model_dump_json())
    task = DEFAULT_BATCH_TASK.replace('批量分析验收', '离散数学批处理')
    report = await run_batch_demo(task, BatchDraftPlanner(fake), ReportStore(tmp_path))
    assert report.outcome == Outcome.PASS and report.task_satisfied and fake.calls == 1
    assert [step.step_id for step in report.steps] == list(ids.values())
