"""Offline demo composition; reusable executor remains independent of this target."""

import json
import secrets
from pathlib import Path

import httpx

from agenttest.contracts import summarize_openapi
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.mini_target import Fault, create_target
from agenttest.policies import PolicyRegistry
from agenttest.report import ReportStore, RunReport
from agenttest.safety import TargetConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def demo_inputs() -> tuple[Scenario, TargetConfig]:
    scenario = Scenario.model_validate_json(
        (PROJECT_ROOT / "examples/demo-scenario.json").read_bytes()
    )
    config = TargetConfig.model_validate_json(
        (PROJECT_ROOT / "examples/demo-target.json").read_bytes()
    )
    return scenario, config


async def run_demo(
    fault: Fault = "none", store: ReportStore | None = None, *, use_workflow: bool = False,
) -> RunReport:
    scenario, config = demo_inputs()
    environ = {
        "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": secrets.token_urlsafe(24),
        "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": secrets.token_urlsafe(24),
    }
    target = create_target({
        environ["DEMO_ALICE_USERNAME"]: environ["DEMO_ALICE_PASSWORD"],
        environ["DEMO_BOB_USERNAME"]: environ["DEMO_BOB_PASSWORD"],
    }, fault)
    registry = PolicyRegistry.from_json(
        (PROJECT_ROOT / "examples/demo-policies.json").read_bytes())
    transport = httpx.ASGITransport(app=target, raise_app_exceptions=False)
    if use_workflow:
        from agenttest.workflow import FakeLLM, Workflow

        return await Workflow(json.dumps(target.openapi()).encode(), config, registry,
                              FakeLLM(scenario.model_dump_json()), store, transport, environ,
                              task_mode="operator_plan").run()
    executor = Executor(
        config, summarize_openapi(target.openapi()), store,
        transport, environ, registry=registry,
    )
    return await executor.run(scenario)
