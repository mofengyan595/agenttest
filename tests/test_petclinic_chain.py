"""A third-party contract's anonymous list/detail flow, with offline adversarial evidence."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.discovery import discover
from agenttest.dsl import ArgumentBinding
from agenttest.eval_v2.scoring import capture_plan, score_intent
from agenttest.executor import Executor
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerDraft
from agenttest.planner_input import planning_context
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract, task_validator

ROOT = PROJECT_ROOT / "examples/portability"
CHAIN = ROOT / "petclinic-chain"


def inputs(name):
    document = (ROOT / "petclinic/openapi.json").read_bytes()
    target = TargetConfig.model_validate_json((CHAIN / "target.json").read_bytes())
    registry = PolicyRegistry.from_json((CHAIN / "policies.json").read_bytes())
    task = TaskContract.model_validate_json((CHAIN / f"{name}.task.json").read_bytes())
    draft = PlannerDraft.model_validate_json((CHAIN / f"{name}.draft.json").read_bytes())
    return document, target, registry, task, draft


@pytest.mark.parametrize("name,index", [("first", 0), ("sixth", 5)])
def test_bound_two_interface_inputs_have_explicit_anonymous_flow_and_no_literal_id(name, index):
    document, target, registry, task, draft = inputs(name)
    contract, plan = load_openapi(document), draft.scenario()
    task_validator(task, contract, target, registry)(task.user_task, plan)
    Executor(target, contract, environ={}, registry=registry).validate(plan)
    assert target.roles == {} and not target.allow_mutations
    assert target.max_requests == target.max_steps == 2
    assert all(s.role is None for s in plan.steps)
    assert plan.steps[1].path_parameters["petTypeId"].from_step == plan.steps[0].id
    assert plan.steps[1].path_parameters["petTypeId"].select == f"$[{index}].id"
    assert plan.model_dump() == json.loads((CHAIN / f"{name}.plan.json").read_bytes())
    context = planning_context(contract, target, registry, task.user_task, {})
    adapter = OpenAICompatiblePlanner(ModelSettings(model="offline-chain",
        base_url="https://model.invalid/v1", api_key="synthetic-chain-key"), profile="generic")
    payload = json.loads(adapter.prepare_request(context))
    public = json.loads(payload["messages"][1]["content"])
    assert {op["id"] for op in public["operations"]} == {"listPetTypes", "getPetType"}
    assert public["roles"] == public["authentication"] == []
    assert not {"requirements", "expected", "rubric"} & public.keys()
    result = discover(contract, registry, target)
    flows = [c for c in result.candidates if c.scenario_kind == "resource_flow"]
    assert flows and all(c.policy_match for c in flows)
    assert all(c.dependencies[0].role_relation == "anonymous" for c in flows)
    assert all("DEPENDENCY_ROLE_EVIDENCE_MISSING" not in c.missing_evidence for c in flows)


@pytest.mark.parametrize("mutation", ["literal", "selector", "source_policy", "missing_list"])
def test_wrong_resource_flow_is_blocked_before_any_http(mutation):
    document, target, registry, task, draft = inputs("first")
    contract, plan = load_openapi(document), draft.scenario()
    if mutation == "literal":
        plan.steps[1].path_parameters["petTypeId"] = ArgumentBinding(value=1)
    elif mutation == "selector":
        plan.steps[1].path_parameters["petTypeId"].select = "$[5].id"
    elif mutation == "source_policy":
        plan.steps[0].policy_id = "unapproved"
    else:
        plan.steps.pop(0)
    with pytest.raises(NeedsReview, match="INTENT_TASK_CONTRACT_PLAN_MISMATCH"):
        task_validator(task, contract, target, registry)(task.user_task, plan)
    with pytest.raises(ValueError):
        Executor(target, contract, environ={}, registry=registry).validate(plan)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,index,fault", [
    ("first", 0, "none"), ("sixth", 5, "none"),
    ("first", 0, "wrong_detail_id"), ("sixth", 5, "wrong_detail_id"),
    ("first", 0, "empty_list"),
])
async def test_same_generic_planner_executes_dynamic_ids_and_detects_faults(
        tmp_path, name, index, fault):
    pytest.importorskip("langgraph", reason="Full generic flow requires the agent extra")
    from agenttest.workflow import Workflow

    document, target, registry, task, draft = inputs(name)
    # Deliberately shuffled IDs: neither 1 nor 6 appears. The planner cannot guess the ID.
    listing = [{"id": ident, "name": f"synthetic{ident}"} for ident in (41, 9, 22, 87, 61, 50)]
    requests, model_requests = [], []

    def model_response(request):
        model_requests.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": draft.model_dump_json()}}], "usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    def business_response(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/petclinic/api/pettypes":
            return httpx.Response(200, json=[] if fault == "empty_list" else listing)
        assert request.url.path == f"/petclinic/api/pettypes/{listing[index]['id']}"
        detail = dict(listing[index])
        if fault == "wrong_detail_id":
            detail["id"] += 100
        return httpx.Response(200, json=detail)

    planner = OpenAICompatiblePlanner(ModelSettings(model="offline-chain",
        base_url="https://model.invalid/v1", api_key="synthetic-chain-key"),
        httpx.MockTransport(model_response), profile="generic")
    store = ReportStore(tmp_path / "reports")
    report = await Workflow(document, target, registry, planner, store,
        httpx.MockTransport(business_response), {}, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(document), target, registry)).run()
    assert len(model_requests) == report.model_call.calls == 1
    assert report.model_call.total_tokens == 150  # SIMULATED; no live model request.
    assert report.completed and report.workflow_completed and report.intent_verified
    assert report == store.read(report.run_id)
    assert score_intent(task.requirements, capture_plan(draft.scenario())).passed
    assert report.outcome == (Outcome.PASS if fault == "none" else Outcome.ASSERTION_FAILED)
    assert report.task_satisfied == (fault == "none")
    assert len(requests) == (1 if fault == "empty_list" else 2)
    if fault != "none":
        assert any(not c.passed and c.kind == ("count" if fault == "empty_list" else "equals")
                   for s in report.steps for c in s.checks)
