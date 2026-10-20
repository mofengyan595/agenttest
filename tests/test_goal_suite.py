"""Independent synthetic replies exercise shared budgets, coverage and goal completeness."""

import json
from copy import deepcopy
from functools import lru_cache

import httpx
import pytest

from agenttest.dsl import Scenario
from agenttest.goal_suite import BudgetPlanner, SuiteLedger, build_suite, run_suite
from agenttest.llm_planner import PlannerFailure
from agenttest.portability_demo import EXAMPLES, project_inputs
from agenttest.project_goals import load_goal_project
from agenttest.report import ModelCallStats, ReportStore


@lru_cache
def _project():
    return load_goal_project(EXAMPLES / "tickets/registered.project.json")


class SyntheticPlanner:
    """Test-only policy fixtures; product runtime does not contain these plans."""

    def __init__(self, unknown=False):
        self.calls = 0
        self.unknown = unknown
        self.stats = ModelCallStats()

    async def generate(self, context):
        self.calls += 1
        self.stats = ModelCallStats(calls=1, total_tokens=None if self.unknown else 100)
        goals = context["requested_goals"]
        values = {(d["policy_id"], d["name"]): d["value"]
                  for d in context["synthetic_input_defaults"]}
        steps = []
        if goals != ["list"]:
            steps.append(dict(id="producer", operation_id="openTicket", role="owner",
                policy_id="ticket_open_v1", body={key: {"value": values[("ticket_open_v1", key)]}
                    for key in ("subject", "priority")}))
        binding = {"ticket_key": {"from_step": "producer", "select": "$.payload.ticket_key"}}
        for goal in goals:
            if goal == "close":
                steps.append(dict(id="closed", operation_id="closeTicket", role="owner",
                    policy_id="ticket_close_v1", path_parameters=binding,
                    body={"state": {"value": "closed"}}))
            elif goal == "read":
                steps.append(dict(id="read", operation_id="readTicket", role="owner",
                    policy_id="ticket_closed_read_v1" if "close" in goals else "ticket_read_v1",
                    path_parameters=binding))
            elif goal == "ownership":
                steps.append(dict(id="denied", operation_id="readTicket", role="visitor",
                    policy_id="ticket_denied_v1", path_parameters=binding))
            elif goal == "list":
                steps.append(dict(id="list", operation_id="listTickets", role="owner",
                                  policy_id="ticket_list_v1"))
        return Scenario.model_validate(dict(name="independent synthetic reply", steps=steps)
                                       ).model_dump_json()


def test_single_generates_schema_candidates_and_never_guesses_error_status():
    suite = build_suite(_project(), "测试创建工单接口")
    assert suite.scope == "single" and suite.discovery["candidates"] > 0
    assert len([c for c in suite.cases if c.goal and c.goal.task]) == 3
    kinds = {k.split(":")[0] for c in suite.cases for k in c.kinds}
    assert {"normal", "required_missing", "type_mismatch", "enum_boundary",
            "string_min_length", "string_max_length"} <= kinds
    negatives = [c for c in suite.cases if c.goal is None]
    assert negatives and all(c.reason == "EXPECTED_ERROR_BEHAVIOR_NOT_APPROVED" for c in negatives)
    preview = suite.preview()
    assert preview["http_requests"] == preview["model_calls"] == 0
    assert preview["coverage"]["scenario_denominator"] == len(suite.cases)


def test_project_groups_are_catalog_derived_and_do_not_include_task_answers():
    suite = build_suite(_project(), "测试这个项目已授权的核心接口")
    assert suite.scope == "project" and len(suite.cases) == 6
    assert suite.operation_ids == ["closeTicket", "listTickets", "openTicket", "readTicket"]
    assert len({c.id for c in suite.cases}) == 6
    assert all(c.goal.task.user_task == "测试这个项目已授权的核心接口" for c in suite.cases)
    assert "requirements" not in json.dumps(suite.preview())


def test_preview_credentials_are_separate_from_static_candidates():
    suite = build_suite(_project(), "测试这个项目已授权的核心接口", environ={})
    assert all(c.preview()["status"] == "NEEDS_REVIEW" for c in suite.cases)
    assert all(c.reason == "PROJECT_CREDENTIAL_PREFLIGHT_FAILED" for c in suite.cases)
    assert suite.discovery["candidate_operations"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["single", "flow", "project"])
async def test_all_three_scopes_use_workflow_and_aggregate_budget(tmp_path, scope):
    pytest.importorskip("langgraph")
    p = deepcopy(_project())
    app, _, _, _, env = project_inputs("tickets")
    texts = dict(single="测试创建工单接口", flow="验证创建、关闭、查询，以及其他用户不能读取的流程",
                 project="测试这个项目已授权的核心接口")
    planner = SyntheticPlanner()
    result = await run_suite(p, texts[scope], planner, ReportStore(tmp_path), environ=env,
                             transport=httpx.ASGITransport(app=app))
    assert result["usage"]["requests"] <= 10 and planner.calls <= 8
    assert result["coverage"]["operation_denominator"] in {1, 3, 4}
    assert all(c["intent_verified"] and c["task_satisfied"] for c in result["cases"]
               if c["status"] == "FINISHED")
    if scope == "single":
        assert result["coverage"]["executed_scenarios"] == 3 and not result["complete"]
    elif scope == "project":
        assert result["http_requests"] == 10
        assert result["coverage"]["executed_scenarios"] == 5
        assert result["cases"][-1]["reason"] == "SUITE_BUDGET_OR_USAGE_STOP"
        assert result["coverage"]["executed_operations"] == 3
    else:
        assert result["complete"] and result["http_requests"] == 4


@pytest.mark.asyncio
async def test_unknown_usage_stops_later_independent_children(tmp_path):
    pytest.importorskip("langgraph")
    app, _, _, _, env = project_inputs("tickets")
    planner = SyntheticPlanner(unknown=True)
    result = await run_suite(_project(), "测试这个项目已授权的核心接口", planner,
        ReportStore(tmp_path), environ=env, transport=httpx.ASGITransport(app=app))
    assert planner.calls == 1 and result["usage"]["total_tokens"] is None
    assert result["usage"]["usage_known"] is False
    assert sum(c["status"] == "NOT_EXECUTED" for c in result["cases"]) == 5


@pytest.mark.asyncio
async def test_request_budget_and_model_budget_do_not_refresh_per_child(tmp_path):
    pytest.importorskip("langgraph")
    p = deepcopy(_project())
    p.registration.budget.suite_max_model_calls = 1
    app, _, _, _, env = project_inputs("tickets")
    planner = SyntheticPlanner()
    result = await run_suite(p, "测试这个项目已授权的核心接口", planner,
        ReportStore(tmp_path), environ=env, transport=httpx.ASGITransport(app=app))
    assert planner.calls == 1 and result["http_requests"] == 1
    assert result["coverage"]["executed_scenarios"] == 1


@pytest.mark.asyncio
async def test_budget_rejection_never_reuses_previous_call_stats():
    inner = SyntheticPlanner()
    inner.stats = ModelCallStats(calls=1, total_tokens=999)
    bounded = BudgetPlanner(inner, SuiteLedger(1, 1, 100, model_calls=1))
    with pytest.raises(PlannerFailure):
        await bounded.generate({})
    assert bounded.stats.calls == 0 and inner.calls == 0
