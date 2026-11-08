"""One deterministic approved flow per arm, validating recorder and real HTTP binding evidence."""

import asyncio
import json
import sys
from pathlib import Path

from budget import cap
from fixture import encoded, sha
from grade import evaluate
from observe import install_observers


async def main(folder, arm):
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
    cfg = json.loads((folder / "configs.json").read_bytes())["flow"]
    environ = target_environment(folder / "private/model.env", {})
    project = cap(
        build_generic_project(
            "preflight_flow",
            cfg["document"],
            load_openapi(cfg["document"]),
            GenericSetup.model_validate(cfg["generic"]),
            environ=environ,
        )
    )
    policies = {p.operation_id: p.id for p in project.registry.definitions()}
    steps = [
        dict(
            id="index",
            operation_id="readIndex",
            role="owner",
            path_parameters={"indexId": dict(value=41)},
        ),
        dict(
            id="entry",
            operation_id="readEntry",
            role="reader",
            path_parameters={"entryId": dict(from_step="index", select="$.links[0].id")},
        ),
        dict(
            id="receipt",
            operation_id="readReceipt",
            role="owner",
            path_parameters={"code": dict(from_step="index", select="$.links[0].code")},
        ),
        dict(id="health", operation_id="readHealth", role=None),
    ]
    for step in steps:
        step["policy_id"] = policies[step["operation_id"]]
    scenario = Scenario.model_validate(dict(name="technical_fixture_preflight", steps=steps))
    observations = folder / (arm + "-deterministic-observations")
    install_observers(observations, list(environ.values()))
    structure = [
        PlanStepEvidence(
            step_id=s.id,
            operation_id=s.operation_id,
            role=s.role,
            policy_id=s.policy_id,
            dependencies={
                location + "." + name: binding.from_step
                for location in ("path_parameters", "query", "body")
                for name, binding in getattr(s, location).items()
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
    identifier = "_deterministic_" + arm + "_" + report.run_id
    (folder / "active.json").write_bytes(encoded(dict(id=identifier, config="flow")))
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
    authority = json.loads((folder / (arm + "-authority.json")).read_bytes())["flow"]
    expected = []
    for op, role, params, fields in [
        ("readIndex", "owner", {"indexId": 41}, []),
        ("readEntry", "reader", {"entryId": 73}, [("$.id", "equals", 73)]),
        ("readReceipt", "owner", {"code": "R73"}, [("$.code", "equals", "R73")]),
        ("readHealth", None, {}, []),
    ]:
        expected.append(
            dict(
                operation=op,
                role=role,
                status=200,
                values={"path_parameters": params} if params else {},
                fields=[dict(select=s, operator=o, expected=e) for s, o, e in fields],
            )
        )
    item = dict(
        rubric=dict(
            steps=expected,
            gaps=[],
            order=[0, 1, 2, 3],
            bindings=[
                dict(
                    producer="readIndex",
                    consumer=op,
                    source_role="owner",
                    role=role,
                    location="path_parameters",
                    name=name,
                    selector=selector,
                )
                for op, role, name, selector in [
                    ("readEntry", "reader", "entryId", "$.links[0].id"),
                    ("readReceipt", "owner", "code", "$.links[0].code"),
                ]
            ],
        )
    )
    result = evaluate(item, {}, [report.model_dump(mode="json")], authority, audit, observed)
    assert result["full_execution"] and result["evidence_integrity"], result
    with (folder / (arm + "-execution-preflight.json")).open(
        "x", encoding="utf-8", newline="\n"
    ) as f:
        json.dump(
            dict(
                result=result,
                report=report.model_dump(mode="json"),
                observations=observed,
                audit=audit,
                model_calls=0,
            ),
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(
        arm, "actual approved flow / integer+string bindings / independent scorer passed, 0 model"
    )


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve(), sys.argv[2]))
