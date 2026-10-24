"""New development cases: legal baselines only; variants never invoke a planner."""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

pytest.importorskip("langgraph")

from agenttest.goal_suite import build_suite, run_suite
from agenttest.negative_demo import create_negative_target
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.plan_reuse import LegalStructure
from agenttest.policies import NeedsReview
from agenttest.project_goals import load_goal_project
from agenttest.report import Outcome, ReportStore

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples/trusted-testing/development/validation/registered.project.json"
TEXT = "请测试新建记录接口"


class OnePlan(CatalogFixturePlanner):
    def __init__(self, invalid=False):
        super().__init__()
        self.calls, self.invalid = 0, invalid

    async def generate(self, context):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("A deterministic variant attempted a model call")
        raw = await super().generate(context)
        if self.invalid:
            from agenttest.dsl import Scenario

            scenario = json.loads(raw)
            scenario["steps"][0]["body"].pop("message")
            return Scenario.model_validate(scenario).model_dump_json()
        return raw


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["none", "accept_invalid", "wrong_error"])
async def test_single_legal_base_derives_without_models_or_assertion_changes(tmp_path, fault):
    p = load_goal_project(PROJECT)
    model = OnePlan()
    p.registration.budget.suite_max_model_calls = 1
    store, captured = ReportStore(tmp_path), {}
    result = await run_suite(p, TEXT, model, store, environ={},
        transport=httpx.ASGITransport(app=create_negative_target(fault)),
        on_validated_plan=lambda goal, plan: captured.update({
            goal.task.requirements.model_dump_json(): plan}))
    assert model.calls == result["model_calls"] == 1
    assert result["http_requests"] == 12 and len(result["cases"]) == 15
    assert result["budget_preview"]["required_planning_calls"] == 1
    assert result["budget_preview"]["derived_cases"] == 11
    reports = [store.read(row["run_id"]) for row in result["cases"] if row.get("run_id")]
    assert sum(r.planner_calls for r in reports) == 1
    derived = [r for r in reports if r.plan_derivation]
    assert len(derived) == 11 and len({r.run_id for r in reports}) == 12
    assert len({r.plan_derivation["base_plan_sha256"] for r in derived}) == 1
    assert all(r.plan_derivation["model_calls"] == 0
               and not r.plan_derivation["reused_execution_state"] for r in derived)
    negative = [r for r in reports if any(s.negative_test for s in r.steps)]
    assert len(negative) == 8
    assert all(r.outcome == (Outcome.PASS if fault == "none" else Outcome.ASSERTION_FAILED)
               for r in negative)
    # Three unapproved errors stay in the denominator even on the healthy target.
    assert not result["complete"]
    assert result["coverage"]["assertion_passed_scenarios"] == (12 if fault == "none" else 4)


@pytest.mark.asyncio
async def test_rejected_base_has_zero_http_and_keeps_all_variants(tmp_path):
    p, model = load_goal_project(PROJECT), OnePlan(invalid=True)
    result = await run_suite(p, TEXT, model, ReportStore(tmp_path), environ={},
        transport=httpx.MockTransport(lambda _: pytest.fail("Invalid base sent HTTP")))
    assert model.calls == 1 and result["http_requests"] == 0
    assert result["coverage"]["scenario_denominator"] == 15
    assert result["coverage"]["not_executed"] == 15
    assert sum(c["reason"] == "LEGAL_BASE_PLAN_REJECTED" for c in result["cases"]) == 11
    assert not result["complete"]


@pytest.mark.asyncio
async def test_shared_request_budget_is_not_reset_for_derived_cases(tmp_path):
    p = load_goal_project(PROJECT)
    p.target.max_requests = 4
    model = OnePlan()
    result = await run_suite(p, TEXT, model, ReportStore(tmp_path), environ={},
        transport=httpx.ASGITransport(app=create_negative_target()))
    assert result["http_requests"] == 4 and model.calls == 1
    assert result["coverage"]["scenario_denominator"] == 15
    assert result["coverage"]["not_executed"] == 11


def test_explicit_literals_are_not_mutation_capabilities():
    p = load_goal_project(PROJECT)
    suite = build_suite(p, '请测试新建记录“限定输入”接口')
    ready = [c for c in suite.cases if c.goal and c.goal.task]
    assert len(ready) == 7 and not ready[0].is_derived
    assert all(next(a.value for a in c.goal.task.requirements.steps[0].arguments
                    if a.name == "message") == "限定输入" for c in ready)
    assert all(c.reason in {"EXPLICIT_INPUT_VALUE_FIXED", "EXPECTED_ERROR_BEHAVIOR_NOT_APPROVED"}
               for c in suite.cases if not c.goal)


@pytest.mark.asyncio
async def test_immutable_structure_binds_config_and_rejects_arbitrary_literals(tmp_path):
    p, saved = load_goal_project(PROJECT), []
    store = ReportStore(tmp_path)
    result = await run_suite(p, TEXT, OnePlan(), store, environ={},
        transport=httpx.ASGITransport(app=create_negative_target()),
        on_validated_plan=lambda goal, plan: saved.append((goal, plan)))
    base, plan = saved[0]
    structure = LegalStructure.capture(p, base, plan, store.read(result["cases"][0]["run_id"]), {})
    candidate = deepcopy(saved[-1][0])
    mutated_project = deepcopy(p)
    mutated_project.target.max_requests -= 1
    with pytest.raises(NeedsReview, match="PLAN_REUSE_BINDING_MISMATCH"):
        structure.derive(mutated_project, base, candidate, {})
    # Ordinary approved values still cannot masquerade as declared boundary/enum variants.
    candidate = deepcopy(base)
    candidate.task.requirements.steps[0].arguments[0].value = "other"
    with pytest.raises(NeedsReview, match="PLAN_REUSE_VARIATION_NOT_DECLARED"):
        structure.derive(p, base, candidate, {})
    candidate = deepcopy(base)
    candidate.task.requirements.steps[0].role = "unapproved"
    with pytest.raises(NeedsReview, match="PLAN_REUSE_ROLE_OR_OPERATION_CHANGED"):
        structure.derive(p, base, candidate, {})
    candidate = deepcopy(base)
    candidate.task.requirements.steps[0].policy_id = "invented_state"
    with pytest.raises(NeedsReview, match="PLAN_REUSE_POLICY_NOT_DERIVABLE"):
        structure.derive(p, base, candidate, {})


@pytest.mark.asyncio
async def test_multi_producer_negatives_produce_fresh_resources_for_each_run(tmp_path):
    import runpy

    folder = ROOT / "examples/trusted-testing/laboratory"
    p = load_goal_project(folder / "registered.project.json")
    app, env = runpy.run_path(str(folder / "fixture.py"))["runtime"]()
    observations, current = [], []

    class Observed(httpx.ASGITransport):
        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            body = await response.aread()
            current.append((request.method, request.url.path, json.loads(body)))
            return httpx.Response(response.status_code, headers=response.headers, content=body)

    def report(goal, result):
        observations.append((goal.task.requirements.steps[-1].policy_id,
                             list(current), result))
        current.clear()

    result = await run_suite(p, "请测试所有已授权接口", CatalogFixturePlanner(),
        ReportStore(tmp_path), environ=env, transport=Observed(app=app), on_report=report)
    assert result["complete"] and result["model_calls"] == 5
    assert result["http_requests"] == 42
    producers = []
    for policy, requests, report in observations:
        if policy == "lab_place_v1" or policy.startswith("place_"):
            assert len(requests) == 3
            sample = next(body["sample"]["key"] for _, path, body in requests
                          if path == "/lab/specimens")
            tray = next(body["tray"]["key"] for _, path, body in requests if path == "/lab/trays")
            assert requests[-1][1] == f"/lab/trays/{tray}/specimens/{sample}"
            producers.append((sample, tray))
            assert report.outcome == Outcome.PASS
    assert len(producers) == len(set(producers)) == 6


@pytest.mark.asyncio
async def test_flow_endpoint_derives_approved_negatives_without_replanning(tmp_path):
    from agenttest.portability_demo import project_inputs

    folder = ROOT / "examples/trusted-testing/projects/tickets"
    p = load_goal_project(folder / "registered.project.json")
    app, _, _, _, env = project_inputs("tickets")
    model = OnePlan()
    result = await run_suite(p, "请验证创建、关闭工单流程", model, ReportStore(tmp_path),
        environ=env, transport=httpx.ASGITransport(app=app))
    assert result["complete"] and model.calls == 1
    assert result["scope"] == "flow" and result["http_requests"] == 10
    assert len(result["cases"]) == 5
    assert result["coverage"]["derived_executed"] == 4
    assert sum(c["negative_rejections_passed"] for c in result["cases"]) == 4


@pytest.mark.asyncio
async def test_quoted_producer_value_is_kept_when_another_flow_field_varies(tmp_path):
    from agenttest.portability_demo import project_inputs

    folder = ROOT / "examples/trusted-testing/projects/tickets"
    p = load_goal_project(folder / "registered.project.json")
    app, _, _, _, env = project_inputs("tickets")
    bodies = []

    class Observed(httpx.ASGITransport):
        async def handle_async_request(self, request):
            if request.url.path == "/v2/tickets":
                bodies.append(json.loads(await request.aread()))
            return await super().handle_async_request(request)

    result = await run_suite(p, '请创建工单“固定工单”，然后关闭', OnePlan(), ReportStore(tmp_path),
        environ=env, transport=Observed(app=app))
    assert result["complete"] and result["coverage"]["derived_executed"] == 4
    assert len(bodies) == 5 and all(b["subject"] == "固定工单" for b in bodies)


@pytest.mark.asyncio
async def test_failed_producer_stops_its_dependents_without_old_resource_reuse(tmp_path):
    import runpy

    folder = ROOT / "examples/trusted-testing/laboratory"
    p = load_goal_project(folder / "registered.project.json")
    app, env = runpy.run_path(str(folder / "fixture.py"))["runtime"]()
    seen = []
    inner = httpx.ASGITransport(app=app)

    class FailedProducer(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(request.url.path)
            if request.url.path == "/lab/trays":
                return httpx.Response(503, json={"code": "TEMP_UNAVAILABLE"})
            return await inner.handle_async_request(request)

    result = await run_suite(p, "请验证装载接口", CatalogFixturePlanner(), ReportStore(tmp_path),
        environ=env, transport=FailedProducer())
    assert not result["complete"]
    assert seen and not any("/specimens/" in path for path in seen)
    assert result["coverage"]["not_executed"] >= 0
