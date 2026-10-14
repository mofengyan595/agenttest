"""Disposable in-process composition; reuse the existing Workflow and Executor."""

import json
import secrets

import httpx
from pydantic import ValidationError

from agenttest.composed_intent import validate_composed_intent
from agenttest.composed_plan import PROFILE, ComposedPlannerDraft, composed_registry
from agenttest.demo import PROJECT_ROOT
from agenttest.diagnostics import Layer, schema_diagnostic
from agenttest.llm_planner import PlannerFailure
from agenttest.mini_target import Fault, create_target
from agenttest.plan_json import bounded_json
from agenttest.report import Outcome, ReportStore, RunReport
from agenttest.safety import TargetConfig


def composed_inputs(fault: Fault = "none"):
    config = TargetConfig.model_validate_json(
        (PROJECT_ROOT / "examples/demo-target.json").read_bytes())
    environ = {f"DEMO_{role.upper()}_{key}": (
        role if key == "USERNAME" else secrets.token_urlsafe(24))
               for role in ("alice", "bob") for key in ("USERNAME", "PASSWORD")}
    target = create_target({role: environ[f"DEMO_{role.upper()}_PASSWORD"]
                            for role in ("alice", "bob")}, fault)
    config.business_scenario = PROFILE
    config.allowed_operations = ["login", "createCourse", "getCourse", "createBatch",
                                 "getBatch", "getBatchResults"]
    config.max_steps, config.max_requests = 8, 10
    config.connect_timeout, config.read_timeout, config.run_timeout = 1.0, 2.0, 15.0
    return target, config, composed_registry(), environ


class ComposedDraftPlanner:
    """Typed conversion for fixture planners; never supplies a missing step or argument."""

    def __init__(self, planner):
        self.planner = planner

    async def generate(self, context: dict) -> str:
        raw = await self.planner.generate(context)
        try:
            draft = ComposedPlannerDraft.model_validate(bounded_json(raw, 100_000))
        except ValidationError as exc:
            raise PlannerFailure(Outcome.TOOL_ERROR, "MODEL_INVALID_RESPONSE",
                                 schema_diagnostic(exc, Layer.MODEL)) from None
        if not draft.steps:
            raise PlannerFailure(Outcome.NEEDS_REVIEW, "MODEL_NO_APPROVED_PLAN")
        return draft.scenario().model_dump_json()


async def run_composed_demo(user_task: str, planner, store: ReportStore | None = None,
                            *, fault: Fault = "none", budget=None) -> RunReport:
    from agenttest.workflow import Workflow

    target, config, registry, environ = composed_inputs(fault)
    return await Workflow(json.dumps(target.openapi()).encode(), config, registry, planner,
                          store, httpx.ASGITransport(target, raise_app_exceptions=False),
                          environ, budget, user_task=user_task,
                          intent_validator=validate_composed_intent).run()
