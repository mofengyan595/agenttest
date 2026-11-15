import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.negative_post_observation import observation_receipts
from agenttest.product_design import DesignCase, validate_design_case
from agenttest.product_fingerprint import scenario_fingerprint
from agenttest.product_obligations import approved_project_scope
from agenttest.product_schema import encoded_literal
from agenttest.report import ReportStore, RunReport
from agenttest.write_effects import CREATION_UNPROVEN, NO_SIDE_EFFECT_PROVEN, for_case


@pytest.mark.asyncio
async def test_isolated_observation_counts_only_correlated_actual_requests(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from prepare_colleague_workspace import prepare
    from serve_colleague_targets import create_target

    receipt = await prepare(tmp_path, 18000, {}, observe_negative_posts=True)
    identifier = receipt["projects"]["parcels"]
    database = tmp_path / "workspace" / "project-intake.sqlite3"
    with sqlite3.connect(database) as connection:
        record = json.loads(connection.execute(
            "SELECT payload FROM projects WHERE id=?", (identifier,)).fetchone()[0])
    specification = record["document"]
    project = build_generic_project(identifier, specification, load_openapi(specification),
                                    GenericSetup.model_validate(record["generic"]), (), {})
    assert len(approved_project_scope(project)) == 31
    policies = {p.id: p for p in project.registry.definitions()}
    negative = next(p for p in policies.values() if p.operation_id == "createParcel"
                    and p.negative and p.negative.name == "quantity")
    body = {"name": "test", "quantity": 2, "meta": {
        "labels": ["blue"], "options": {"tracked": True}}}
    proposed = DesignCase.model_validate(dict(title="test", kind="negative", plan=dict(
        name="check", steps=[dict(id="submit", operation_id="createParcel", role=None,
        policy_id=negative.id, arguments=[
            *(dict(location="body", name=name, value=encoded_literal(value),
                   from_step=None, select=None)
              for name, value in body.items()),
            dict(location="headers", name="X-Lab-Correlation", value="a" * 32,
                 from_step=None, select=None)])])))
    events = []
    app = create_target("parcels", environ={}, observe_negative_posts=True)

    async def send(request):
        events.append(request.method)
        return await httpx.ASGITransport(app=app).handle_async_request(request)

    transport = httpx.MockTransport(send)
    store = ReportStore(tmp_path / "reports")
    scenario = validate_design_case(project, proposed, store, environ={})
    assert scenario_fingerprint(project, scenario) == scenario_fingerprint(
        project, validate_design_case(project, proposed, store, environ={}))
    report = await Executor(project.target, project.contract, store, transport, {},
        registry=project.registry).run(scenario, report=RunReport(
            scenario_sha256="", contract_sha256="", task_mode="model_designed",
            environment="isolated", target_origin=project.target.base_url))
    case = dict(evidence=[dict(step_id=result.step_id, operation_id=result.operation_id,
        method=result.method, policy_id=planned.policy_id, role=planned.role,
        outcome=result.outcome.value, executed=bool(result.attempts),
        attempt=result.attempts[-1].model_dump(), request=result.request_evidence,
        checks=[check.model_dump() for check in result.checks])
        for result, planned in zip(report.steps, scenario.steps, strict=True)])
    assert events == ["GET", "POST", "GET"]
    assert [step.attempts[-1].status_code for step in report.steps] == [200, 422, 200]
    assert report.policy_authorized and report.outcome.value == "PASS"
    case["negative_post_observations"] = observation_receipts(project, scenario, case)
    assert for_case(case, policies)[0]["status"] == NO_SIDE_EFFECT_PROVEN

    count = next(check for check in case["evidence"][-1]["checks"]
                 if check["selector"] == "$.count")
    count["passed"] = False
    case["negative_post_observations"] = observation_receipts(project, scenario, case)
    assert for_case(case, policies)[0]["status"] == CREATION_UNPROVEN
    count["passed"] = True

    case["evidence"][-1]["request"]["values"]["query"]["key"] = "other"
    case["negative_post_observations"] = observation_receipts(project, scenario, case)
    assert for_case(case, policies)[0]["status"] == CREATION_UNPROVEN
