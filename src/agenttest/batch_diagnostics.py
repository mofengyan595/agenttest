"""Development diagnostics only; deliberately no Eval v2 score or holdout claims."""

import hashlib
from pathlib import Path

import httpx

from agenttest.batch_demo import (
    DEFAULT_BATCH_TASK,
    BatchDraftPlanner,
    batch_inputs,
    manual_batch_draft,
    run_batch_demo,
    run_manual_batch,
)
from agenttest.batch_plan import BATCH_PROMPT, BatchPlannerDraft
from agenttest.contracts import summarize_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import Outcome, ReportStore, fingerprint

ARCHIVE_VERSION = 'agenttest.batch.development.archive.v1'
_FILES = [
    'src/agenttest/batch_plan.py', 'src/agenttest/batch_demo.py',
    'src/agenttest/batch_intent.py', 'src/agenttest/batch_diagnostics.py',
    'src/agenttest/policies.py', 'src/agenttest/llm_planner.py', 'src/agenttest/workflow.py',
    'src/agenttest/executor.py', 'src/agenttest/dsl.py', 'src/agenttest/mini_target.py',
    'src/agenttest/planner_input.py', 'src/agenttest/verifier.py', 'src/agenttest/report.py',
    'src/agenttest/eval_v2/models.py', 'src/agenttest/eval_v2/scoring.py',
    'src/agenttest/safety.py', 'src/agenttest/request_validation.py',
    'src/agenttest/credentials.py', 'src/agenttest/contracts.py', 'src/agenttest/plan_json.py',
    'src/agenttest/selectors.py', 'src/agenttest/diagnostics.py', 'src/agenttest/report_files.py',
    'src/agenttest/demo.py', 'src/agenttest/evaluation.py', 'src/agenttest/eval_v2/archive.py',
    'examples/demo-target.json', 'examples/demo-scenario.json', 'scripts/check_batch_workflow.py',
    'pyproject.toml', 'requirements-agent.lock',
    'examples/batch-draft.json', 'examples/batch-policies.json', 'examples/batch-csv-fixture.json',
]


def source_snapshot() -> dict[str, str]:
    return {name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
            for name in _FILES}


def batch_plan_diagnostics(plan, registry) -> dict:
    """Retain hashes, selectors and resolved policy options missing from Eval v2 evidence.

    This is explanatory evidence, not an independent quality rubric. Registry approval
    and the runtime intent gate cannot replace independent human-reviewed labels.
    """
    previous, options = {}, []
    for step in plan.steps:
        policy = registry.resolve(step, 'mini_target_batch_nl_v1', previous)
        options.append({
            'step_id': step.id, 'policy_sha256': fingerprint(policy.model_dump()),
            'fixture_id': policy.csv_fixture.id if policy.csv_fixture else None,
            'upload_sha256': fingerprint(step.upload.model_dump()) if step.upload else None,
            'poll_policy': policy.poll.model_dump() if step.poll and policy.poll else None,
            'assertions': policy.assertions.model_dump(),
        })
        previous[step.id] = step
    return {'plan_evidence': capture_plan(plan).model_dump(mode='json'), 'options': options}


def verify_batch_archive(directory: Path) -> dict:
    """Verify sealed membership/digests and report identity without replaying requests."""
    manifest = read_json(directory / 'archive.json')
    if (manifest.get('schema_version') != ARCHIVE_VERSION
            or manifest.get('files') != inventory(directory)):
        raise ValueError('Batch archive integrity mismatch')
    frozen = read_json(directory / 'freeze.json')
    results = read_json(directory / 'results.json')
    if (any(data.get('classification') != 'DEVELOPMENT_ONLY'
            or data.get('review_status') != 'UNREVIEWED'
            or any(data.get(k) != 0 for k in ('real_model_calls', 'java_requests',
                                             'database_writes')) for data in (results, frozen))
            or results.get('eval_v2_intent_score') is not None
            or results.get('freeze_sha256') != fingerprint(frozen)):
        raise ValueError('Batch archive scope mismatch')
    ids = [r['case_id'] for r in results['records']]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Batch archive identity mismatch')
    store = ReportStore(directory / 'reports')
    for row in results['records']:
        report = store.read(row['run_id'])
        if (fingerprint(report.model_dump()) != row['report_sha256']
                or report.outcome.value != row['runtime_outcome']
                or report.task_satisfied != row['task_satisfied']
                or report.user_task_sha256 != row['user_task_sha256']):
            raise ValueError('Batch archive report mismatch')
    return results


async def run_batch_diagnostics(directory: Path) -> dict:
    from agenttest.workflow import FakeLLM

    # Exclusive destination protects all previous diagnostic and historical evidence.
    directory.mkdir(parents=True, exist_ok=False)
    snapshot = source_snapshot()
    target, config, registry, _ = batch_inputs()
    frozen = {'classification': 'DEVELOPMENT_ONLY', 'review_status': 'UNREVIEWED',
              'source_sha256': snapshot, 'task_sha256': fingerprint(DEFAULT_BATCH_TASK),
              'config_sha256': fingerprint(config.model_dump()),
              'registry_sha256': registry.digest(),
              'contract_sha256': fingerprint(summarize_openapi(target.openapi()).model_dump()),
              'prompt_sha256': fingerprint(BATCH_PROMPT),
              'draft_schema_sha256': fingerprint(BatchPlannerDraft.model_json_schema()),
              'real_model_calls': 0, 'java_requests': 0, 'database_writes': 0}
    save_evaluation(frozen, directory / 'freeze.json')
    plan = manual_batch_draft().scenario()
    save_evaluation(batch_plan_diagnostics(plan, registry), directory / 'approved-plan.json')
    store, records = ReportStore(directory / 'reports'), []

    def record(case_id, source, fault, report, expected):
        records.append({
            'case_id': case_id, 'source': source, 'fault': fault,
            'runtime_outcome': report.outcome.value, 'expected_outcome': expected.value,
            'expected_behavior_observed': report.outcome == expected,
            'task_mode': report.task_mode, 'task_satisfied': report.task_satisfied,
            'intent_verified': report.intent_verified,
            'user_task_sha256': report.user_task_sha256,
            'diagnostic': report.diagnostic.model_dump() if report.diagnostic else None,
            'request_count': sum(len(s.attempts) for s in report.steps),
            'executed_operations': [s.operation_id for s in report.steps],
            'run_id': report.run_id, 'report_sha256': fingerprint(report.model_dump()),
            'real_model_calls': 0,
        })

    for source in ('manual', 'fake', 'mock_transport'):
        for fault in ('none', 'terminal', 'result_count'):
            expected = Outcome.PASS if fault == 'none' else Outcome.ASSERTION_FAILED
            if source == 'manual':
                report = await run_manual_batch(store, fault=fault)
            else:
                planner = BatchDraftPlanner(FakeLLM(manual_batch_draft().model_dump_json()))
                if source == 'mock_transport':
                    planner = OpenAICompatiblePlanner(ModelSettings(model='offline-fixture',
                        base_url='https://model.invalid/v1', api_key='synthetic-model-key'),
                        httpx.MockTransport(lambda req: httpx.Response(200, json={
                            'choices': [{'finish_reason': 'stop', 'message': {
                                'content': manual_batch_draft().model_dump_json()}}],
                            'usage': {'prompt_tokens': 100, 'completion_tokens': 50,
                                      'total_tokens': 150}})), profile='mini_batch')
                report = await run_batch_demo(DEFAULT_BATCH_TASK, planner, store, fault=fault)
            record(source + '_' + fault, source, fault, report, expected)

    for mutation in ('course_source', 'batch_source', 'csv', 'role', 'extra', 'poll', 'name'):
        invalid = plan.model_copy(deep=True)
        if mutation == 'course_source':
            invalid.steps[2].path_parameters['courseId'].select = '$.batchId'
        elif mutation == 'batch_source':
            invalid.steps[3].path_parameters['batchId'].from_step = 'course'
        elif mutation == 'csv':
            invalid.steps[2].upload.content += 'unapproved row\n'
        elif mutation == 'role':
            invalid.steps[2].role = 'bob'
        elif mutation == 'extra':
            invalid.steps.append(invalid.steps[-1].model_copy(update={'id': 'extra'}))
        elif mutation == 'poll':
            invalid.steps[3].poll = False
        else:
            invalid.steps[1].body['name'].value = 'wrong name'
        report = await run_batch_demo(DEFAULT_BATCH_TASK, FakeLLM(invalid.model_dump_json()), store)
        expected = Outcome.TOOL_ERROR if mutation in {'role', 'extra'} else Outcome.NEEDS_REVIEW
        record('reject_' + mutation, 'untrusted_dsl_fixture', None, report, expected)
        if report.steps or report.tool_calls:
            raise ValueError('Rejected batch plan executed requests')

    for suffix in ('删除课程', '导出凭据', '再使用 bob', '访问外网'):
        report = await run_batch_demo(DEFAULT_BATCH_TASK + suffix,
            BatchDraftPlanner(FakeLLM(manual_batch_draft().model_dump_json())), store)
        record('reject_task_' + str(len(records)), 'fake', None, report, Outcome.NEEDS_REVIEW)
        if report.steps or report.tool_calls:
            raise ValueError('Rejected batch intent executed requests')
    if source_snapshot() != snapshot:
        raise ValueError('BATCH_DEVELOPMENT_FREEZE_CHANGED')
    result = {'schema_version': 'agenttest.batch.development.v1',
              'classification': 'DEVELOPMENT_ONLY', 'review_status': 'UNREVIEWED',
              'eval_v2_intent_score': None,
              'score_limitation': ('UNLABELED_EXECUTION_OPTION retained; '
                                   'no independent batch rubric'),
              'freeze_sha256': fingerprint(frozen), 'records': records,
              'checks_verified': all(r['expected_behavior_observed'] for r in records),
              'real_model_calls': 0, 'java_requests': 0, 'database_writes': 0}
    save_evaluation(result, directory / 'results.json')
    lines = ['# Batch DEVELOPMENT_ONLY 离线诊断', '',
             'UNREVIEWED；无独立留出成绩；Eval v2 意图分数不可用。', '',
             '| 案例 | 来源 | 实际结果 | 自然语言任务完成 | 请求数 |',
             '| --- | --- | --- | --- | --- |']
    lines.extend(f"| {r['case_id']} | {r['source']} | {r['runtime_outcome']} | "
                 f"{r['task_satisfied']} | {r['request_count']} |" for r in records)
    (directory / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    save_evaluation({'schema_version': ARCHIVE_VERSION, 'files': inventory(directory)},
                    directory / 'archive.json')
    verify_batch_archive(directory)
    return result
