"""State checks cannot run before an approved write merely because no ID is consumed."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.goal_derivation import derive_goal
from agenttest.product_design import design_groups
from agenttest.report import Outcome, ReportStore


def project():
    raw = json.dumps(dict(openapi="3.0.3", info=dict(title="Disposable records", version="1"),
        paths={"/records/current": {"delete": dict(operationId="clearRecords", responses={
            "204": dict(description="cleared")})}, "/records": {"get": dict(
                operationId="listRecords", responses={"200": dict(description="ok")})}}))
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998",
        isolated_confirmed=True, allow_mutations=True, assisted=True, operations=[dict(
            operation_id="clearRecords", alias="清除本轮记录", statuses=[204]), dict(
                operation_id="listRecords", alias="确认清除后为空", statuses=[200],
                after_operations=["clearRecords"], checks=[dict(select="$.total", expected=0)])]))
    return build_generic_project("records", raw, load_openapi(raw), setup), setup


@pytest.mark.asyncio
async def test_missing_or_late_state_prerequisite_stops_all_http_but_valid_order_executes(tmp_path):
    proj, _ = project()
    policies = {p.operation_id: p.id for p in proj.registry.definitions()}
    steps = {key: dict(id=key, operation_id=key, policy_id=value)
             for key, value in policies.items()}
    calls = []

    def target(request):
        calls.append(request.method)
        return httpx.Response(204) if request.method == "DELETE" else httpx.Response(
            200, json=dict(total=0))

    executor = Executor(proj.target, proj.contract, ReportStore(tmp_path),
        registry=proj.registry, environ={}, transport=httpx.MockTransport(target))
    for names in (["listRecords"], ["listRecords", "clearRecords"]):
        report = await executor.run(Scenario.model_validate(dict(name="wrong order",
            steps=[steps[key] for key in names])))
        assert report.outcome == Outcome.NEEDS_REVIEW
        assert "DEPENDENCY_PREREQUISITE_NOT_COMPLETED" in str(report.model_dump())
        assert calls == []
    report = await executor.run(Scenario.model_validate(dict(name="valid order",
        steps=[steps[key] for key in ["clearRecords", "listRecords"]])))
    assert report.outcome == Outcome.PASS
    assert calls == ["DELETE", "GET"]


def test_closure_and_deterministic_requirements_include_state_prerequisite():
    proj, _ = project()
    group = design_groups(proj, operation_ids=["listRecords"])[0]
    assert group["primary"] == ["listRecords"]
    assert set(group["operations"]) == {"clearRecords", "listRecords"}
    derived = derive_goal(proj, "确认清除后为空")
    assert derived.task is not None
    steps = derived.task.requirements.steps
    assert [s.operation_id for s in steps] == ["clearRecords", "listRecords"]
    assert (steps[0].key, steps[1].key) in derived.task.requirements.before


def test_state_dependency_cycles_or_unselected_prerequisites_cannot_be_ready():
    from agenttest.project_inventory import inventory

    proj, setup = project()
    setup.operations[0].after_operations = ["listRecords"]
    current = inventory(proj.contract, setup, {})
    assert current["counts"]["READY"] == 0
    with pytest.raises(ValueError):
        build_generic_project("cycle", proj.document.decode(), proj.contract, setup, environ={})
    setup.operations[0].after_operations = []
    setup.operations[0].enabled = False
    states = {e["operation_id"]: e["state"]
              for e in inventory(proj.contract, setup, {})["operations"]}
    assert states["listRecords"] == "NEEDS_INPUT"
