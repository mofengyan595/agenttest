"""One explicitly opted-in batch smoke; preparation never contacts a model service."""

import time
from pathlib import Path

import httpx

from agenttest.batch_demo import batch_inputs, run_batch_demo
from agenttest.batch_diagnostics import batch_plan_diagnostics, source_snapshot
from agenttest.batch_intent import interpret_batch_task, validate_batch_intent
from agenttest.batch_plan import FIXTURE_ID, approved_fixture
from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.report import Outcome, ReportStore, fingerprint

SMOKE_TASK = ('使用 alice 创建课程“批量分析验收”，使用已批准的两行 CSV 测试数据创建批量分析任务，'
              '等待任务成功后读取结果，并验证返回两条记录。')
BASE_URL = 'https://chat.ecnu.edu.cn/open/api/v1'
BUDGET = {'max_calls': 1, 'max_input_tokens': 20_000, 'max_output_tokens': 2048,
          'max_total_tokens': 22_048, 'model_timeout': 30, 'workflow_timeout': 120,
          'retries': 0, 'replans': 0, 'probes': 0}


def smoke_settings(env_file: Path) -> ModelSettings:
    """Read only approved model settings; never display or archive the credential."""
    source = ModelSettings.from_env_file(env_file, environ={})
    if (source.model != 'ecnu-plus' or source.base_url != BASE_URL
            or source.protocol != 'chatecnu'):
        raise ValueError('BATCH_SMOKE_MODEL_IDENTITY_MISMATCH')
    return ModelSettings(model=source.model, base_url=source.base_url, api_key=source.api_key,
                         protocol='chatecnu', timeout=30, max_input_tokens=20_000,
                         max_output_tokens=2048, max_total_tokens=22_048)


def clean_identity() -> dict:
    identity = checkout_identity(PROJECT_ROOT)
    if identity['worktree_status']:
        raise ValueError('BATCH_SMOKE_REQUIRES_CLEAN_CHECKOUT')
    return identity


def private_output(path: Path) -> None:
    # Reuse the existing private evidence boundary; this does not enable A/B.
    from agenttest.development_ab import private_output as validate_output

    validate_output(PROJECT_ROOT, path)
    if path.exists():
        raise ValueError('BATCH_SMOKE_OUTPUT_EXISTS')


def preflight(settings: ModelSettings) -> dict:
    from agenttest.contracts import summarize_openapi

    interpret_batch_task(SMOKE_TASK)
    target, config, registry, _ = batch_inputs()
    contract = summarize_openapi(target.openapi())
    context = planning_context(contract, config, registry, SMOKE_TASK, Scenario.model_json_schema())
    planner = OpenAICompatiblePlanner(settings, profile='mini_batch')
    planner.prepare_request(context)
    return {'schema_version': 'agenttest.batch.smoke.preflight.v1',
            'classification': 'DEVELOPMENT_ONLY', 'review_status': 'UNREVIEWED',
            'user_task': SMOKE_TASK, 'user_task_sha256': fingerprint(SMOKE_TASK),
            'budget': BUDGET, 'model_config': public_model_config(settings),
            'source_sha256': source_snapshot(), 'git': clean_identity(),
            'context_sha256': fingerprint(context), 'registry_sha256': registry.digest(),
            'request_sha256': planner.stats.request_sha256,
            'request_bytes': planner.stats.request_bytes,
            'input_token_upper_bound': planner.stats.input_token_upper_bound,
            'reserved_tokens': 22_048, 'real_model_calls': 0,
            'java_requests': 0, 'database_writes': 0, 'eval_v2_intent_score': None}


def prepare_smoke(env_file: Path, output: Path) -> dict:
    private_output(output)
    value = preflight(smoke_settings(env_file))
    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(value, output / 'preflight.json')
    return value


class CapturedPlanner:
    """Retain the one generated plan in memory only, and check drift before any call."""

    def __init__(self, adapter, prepared, settings):
        self.adapter, self.prepared, self.settings = adapter, prepared, settings
        self.plan: Scenario | None = None
        self.invoked = False

    @property
    def stats(self):
        return self.adapter.stats

    async def generate(self, context: dict) -> str:
        if self.invoked or preflight(self.settings) != self.prepared:
            raise ValueError('BATCH_SMOKE_PREFLIGHT_CHANGED')
        if fingerprint(context) != self.prepared['context_sha256']:
            raise ValueError('BATCH_SMOKE_CONTEXT_CHANGED')
        self.invoked = True
        raw = await self.adapter.generate(context)
        self.plan = Scenario.model_validate_json(raw)
        return raw


async def execute_smoke(env_file: Path, prepared_path: Path, output: Path, *,
                        real_opt_in: bool = False,
                        model_transport: httpx.MockTransport | None = None) -> dict:
    """The real path requires separate operator approval; tests may inject MockTransport."""
    from agenttest.workflow import WorkflowBudget

    if not real_opt_in and model_transport is None:
        raise ValueError('BATCH_SMOKE_REAL_OPT_IN_REQUIRED')
    if model_transport is not None and type(model_transport) is not httpx.MockTransport:
        raise ValueError('BATCH_SMOKE_ONLY_MOCK_OVERRIDE_ALLOWED')
    settings = smoke_settings(env_file)
    prepared = read_json(prepared_path)
    if preflight(settings) != prepared:
        raise ValueError('BATCH_SMOKE_PREFLIGHT_CHANGED')
    private_output(output)
    # One consumed marker per prepared request; it is never removed/refunded on failure.
    marker = prepared_path.with_name('consumed.json')
    save_evaluation({'preflight_sha256': fingerprint(prepared), 'reserved_tokens': 22_048,
                     'slots': 1}, marker)
    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(prepared, output / 'preflight.json')
    adapter = OpenAICompatiblePlanner(settings, model_transport, profile='mini_batch')
    planner = CapturedPlanner(adapter, prepared, settings)
    store = ReportStore(output / 'reports')
    def guard():
        if preflight(settings) != prepared:
            raise ValueError('BATCH_SMOKE_PREFLIGHT_CHANGED')

    start = time.monotonic()
    report = await run_batch_demo(SMOKE_TASK, planner, store, budget=WorkflowBudget(
        max_model_calls=1, max_tool_calls=1, model_timeout=30, workflow_timeout=120),
        execution_guard=guard)
    elapsed_ms = round((time.monotonic() - start) * 1000, 3)
    replay = []
    evidence = None
    # Revalidate the actual generated plan. No oracle/manual fixture plan is loaded.
    if planner.plan is not None:
        _, _, registry, _ = batch_inputs()
        evidence = {'plan_evidence': capture_plan(planner.plan).model_dump(mode='json'),
                    'options': [{
                        'step_id': step.id,
                        'fixture_id': FIXTURE_ID if step.upload == approved_fixture().upload
                        else None,
                        'upload_sha256': fingerprint(step.upload.model_dump()) if step.upload
                        else None} for step in planner.plan.steps]}
        if report.policy_authorized:
            evidence = batch_plan_diagnostics(planner.plan, registry)
        evidence.update(policy_authorized=report.policy_authorized,
                        intent_verified=report.intent_verified)
        save_evaluation(evidence, output / 'plan-evidence.json')
        if report.task_satisfied and report.outcome == Outcome.PASS:
            validate_batch_intent(SMOKE_TASK, planner.plan)
            from agenttest.contracts import summarize_openapi
            from agenttest.executor import Executor

            for fault in ('terminal', 'result_count'):
                try:
                    guard()
                except (ValueError, OSError):
                    replay.append({'fault': fault, 'outcome': 'NOT_EXECUTED_FREEZE_CHANGED',
                                   'model_calls': 0})
                    break
                target, config, registry, environ = batch_inputs(fault)
                result = await Executor(config, summarize_openapi(target.openapi()), store,
                    httpx.ASGITransport(target, raise_app_exceptions=False), environ,
                    registry=registry).run(planner.plan)
                replay.append({'fault': fault, 'run_id': result.run_id,
                               'outcome': result.outcome.value,
                               'report_sha256': fingerprint(result.model_dump()),
                               'model_calls': 0})
    try:
        guard()
        freeze_valid = True
    except (ValueError, OSError):
        freeze_valid = False
    value = {'schema_version': 'agenttest.batch.smoke.v1',
             'classification': 'DEVELOPMENT_ONLY', 'review_status': 'UNREVIEWED',
             'source': 'real' if model_transport is None else 'mock_transport',
             'user_task': SMOKE_TASK, 'budget': BUDGET, 'reserved_tokens': 22_048,
             'runtime_outcome': report.outcome.value, 'task_satisfied': report.task_satisfied,
             'run_id': report.run_id, 'report_sha256': fingerprint(report.model_dump()),
             'model_call': adapter.stats.model_dump(mode='json'), 'elapsed_ms': elapsed_ms,
             'plan_evidence_available': evidence is not None, 'offline_replay': replay,
             'real_model_calls': adapter.stats.calls if model_transport is None else 0,
             'java_requests': 0, 'database_writes': 0, 'eval_v2_intent_score': None,
             'freeze_valid': freeze_valid}
    save_evaluation(value, output / 'results.json')
    save_evaluation({'schema_version': 'agenttest.batch.smoke.archive.v1',
                     'files': inventory(output)}, output / 'archive.json')
    return value
