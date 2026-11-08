"""Two deterministic approved flows per arm, validating recorder and real HTTP binding evidence."""

import asyncio
import json
import sys
from pathlib import Path

from budget import cap
from fixture import encoded, sha
from grade import evaluate
from observe import install_observers

FLOWS = dict(
    mix=[
        dict(id="shelf", operation_id="listShelf", role="custodian",
             path_parameters={"shelfId": dict(value=12)}),
        dict(id="slip", operation_id="fetchSlip", role="custodian",
             path_parameters={"slipCode": dict(from_step="shelf", select="$.cards[0].slip")}),
        dict(id="notice", operation_id="fetchNotice", role=None),
    ],
    order=[
        dict(id="pulse1", operation_id="checkPulse", role=None),
        dict(id="pulse2", operation_id="checkPulse", role=None),
        dict(id="ledger", operation_id="readLedger", role="auditor",
             path_parameters={"ledgerId": dict(value=7)}),
    ],
)


def rubric(name):
    """Frozen expectation for one deterministic flow; shared with the scorer preflight."""
    if name == "mix":
        return dict(
            steps=[
                dict(operation="listShelf", role="custodian", status=200,
                     values={"path_parameters": {"shelfId": 12}}, fields=[]),
                dict(operation="fetchSlip", role="custodian", status=200,
                     values={"path_parameters": {"slipCode": "S-31"}},
                     fields=[dict(select="$.code", operator="equals", expected="S-31")]),
                dict(operation="fetchNotice", role=None, status=200, values={}, fields=[]),
            ],
            gaps=[],
            order=[0, 1, 2],
            bindings=[
                dict(producer="listShelf", consumer="fetchSlip", source_role="custodian",
                     role="custodian", location="path_parameters", name="slipCode",
                     selector="$.cards[0].slip")
            ],
        )
    return dict(
        steps=[
            dict(operation="checkPulse", role=None, status=200, values={}, fields=[]),
            dict(operation="checkPulse", role=None, status=200, values={}, fields=[]),
            dict(operation="readLedger", role="auditor", status=200,
                 values={"path_parameters": {"ledgerId": 7}}, fields=[]),
        ],
        gaps=[],
        order=[0, 1, 2],
        bindings=[],
    )


async def flow(folder, arm, name):
    source = folder / ("source-" + arm)
    sys.path.insert(0, str(source / "src"))
    import agenttest
    from agenttest.contracts import load_openapi
    from agenttest.dsl import Scenario
    from agenttest.executor import Executor
    from agenttest.generic_intake import GenericSetup, build_generic_project
    from agenttest.goal_server import target_environment
    from agenttest.report import PlanStepEvidence, ReportStore, RunReport

    assert Path(agenttest.__file__).resolve().is_relative_to(source / "src")
    catalog = json.loads((folder / "configs.json").read_bytes())
    cfg = catalog[name]
    environ = target_environment(folder / "private/model.env", {})
    project = cap(
        build_generic_project(
            "preflight_" + name,
            cfg["document"],
            load_openapi(cfg["document"]),
            GenericSetup.model_validate(cfg["generic"]),
            environ=environ,
        )
    )
    policies = {p.operation_id: p.id for p in project.registry.definitions()}
    steps = [dict(step, policy_id=policies[step["operation_id"]]) for step in FLOWS[name]]
    scenario = Scenario.model_validate(dict(name="technical_fixture_preflight_" + name,
                                            steps=steps))
    observations = folder / (arm + "-deterministic-observations-" + name)
    install_observers(observations, list(environ.values()))
    structure = [
        PlanStepEvidence(
            step_id=s.id,
            operation_id=s.operation_id,
            role=s.role,
            policy_id=s.policy_id,
            dependencies={
                location + "." + parameter: binding.from_step
                for location in ("path_parameters", "query", "body")
                for parameter, binding in getattr(s, location).items()
                if binding.from_step
            },
        )
        for s in scenario.steps
    ]
    report = RunReport(
        scenario_sha256=sha(scenario.model_dump_json().encode()),
        contract_sha256=sha(project.contract.model_dump_json().encode()),
        environment=project.target.environment,
        target_origin=project.target.base_url,
        task_mode="model_designed",
        plan_structure=structure,
    )
    identifier = f"_deterministic_{arm}_{name}_" + report.run_id
    (folder / "active.json").write_bytes(encoded(dict(id=identifier, config=name)))
    executor = Executor(
        project.target,
        project.contract,
        ReportStore(folder / (arm + "-deterministic-reports")),
        environ=environ,
        registry=project.registry,
    )
    report = await executor.run(scenario, report=report)
    (folder / "active.json").write_bytes(encoded(dict(id="_idle")))
    assert report.outcome.value == "PASS", report.reason
    audit = [
        json.loads(line)
        for line in (folder / "target-audit.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["position"] == identifier
    ]
    observed = [json.loads(path.read_bytes()) for path in observations.glob("*.json")]
    authority = json.loads((folder / (arm + "-authority.json")).read_bytes())[name]
    item = dict(rubric=rubric(name))
    result = evaluate(item, {}, [report.model_dump(mode="json")], authority, audit, observed)
    assert result["full_execution"] and result["evidence_integrity"], result
    with (folder / (arm + "-execution-preflight-" + name + ".json")).open(
        "x", encoding="utf-8", newline="\n"
    ) as stream:
        json.dump(
            dict(result=result, report=report.model_dump(mode="json"), observations=observed,
                 audit=audit, authority=authority, item=item, model_calls=0),
            stream,
            ensure_ascii=False,
            indent=2,
        )
    print(arm, name, "actual approved flow / binding / independent scorer passed, 0 model")


async def main(folder, arm):
    for name in FLOWS:
        await flow(folder, arm, name)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve(), sys.argv[2]))
