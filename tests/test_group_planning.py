"""Group scope is public configuration; model proposals still face independent local gates."""

import json
from copy import deepcopy

import httpx
import pytest
from test_goal_suite import SyntheticPlanner, _project

from agenttest.feedback import FakeFeedback
from agenttest.goal_derivation import derive_goal
from agenttest.goal_runtime import GoalPlanner
from agenttest.goal_suite import SuiteLedger, build_suite, run_suite
from agenttest.nl_entry_evaluation import prepare_profiles
from agenttest.planner_input import planning_context
from agenttest.portability_demo import project_inputs
from agenttest.project_goals import load_goal_project
from agenttest.report import ReportStore


class ObservedPlanner(SyntheticPlanner):
    def __init__(self):
        super().__init__()
        self.contexts = []

    async def generate(self, context):
        self.contexts.append(deepcopy(context))
        return await super().generate(context)


@pytest.mark.asyncio
async def test_group_context_contains_only_selected_policies_roles_and_dependencies():
    from agenttest.dsl import Scenario

    class CaptureOnly:
        contexts = []

        async def generate(self, context):
            self.contexts.append(deepcopy(context))
            return "{}"

    p = deepcopy(_project())
    goal = derive_goal(p, "关闭、查询工单流程")
    inner = CaptureOnly()
    context = planning_context(p.contract, p.target, p.registry, goal.task.user_task,
                               Scenario.model_json_schema())
    await GoalPlanner(inner, p, goal).generate(context)
    public = inner.contexts[0]
    assert {x["id"] for x in public["policies"]} == {
        "ticket_open_v1", "ticket_close_v1", "ticket_closed_read_v1"}
    assert {x["id"] for x in public["operations"]} == {
        "openTicket", "closeTicket", "readTicket"}
    assert public["roles"] == ["owner"]
    assert all(x["roles"] == ["owner"] for x in public["policies"])
    assert "listTickets" not in json.dumps(public)
    assert "ticket_read_v1" not in json.dumps(public)
    assert "requirements" not in public and "required_plan" not in public
    assert "reference_plan" not in public and "rubric" not in public


@pytest.mark.asyncio
async def test_sufficient_frozen_budget_covers_all_groups_without_inserting_model_steps(tmp_path):
    pytest.importorskip("langgraph")
    p = deepcopy(_project())
    p.target.max_requests = 12
    p.registration.budget.suite_max_model_calls = 6
    p.registration.budget.suite_max_total_tokens = 500_000
    suite = build_suite(p, "测试这个项目已授权的核心接口")
    preview = suite.preview()["budget_preview"]
    assert preview["required_business_requests"] == 11
    assert preview["required_planning_calls"] == 6
    assert preview["complete_project_within_budget"]
    assert preview["optional_readonly_requests"] == 1
    app, _, _, _, env = project_inputs("tickets")
    planner = ObservedPlanner()
    result = await run_suite(p, "测试这个项目已授权的核心接口", planner, ReportStore(tmp_path),
        environ=env, transport=httpx.ASGITransport(app=app))
    assert result["complete"] and result["http_requests"] == 11
    assert result["model_calls"] == 6 and len(planner.contexts) == 6
    assert result["coverage"]["planned_scenarios"] == 6
    assert result["coverage"]["executed_scenarios"] == 6
    assert result["coverage"]["executed_operations"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["requests", "models", "prior_entry_call", "tokens"])
async def test_insufficient_shared_budget_keeps_full_denominators(tmp_path, constraint):
    pytest.importorskip("langgraph")
    p = deepcopy(_project())
    p.target.max_requests = 12
    p.registration.budget.suite_max_model_calls = 6
    p.registration.budget.suite_max_total_tokens = 500_000
    ledger = SuiteLedger(12, 6, 500_000)
    if constraint == "requests":
        p.target.max_requests = 10
    elif constraint == "models":
        p.registration.budget.suite_max_model_calls = 5
    elif constraint == "prior_entry_call":
        ledger.model_calls = 1
        ledger.known_tokens = 200
    else:
        ledger.known_tokens = ledger.max_total_tokens
    app, _, _, _, env = project_inputs("tickets")
    planner = ObservedPlanner()
    if constraint == "tokens":
        planner.token_reservation = 1
    result = await run_suite(p, "测试这个项目已授权的核心接口", planner, ReportStore(tmp_path),
        environ=env, transport=httpx.ASGITransport(app=app), ledger=ledger)
    assert not result["complete"]
    assert result["coverage"]["scenario_denominator"] == 6
    assert result["coverage"]["operation_denominator"] == 4
    assert result["usage"]["requests"] <= p.target.max_requests
    assert result["usage"]["model_calls"] <= p.registration.budget.suite_max_model_calls
    assert any(c["status"] == "NOT_EXECUTED" for c in result["cases"])
    if constraint == "prior_entry_call":
        assert result["usage"]["model_calls"] == planner.calls + 1
    if constraint == "tokens":
        assert result["model_calls"] == result["http_requests"] == 0


@pytest.mark.asyncio
async def test_optional_feedback_cannot_consume_later_required_group_budget(tmp_path):
    pytest.importorskip("langgraph")
    prepare_profiles(tmp_path / "projects")
    p = load_goal_project(tmp_path / "projects/tickets/registered.project.json")
    p.target.max_requests = 12
    p.registration.budget.suite_max_model_calls = 9
    p.registration.budget.suite_max_total_tokens = 500_000
    app, _, _, _, env = project_inputs("tickets", "owner_leak")
    result = await run_suite(p, "测试这个项目已授权的核心接口", ObservedPlanner(),
        ReportStore(tmp_path / "reports"), environ=env,
        transport=httpx.ASGITransport(app=app), analyzer=FakeFeedback())
    assert result["cases"][-1]["task_satisfied"] is True
    assert result["coverage"]["executed_scenarios"] == 6
    assert result["http_requests"] == 12 and result["model_calls"] <= 9
    failed = [c for c in result["cases"] if c.get("outcome") == "ASSERTION_FAILED"]
    assert len(failed) == 1 and not result["complete"]


@pytest.mark.asyncio
async def test_extra_model_step_is_retained_rejected_and_excluded_from_valid_coverage(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.dsl import Scenario
    from agenttest.workflow import FakeLLM

    p = deepcopy(_project())
    generated = SyntheticPlanner()
    raw = await generated.generate(dict(requested_goals=["read"], synthetic_input_defaults=[
        dict(policy_id="ticket_open_v1", name="subject", value="a"),
        dict(policy_id="ticket_open_v1", name="priority", value="low")]))
    plan = Scenario.model_validate_json(raw)
    from agenttest.dsl import Step

    plan.steps.append(Step(id="unrequested", operation_id="listTickets", role="owner",
                           policy_id="ticket_list_v1"))
    app, _, _, _, env = project_inputs("tickets")
    store = ReportStore(tmp_path)
    # Flow scope has one group and no automatic Schema variations.
    result = await run_suite(p, "创建、查询工单流程", FakeLLM(plan.model_dump_json()), store,
        environ=env, transport=httpx.ASGITransport(app=app))
    assert result["model_calls"] == 1 and result["http_requests"] == 0
    assert result["coverage"]["proposed_operations"] == 3
    assert result["coverage"]["planned_operations"] == 0
    assert result["coverage"]["planned_scenarios"] == 0
    report = store.read(result["cases"][0]["run_id"])
    assert len(report.plan_structure) == 3
    assert report.diagnostic.code == "INTENT_TASK_CONTRACT_PLAN_MISMATCH"
