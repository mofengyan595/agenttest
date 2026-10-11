"""Operator-owned Stage 3A fixture; no planner, URL input, or second HTTP engine."""

import json
from uuid import uuid4

import httpx

from agenttest.contracts import ContractSummary, load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.policies import PolicyRegistry
from agenttest.report import PlanStepEvidence, ReportStore, RunReport, fingerprint
from agenttest.safety import TargetConfig

FIXTURES = PROJECT_ROOT / "examples/courseinsight"


def inputs() -> tuple[Scenario, TargetConfig, ContractSummary, PolicyRegistry]:
    """Instantiate one approved synthetic fixture, preserving all approved expectations."""
    plan = json.loads((FIXTURES / "scenario.json").read_bytes())
    # 28 ASCII characters, below Java's 32 UTF-16-unit limit. No collision retry.
    plan["steps"][1]["body"]["code"]["value"] = "AT3A" + uuid4().hex[:24]
    scenario = Scenario.model_validate(plan)
    target = TargetConfig.model_validate_json((FIXTURES / "target.json").read_bytes())
    contract = load_openapi((FIXTURES / "openapi.json").read_bytes())
    # Pin the operator-generated code for this run; the model cannot parameterize it.
    from agenttest.policies import RegisteredPolicy

    entries = json.loads((FIXTURES / "policies.json").read_bytes())
    entries[1]["required_bindings"] = {"body": scenario.steps[1].model_dump()["body"]}
    registry = PolicyRegistry([RegisteredPolicy.model_validate(item) for item in entries])
    return scenario, target, contract, registry


async def execute(
    scenario: Scenario, target: TargetConfig, contract: ContractSummary, registry: PolicyRegistry,
    environ: dict[str, str], store: ReportStore,
    *, transport: httpx.AsyncBaseTransport | None = None,
) -> RunReport:
    """Execute once; caller must establish isolation before enabling target mutations."""
    report = RunReport(
        scenario_sha256=fingerprint(scenario.model_dump()),
        contract_sha256=fingerprint(contract.model_dump()),
        environment=target.environment, target_origin=target.base_url,
        cleanup="Retained synthetic course; operator-owned cleanup by manifest course code",
        selected_policy_ids=[step.policy_id for step in scenario.steps],
        plan_structure=[PlanStepEvidence(
            step_id=step.id, operation_id=step.operation_id, role=step.role,
            policy_id=step.policy_id,
            argument_sha256={f"body.{key}": fingerprint(binding.value)
                             for key, binding in step.body.items()},
            dependencies={f"path_parameters.{key}": f"{binding.from_step}:{binding.select}"
                          for key, binding in step.path_parameters.items()},
        ) for step in scenario.steps],
    )
    executor = Executor(target, contract, store, transport, environ, registry=registry)
    executor.validate(scenario)
    report.plan_valid = True
    # Safe, synthetic input only; credential values and HTTP responses never enter this file.
    store.save(report)
    (store.root / report.run_id / "fixture.json").write_text(json.dumps({
        "run_id": report.run_id, "scenario_sha256": report.scenario_sha256,
        "policy_registry_sha256": registry.digest(), "scenario": scenario.model_dump(),
        "cleanup": report.cleanup,
    }, indent=2), encoding="utf-8")
    return await executor.run(scenario, report=report)
