"""Whole-task read limits and sensitive resource journals are safety boundaries."""

import json
from uuid import uuid4

import httpx
import pytest
from test_product_probe import SelectRead
from test_product_tasks import finished, response, service
from test_resource_lifecycle import executor, lifecycle_project, plan

from agenttest.dsl import ArgumentBinding
from agenttest.fixed_plan import replay_scope
from agenttest.generic_intake import GenericSetup, IntakeOperation
from agenttest.policies import BindingRule, PolicyRegistry
from agenttest.project_intake import IntakeInput
from agenttest.report import Outcome
from agenttest.saved_tests import SavedTests


@pytest.mark.asyncio
async def test_sensitive_resource_identifier_never_enters_journal_or_cleanup(tmp_path):
    project, _ = lifecycle_project()
    project.target.sensitive_fields = ["id"]
    secret, requests = "synthetic_private_resource_identifier", []

    def target(request):
        requests.append(request.method)
        return httpx.Response(201, json=dict(id=secret))

    report = await executor(tmp_path, target, project).run(plan())
    assert report.outcome == Outcome.TOOL_ERROR
    assert requests == ["POST"]
    assert report.resource_lifecycle[0]["status"] == "RESOURCE_ID_UNVERIFIED"
    assert report.resource_lifecycle[0]["resource_id"] is None
    assert secret not in report.model_dump_json()
    assert all(secret not in path.read_text(encoding="utf-8")
               for path in tmp_path.glob("*/report.json"))


@pytest.mark.asyncio
async def test_cleanup_rejected_before_send_is_recorded_as_not_sent(tmp_path):
    project, _ = lifecycle_project()
    project.target.max_requests = 1
    requests = []

    def target(request):
        requests.append(request.method)
        return httpx.Response(201, json=dict(id="only_this_run"))

    report = await executor(tmp_path, target, project).run(plan())
    assert requests == ["POST"] and report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.resource_lifecycle[0]["status"] == "CLEANUP_PENDING"
    assert report.resource_lifecycle[0]["cleanup_result"] == "NOT_SENT_PRECHECK_REJECTED"
    assert report.lifecycle_cleanup[0].attempts == []


def test_write_regression_cannot_depend_on_an_approved_but_historical_read(tmp_path):
    project, _ = lifecycle_project()
    definitions = project.registry.definitions()
    read = next(p for p in definitions if p.operation_id == "readJob")
    read.binding_rules = [BindingRule(location="path_parameters", name="jobId",
        literal_schema=dict(type="string", enum=["historical"]))]
    project.registry = PolicyRegistry(definitions)
    scenario = plan()
    scenario.steps[1].path_parameters["jobId"] = ArgumentBinding(value="historical")
    run = executor(tmp_path, lambda _: pytest.fail("Eligibility sends no HTTP"), project)
    run.validate(scenario)  # Operator approval alone does not prove fresh test preparation.
    assert replay_scope(project.contract, project.target, scenario,
                        project.registry) == "MUTATION_LIFECYCLE_REQUIRED"
    assert "未在本轮准备" in SavedTests.lifecycle_reason(project, scenario)


@pytest.mark.asyncio
async def test_investigation_limit_covers_the_whole_task(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="Independent reads", version="1"),
        paths={f"/status/{n}": {"get": dict(operationId=f"read{n}", responses={
            "200": dict(description="ok")})} for n in range(3)}))
    cases = [dict(title=f"检查{n}", kind="normal", plan=dict(name=f"read{n}", steps=[dict(
        id=f"read{n}", operation_id=f"read{n}", role=None, policy_id=f"web_rule_{n + 1}",
        arguments=[])])) for n in range(3)]
    proposal = dict(interpretation="三个独立只读检查", relevant=True, cases=cases,
                    questions=[], not_covered=[])
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda _: httpx.Response(503, json={})))
    svc = service(tmp_path, lambda _: response(proposal))
    svc.product_probe_factory = lambda _: SelectRead()
    try:
        setup = GenericSetup(base_url="http://127.0.0.1:18998", isolated_confirmed=True,
            assisted=True, operations=[IntakeOperation(operation_id=f"read{n}", alias=f"状态{n}",
                                                      statuses=[200]) for n in range(3)])
        project = svc.intake.save(IntakeInput(name="任务调查上限", document=document,
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", project["id"], "", uuid4().hex)
        row = await finished(svc, row)
        cases = row["summary"]["cases"]
        assert len(cases) == 3 and all(c["outcome"] == "ASSERTION_FAILED" for c in cases)
        assert sum(i["requests"] for c in cases for i in c.get("investigations", [])) == 2
        assert all(c.get("failure_explanation", {}).get("next_action") for c in cases)
    finally:
        await svc.close()
