"""No paid calls: offline preflight and simulated one-call smoke/replay evidence."""

import json

import httpx
import pytest

pytest.importorskip('langgraph', reason='Batch smoke requires existing Agent extra')

from agenttest import batch_smoke
from agenttest.batch_demo import manual_batch_draft
from agenttest.batch_intent import interpret_batch_task, validate_batch_intent
from agenttest.batch_smoke import SMOKE_TASK, execute_smoke, prepare_smoke, smoke_settings
from agenttest.eval_v2.archive import inventory, read_json


def environment(tmp_path, monkeypatch):
    env = tmp_path / 'model.env'
    env.write_text('AGENTTEST_MODEL=ecnu-plus\n'
                   'OPENAI_BASE_URL=https://chat.ecnu.edu.cn/open/api/v1\n'
                   'OPENAI_API_KEY=synthetic-smoke-key\n', encoding='utf-8')
    monkeypatch.setattr(batch_smoke, 'clean_identity', lambda: {
        'commit': 'synthetic', 'worktree_status': '', 'checkout_sha256': 'synthetic'})
    monkeypatch.setattr(batch_smoke, 'private_output', lambda path: None)
    return env


def test_user_smoke_text_has_complete_goal_scope():
    assert interpret_batch_task(SMOKE_TASK).course_name == '批量分析验收'
    validate_batch_intent(SMOKE_TASK, manual_batch_draft().scenario())


@pytest.mark.parametrize('mutation', [
    'alice', '两行', '批量分析任务', '等待任务成功后', '读取结果', '两条记录',
])
def test_smoke_full_sentence_refuses_missing_or_wrong_targets(mutation):
    from agenttest.policies import NeedsReview

    with pytest.raises(NeedsReview):
        interpret_batch_task(SMOKE_TASK.replace(mutation, '不支持的目标'))
    with pytest.raises(NeedsReview):
        interpret_batch_task(SMOKE_TASK + '再删除课程')


def test_prepare_never_opens_transport_and_preserves_task_budget(tmp_path, monkeypatch):
    env = environment(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **k: pytest.fail('No model probe'))
    value = prepare_smoke(env, tmp_path / 'prepared')
    assert value['user_task'] == SMOKE_TASK
    assert value['real_model_calls'] == 0 and value['reserved_tokens'] == 22_048
    assert value['input_token_upper_bound'] <= 20_000
    assert value['budget']['max_calls'] == 1 and value['budget']['model_timeout'] == 30
    assert value['budget']['workflow_timeout'] == 120
    assert 'synthetic-smoke-key' not in json.dumps(value)
    assert smoke_settings(env).protocol == 'chatecnu'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'invalid_plan', 'usage', 'http_error', 'timeout'])
async def test_single_generated_plan_no_retry_and_safe_offline_replay(
        tmp_path, monkeypatch, failure):
    env = environment(tmp_path, monkeypatch)
    prepared = tmp_path / 'prepared'
    prepare_smoke(env, prepared)
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload['max_tokens'] == 2048 and payload['thinking'] == {'type': 'disabled'}
        assert 'max_completion_tokens' not in payload and 'store' not in payload
        assert 'strict' not in payload['response_format']['json_schema']
        assert 'first synthetic row' not in request.content.decode()
        assert SMOKE_TASK in payload['messages'][1]['content']
        if failure == 'timeout':
            raise httpx.ReadTimeout('private synthetic-smoke-key error')
        if failure == 'http_error':
            return httpx.Response(429, json={})
        draft = manual_batch_draft().model_dump()
        # Renaming demonstrates that replay uses this response, not the oracle file.
        draft['steps'][0]['id'] = 'model_login'
        if failure == 'invalid_plan':
            draft['steps'][3]['arguments'][0]['select'] = '$.courseId'
        usage = {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300}
        if failure == 'usage':
            usage['total_tokens'] = 400
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(draft)}}], 'usage': usage})

    result = await execute_smoke(env, prepared / 'preflight.json', tmp_path / 'run',
                                 model_transport=httpx.MockTransport(handler))
    assert len(requests) == 1 and result['real_model_calls'] == 0
    assert result['classification'] == 'DEVELOPMENT_ONLY'
    assert result['eval_v2_intent_score'] is None and result['freeze_valid']
    assert 'synthetic-smoke-key' not in json.dumps(result)
    if failure is None:
        assert result['task_satisfied'] and result['model_call']['total_tokens'] == 300
        assert [r['outcome'] for r in result['offline_replay']] == [
            'ASSERTION_FAILED', 'ASSERTION_FAILED']
        evidence = read_json(tmp_path / 'run/plan-evidence.json')
        assert evidence['plan_evidence']['steps'][0]['step_id'] == 'model_login'
        assert evidence['options'][2]['fixture_id'] == 'mini_two_rows_v1'
        assert evidence['plan_evidence']['steps'][3]['selectors'] == {
            'path_parameters.batchId': '$.batchId'}
    else:
        assert not result['task_satisfied'] and result['offline_replay'] == []
        if failure == 'invalid_plan':
            evidence = read_json(tmp_path / 'run/plan-evidence.json')
            assert not evidence['policy_authorized'] and not evidence['intent_verified']
            assert evidence['plan_evidence']['steps'][3]['selectors'] == {
                'path_parameters.batchId': '$.courseId'}
            assert evidence['options'][2]['fixture_id'] == 'mini_two_rows_v1'
            from agenttest.report import ReportStore

            report = ReportStore(tmp_path / 'run/reports').read(result['run_id'])
            assert report.steps == [] and report.tool_calls == 0
            assert report.diagnostic.code == 'POLICY_DEPENDENCY_MISMATCH'
    manifest = read_json(tmp_path / 'run/archive.json')
    assert manifest['files'] == inventory(tmp_path / 'run')
    with pytest.raises(FileExistsError):
        await execute_smoke(env, prepared / 'preflight.json', tmp_path / 'repeated',
                            model_transport=httpx.MockTransport(handler))
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_missing_optin_or_changed_preflight_never_calls_model(tmp_path, monkeypatch):
    env = environment(tmp_path, monkeypatch)
    prepared = tmp_path / 'prepared'
    prepare_smoke(env, prepared)
    with pytest.raises(ValueError, match='OPT_IN_REQUIRED'):
        await execute_smoke(env, prepared / 'preflight.json', tmp_path / 'run')
    document = read_json(prepared / 'preflight.json')
    document['budget']['max_calls'] = 2
    (prepared / 'preflight.json').write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='PREFLIGHT_CHANGED'):
        await execute_smoke(env, prepared / 'preflight.json', tmp_path / 'run',
            model_transport=httpx.MockTransport(lambda req: pytest.fail('No paid call')))


@pytest.mark.asyncio
async def test_drift_after_generation_blocks_all_business_requests(tmp_path, monkeypatch):
    env = environment(tmp_path, monkeypatch)
    prepared = tmp_path / 'prepared'
    prepare_smoke(env, prepared)

    def handler(request):
        monkeypatch.setattr(batch_smoke, 'clean_identity', lambda: {
            'commit': 'changed', 'worktree_status': '', 'checkout_sha256': 'changed'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': manual_batch_draft().model_dump_json()}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})

    value = await execute_smoke(env, prepared / 'preflight.json', tmp_path / 'run',
                                model_transport=httpx.MockTransport(handler))
    assert not value['freeze_valid'] and not value['task_satisfied']
    assert value['offline_replay'] == []
    from agenttest.report import ReportStore

    report = ReportStore(tmp_path / 'run/reports').read(value['run_id'])
    assert report.reason == 'EXECUTION_PREFLIGHT_FAILED' and report.steps == []
