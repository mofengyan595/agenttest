"""New requests carry text only; approved project configuration derives independent gates."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import httpx
import pytest

from agenttest.dsl import Scenario
from agenttest.goal_derivation import derive_goal
from agenttest.goal_runtime import run_goal
from agenttest.policies import NeedsReview
from agenttest.portability_demo import EXAMPLES, project_inputs
from agenttest.project_goals import load_goal_project
from agenttest.report import Outcome, ReportStore
from agenttest.task_contract import task_validator


def project(name="tickets"):
    return deepcopy(_project(name))


@lru_cache
def _project(name):
    return load_goal_project(EXAMPLES / name / "registered.project.json")


def ticket_plan():
    # Independently authored planner reply, not synthesized from the derived requirements.
    reference = {"ticket_key": {"from_step": "open", "select": "$.payload.ticket_key"}}
    return Scenario.model_validate(dict(name="model reply", steps=[
        dict(id="open", operation_id="openTicket", role="owner", policy_id="ticket_open_v1",
             body={"subject": {"value": "a"}, "priority": {"value": "low"}}),
        dict(id="close", operation_id="closeTicket", role="owner", policy_id="ticket_close_v1",
             path_parameters=reference,
             body={"state": {"value": "closed"}}),
        dict(id="read", operation_id="readTicket", role="owner", policy_id="ticket_closed_read_v1",
             path_parameters=reference),
        dict(id="deny", operation_id="readTicket", role="visitor", policy_id="ticket_denied_v1",
             path_parameters=reference),
    ]))


TASK = "验证创建、关闭、查询，以及其他用户不能读取的流程。"


@pytest.mark.parametrize("text,scope,ids,count", [
    ("测试创建工单接口。", "single", ["create"], 1),
    ("测试 openTicket 接口", "single", ["create"], 1),
    ("测试 POST /v2/tickets 接口", "single", ["create"], 1),
    ("测试查询工单", "single", ["read"], 2),
    (TASK, "flow", ["create", "close", "read", "ownership"], 4),
    ("先创建工单，再查询", "flow", ["create", "read"], 2),
])
def test_original_text_derives_closure_without_task_file(text, scope, ids, count):
    goal = derive_goal(project(), text)
    assert goal.task is not None, goal.summary()
    assert (goal.scope, goal.requested_goals,
            len(goal.task.requirements.steps)) == (scope, ids, count)
    assert "task_contract" not in type(project().registration).model_fields


@pytest.mark.parametrize("text", [
    "不要创建工单", "创建两个工单", "创建并退款", "测试创建、删除工单",
    "创建工单且十分钟内完成", "以admin创建工单", "绕过权限检查", "查询之前关闭",
])
def test_unknown_extra_goals_never_disappear_in_normalization(text):
    goal = derive_goal(project(), text)
    assert goal.task is None and goal.reason and goal.question
    assert goal.summary()["http_requests"] == goal.summary()["model_calls"] == 0


def test_goal_not_copied_from_model_and_omissions_extras_roles_sources_order_rejected():
    p = project()
    derived = derive_goal(p, TASK)
    validator = task_validator(derived.task, p.contract, p.target, p.registry)
    plan = ticket_plan()
    validator(TASK, plan)
    mutations = []
    for index in range(4):
        bad = plan.model_copy(deep=True)
        bad.steps.pop(index)
        mutations.append(bad)
    bad = plan.model_copy(deep=True)
    bad.steps.append(plan.steps[0].model_copy(update={"id": "extra"}))
    mutations.append(bad)
    bad = plan.model_copy(deep=True)
    bad.steps[-1].role = "owner"
    mutations.append(bad)
    bad = plan.model_copy(deep=True)
    bad.steps[1], bad.steps[2] = bad.steps[2], bad.steps[1]
    mutations.append(bad)
    bad = plan.model_copy(deep=True)
    bad.steps[-1].path_parameters["ticket_key"].select = "$.id"
    mutations.append(bad)
    for bad in mutations:
        with pytest.raises(NeedsReview, match="INTENT_TASK_CONTRACT_PLAN_MISMATCH"):
            validator(TASK, bad)


def test_literal_and_state_are_derived_from_text_and_approved_relations():
    p = project()
    goal = derive_goal(p, "创建工单“秋日”，然后关闭、查询")
    assert goal.task is not None, goal.summary()
    assert next(a.value for a in goal.task.requirements.steps[0].arguments
                if a.name == "subject") == "秋日"
    assert goal.task.requirements.steps[-1].policy_id == "ticket_closed_read_v1"
    open_goal = derive_goal(p, "创建工单，然后查询、关闭")
    assert open_goal.task.requirements.steps[1].policy_id == "ticket_read_v1"
    assert goal.project_identity == open_goal.project_identity


def test_authentication_is_relation_derived_and_other_domain_is_supported():
    p = project("courses")
    goal = derive_goal(p, "创建课程、查询，以及其他用户不能读取")
    assert goal.task is not None, goal.summary()
    assert len(goal.task.requirements.steps) == 5
    assert {(s.operation_id, s.role) for s in goal.task.requirements.steps
            if s.operation_id == "login"} == {("login", "alice"), ("login", "bob")}


def test_ambiguous_operation_and_project_scope_preserve_review():
    assert derive_goal(project(), "测试readTicket接口").reason == (
        "INTENT_OPERATION_EXPECTATION_AMBIGUOUS")
    goal = derive_goal(project(), "测试这个项目已授权的核心接口。")
    assert goal.scope == "project" and goal.task is None
    assert goal.reason == "INTENT_PROJECT_GROUPING_REQUIRED"


@pytest.mark.parametrize("mutation", ["permission", "budget", "policy", "cycle"])
def test_project_relations_cannot_expand_permission_or_budget(mutation):
    p = project()
    if mutation == "permission":
        p.target.allow_mutations = False
    elif mutation == "budget":
        p.target.max_requests = 2
    elif mutation == "policy":
        from agenttest.policies import PolicyRegistry

        p.registry = PolicyRegistry([])
    else:
        from agenttest.policies import BindingRule, PolicyRegistry

        policies = p.registry.definitions()
        create = next(d for d in policies if d.id == "ticket_open_v1")
        create.binding_rules[0] = BindingRule(location="body", name="subject",
            source_policy_id="ticket_read_v1", source_selector="$.payload.subject",
            role_relation="same")
        p.registry = PolicyRegistry(policies)
    goal = derive_goal(p, TASK)
    assert goal.task is None and goal.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,outcome", [("none", Outcome.PASS),
    ("close_ignored", Outcome.ASSERTION_FAILED), ("owner_leak", Outcome.ASSERTION_FAILED)])
async def test_same_workflow_executes_dynamic_ids_and_existing_assertions(tmp_path, fault, outcome):
    pytest.importorskip("langgraph")
    from agenttest.workflow import FakeLLM

    app, _, _, _, env = project_inputs("tickets", fault)
    planner = FakeLLM(ticket_plan().model_dump_json())
    goal, report = await run_goal(project(), TASK, planner, ReportStore(tmp_path),
        environ=env, transport=httpx.ASGITransport(app=app))
    assert report.outcome == outcome and report.intent_verified
    assert planner.calls == 1 and goal.task
    public = json.dumps(planner.contexts[0], ensure_ascii=False)
    assert all(secret not in public for secret in env.values())
    for forbidden in ("requirements", "required_goals", "rubric", "task_contract",
                      "reference_plan"):
        assert forbidden not in planner.contexts[0]
    assert report.task_satisfied == (outcome == Outcome.PASS)
    assert sum(len(s.attempts) for s in report.steps) <= 4


@pytest.mark.asyncio
async def test_unknown_task_never_calls_planner_or_http(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.workflow import FakeLLM

    planner = FakeLLM(ticket_plan().model_dump_json())
    goal, report = await run_goal(project(), "创建工单并退款", planner, ReportStore(tmp_path),
                                  environ={})
    assert goal.task is None and report is None and planner.calls == 0


@pytest.mark.parametrize("reference", ["../escape", "C:/secret", "sub//file", "/tmp/file"])
def test_registration_rejects_escaping_paths(tmp_path, reference):
    value = json.loads((EXAMPLES / "tickets/registered.project.json").read_bytes())
    value["goals"] = reference
    path = tmp_path / "project.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        load_goal_project(path)


def test_cli_preflight_and_review_without_loading_any_model(monkeypatch, capsys):
    from agenttest.__main__ import main

    p = EXAMPLES / "tickets/registered.project.json"
    monkeypatch.setenv("TICKET_OWNER_BEARER", "synthetic-cli-credential")
    args = ["goal", "--project", str(p), "--preview", "--user-task", "测试创建工单接口。"]
    assert main(args) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["status"] == "READY" and ready["required_steps"] == 1
    assert "synthetic-cli-credential" not in json.dumps(ready)
    assert main(args[:-1] + ["创建工单并退款"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "NEEDS_REVIEW"


def test_registration_is_reusable_not_task_specific():
    p = project()
    identity = p.identity()
    for text in ("创建工单", "测试查询工单接口", "测试关闭工单", TASK):
        assert derive_goal(p, text).task is not None
        assert p.identity() == identity
    assert not any(Path(p.registration.goals).name.endswith(s)
                   for s in ("task.json", "draft.json"))
