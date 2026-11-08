"""Two deterministic approved flows per arm for the F4 recheck fixture.

``chain`` exercises the dynamic binding and the approved assertion; ``review`` exercises
two anonymous reads with a repeat. Both run through the real executor and the real
loopback target, then through the independent scorer.
"""

import asyncio
import json
import sys
from pathlib import Path

from budget import cap
from fixture import encoded, sha
from grade import evaluate
from observe import install_observers

FLOWS = dict(
    chain=[
        dict(
            id="register",
            operation_id="openRegister",
            role="warden",
            path_parameters={"registerId": dict(value=31)},
        ),
        dict(
            id="entry",
            operation_id="readEntry",
            role="registrar",
            path_parameters={"entryId": dict(from_step="register", select="$.entries[0].entryId")},
        ),
        dict(id="dock", operation_id="checkDock", role=None),
    ],
    review=[
        dict(id="beacon", operation_id="readBeacon", role=None),
        dict(id="dock1", operation_id="checkDock", role=None),
        dict(id="dock2", operation_id="checkDock", role=None),
    ],
)


def rubric(name):
    """Frozen expectation for one deterministic flow; shared with the scorer preflight."""
    if name == "chain":
        return dict(
            steps=[
                dict(
                    operation="openRegister",
                    role="warden",
                    status=200,
                    values={"path_parameters": {"registerId": 31}},
                    fields=[],
                ),
                dict(
                    operation="readEntry",
                    role="registrar",
                    status=200,
                    values={"path_parameters": {"entryId": 58}},
                    fields=[dict(select="$.label", operator="equals", expected="draft")],
                ),
                dict(operation="checkDock", role=None, status=200, values={}, fields=[]),
            ],
            gaps=[],
            order=[0, 1, 2],
            bindings=[
                dict(
                    producer="openRegister",
                    consumer="readEntry",
                    source_role="warden",
                    role="registrar",
                    location="path_parameters",
                    name="entryId",
                    selector="$.entries[0].entryId",
                )
            ],
        )
    return dict(
        steps=[
            dict(operation="readBeacon", role=None, status=200, values={}, fields=[]),
            dict(operation="checkDock", role=None, status=200, values={}, fields=[]),
            dict(operation="checkDock", role=None, status=200, values={}, fields=[]),
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
    policies = {policy.operation_id: policy.id for policy in project.registry.definitions()}
    steps = [dict(step, policy_id=policies[step["operation_id"]]) for step in FLOWS[name]]
    scenario = Scenario.model_validate(
        dict(name="technical_fixture_preflight_" + name, steps=steps)
    )
    observations = folder / (arm + "-deterministic-observations-" + name)
    install_observers(observations, list(environ.values()))
    structure = [
        PlanStepEvidence(
            step_id=step.id,
            operation_id=step.operation_id,
            role=step.role,
            policy_id=step.policy_id,
            dependencies={
                location + "." + parameter: binding.from_step
                for location in ("path_parameters", "query", "body")
                for parameter, binding in getattr(step, location).items()
                if binding.from_step
            },
        )
        for step in scenario.steps
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
    assert report.outcome.value == "PASS", (report.reason, report.diagnostic)
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
            dict(
                result=result,
                report=report.model_dump(mode="json"),
                observations=observed,
                audit=audit,
                authority=authority,
                item=item,
                model_calls=0,
            ),
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
