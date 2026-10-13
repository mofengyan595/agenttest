"""Independent DEVELOPMENT_ONLY batch composition, always an in-process mini target."""

import json
import secrets

import httpx
from pydantic import ValidationError

from agenttest.batch_intent import validate_batch_intent
from agenttest.batch_plan import PROFILE, BatchPlannerDraft
from agenttest.demo import PROJECT_ROOT
from agenttest.diagnostics import Layer, schema_diagnostic
from agenttest.dsl import Scenario
from agenttest.llm_planner import PlannerFailure
from agenttest.mini_target import Fault, create_target
from agenttest.plan_json import bounded_json
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, ReportStore, RunReport
from agenttest.safety import TargetConfig

DEFAULT_BATCH_TASK = (
    'alice 登录后创建课程“批量分析验收”，使用批准的两行 CSV fixture mini_two_rows_v1 '
    '创建批处理任务，使用创建响应中的 courseId 和 batchId，有界轮询直到 SUCCEEDED，'
    '读取结果并验证条数为 2。'
)


def batch_inputs(fault: Fault = "none"):
    config = TargetConfig.model_validate_json(
        (PROJECT_ROOT / 'examples/demo-target.json').read_bytes())
    environ = {"DEMO_ALICE_USERNAME": "alice",
               "DEMO_ALICE_PASSWORD": secrets.token_urlsafe(24)}
    target = create_target({"alice": environ["DEMO_ALICE_PASSWORD"]}, fault)
    config.business_scenario = PROFILE
    config.allowed_operations = ["login", "createCourse", "createBatch", "getBatch",
                                 "getBatchResults"]
    config.roles = {"alice": config.roles["alice"]}
    config.max_steps, config.max_requests = 5, 8
    config.connect_timeout, config.read_timeout, config.run_timeout = 1.0, 2.0, 15.0
    registry = PolicyRegistry.from_json(
        (PROJECT_ROOT / "examples/batch-policies.json").read_bytes())
    return target, config, registry, environ


def manual_batch_draft() -> BatchPlannerDraft:
    """A deterministic development fixture, not a model-performance observation."""
    return BatchPlannerDraft.model_validate_json(
        (PROJECT_ROOT / "examples/batch-draft.json").read_bytes())


class BatchDraftPlanner:
    """Apply the same typed conversion to FakeLLM output, with exactly one generation."""

    def __init__(self, planner):
        self.planner = planner

    async def generate(self, context: dict) -> str:
        raw = await self.planner.generate(context)
        try:
            draft = BatchPlannerDraft.model_validate(bounded_json(raw, 100_000))
        except ValidationError as exc:
            raise PlannerFailure(Outcome.TOOL_ERROR, "MODEL_INVALID_RESPONSE",
                                 schema_diagnostic(exc, Layer.MODEL)) from None
        if not draft.steps:
            raise PlannerFailure(Outcome.NEEDS_REVIEW, "MODEL_NO_APPROVED_PLAN")
        return draft.scenario().model_dump_json()


async def run_batch_demo(user_task: str, planner, store: ReportStore | None = None,
                         *, fault: Fault = "none", budget=None, execution_guard=None) -> RunReport:
    from agenttest.workflow import Workflow

    target, config, registry, environ = batch_inputs(fault)
    return await Workflow(json.dumps(target.openapi()).encode(), config, registry, planner,
                          store, httpx.ASGITransport(target, raise_app_exceptions=False),
                          environ, budget, user_task=user_task,
                          intent_validator=validate_batch_intent,
                          execution_guard=execution_guard).run()


async def run_manual_batch(store: ReportStore | None = None, *, fault: Fault = "none") -> RunReport:
    from agenttest.contracts import summarize_openapi
    from agenttest.executor import Executor

    target, config, registry, environ = batch_inputs(fault)
    plan: Scenario = manual_batch_draft().scenario()
    return await Executor(config, summarize_openapi(target.openapi()), store,
                          httpx.ASGITransport(target, raise_app_exceptions=False), environ,
                          registry=registry).run(plan)
