"""Keyless demonstration; explicitly enabled operator authority, existing workflow."""

from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import Scenario
from agenttest.investigation import (
    FakeAnalyzer,
    InvestigationBudget,
    InvestigationConfig,
    ReadOnlyCapabilityRegistry,
)

DEMO_TASK = 'alice 创建“调查演示课程”并查询核对 ID 和名称，再让 bob 查询同一课程被拒绝。'


def investigation_inputs():
    """New synthetic demo data; never edit the approved evaluation tasks or labels."""
    plan = Scenario.model_validate_json(
        (PROJECT_ROOT / "examples/nl-ownership-plan.json").read_bytes())
    plan.name = "read-only investigation demonstration"
    next(s for s in plan.steps if s.operation_id == "createCourse").body[
        "name"].value = "调查演示课程"
    config = InvestigationConfig(enabled=True, budget=InvestigationBudget(
        max_requests=1, max_rounds=1, max_model_calls=1, timeout=5.0))
    capabilities = ReadOnlyCapabilityRegistry.from_json(
        (PROJECT_ROOT / "examples/nl-readonly-capabilities.json").read_bytes())
    return plan, config, capabilities


async def run_investigation_demo(store=None, *, fault="ownership"):
    from agenttest.natural_demo import run_natural_demo
    from agenttest.workflow import FakeLLM

    plan, config, capabilities = investigation_inputs()
    return await run_natural_demo(DEMO_TASK, FakeLLM(plan.model_dump_json()), store,
                                  fault=fault, investigation=config,
                                  capabilities=capabilities, analyzer=FakeAnalyzer())
