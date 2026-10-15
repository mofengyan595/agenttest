"""Trusted ordering metadata and task rejection before any planner or HTTP attempt."""

import json

import pytest

from agenttest.composed_demo import composed_inputs, run_composed_demo
from agenttest.composed_evaluation import CompositionCase, CompositionGoals, reference_draft
from agenttest.composed_planning import planning_grade
from agenttest.contracts import summarize_openapi
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.report import Outcome, ReportStore, fingerprint


def public(task):
    app, config, registry, _ = composed_inputs()
    context = planning_context(summarize_openapi(app.openapi()), config, registry, task, {})
    planner = OpenAICompatiblePlanner(ModelSettings(model="offline-ordering",
        base_url="https://model.invalid/v1", api_key="synthetic-ordering-key"),
        profile="mini_composed")
    return json.loads(json.loads(planner.prepare_request(context))["messages"][1]["content"])


def test_required_order_is_catalog_owned_and_identical_across_tasks():
    first = public("alice 创建课程“新偏序目录甲”；核对创建者读取一致。")
    second = public("bob 创建课程“新偏序目录乙”；验证 alice 读取被拒绝；核对创建者读取一致。")
    assert first.pop("user_task") != second.pop("user_task")
    assert first == second
    rules = {(r["before_policy_id"], r["after_policy_id"]) for r in first["ordering_rules"]}
    assert len(rules) == 7
    assert ("composed_read_v1", "composed_forbidden_v1") in rules
    assert ("composed_poll_v1", "composed_results_v1") in rules
    assert ("composed_create_v1", "composed_upload_v1") in rules
    assert all(r["when"] == "both_selected" for r in first["ordering_rules"])
    assert not {"expected", "requirements", "goals", "rubric"} & first.keys()


class ForbiddenPlanner:
    def __init__(self):
        self.calls = 0

    async def generate(self, context):
        self.calls += 1
        raise AssertionError("Unsupported task must not reach a planner")


@pytest.mark.asyncio
@pytest.mark.parametrize("task,code", [
    ("alice 创建课程“新前置删除甲”；删除课程；核对创建者读取一致。",
     "INTENT_COMPOSED_UNSUPPORTED_OBJECTIVE"),
    ("bob 创建课程“新前置角色乙”；验证批准两行 CSV 批处理成功及两条结果。",
     "INTENT_COMPOSED_BATCH_ROLE_UNAPPROVED"),
    ("alice 创建课程“新前置缺失丙”；验证 bob 读取被拒绝。",
     "INTENT_COMPOSED_INCOMPLETE_OBJECTIVE"),
    ("alice 创建课程“新前置重复丁”；核对创建者读取一致；核对创建者读取一致。",
     "INTENT_COMPOSED_UNSUPPORTED_OBJECTIVE"),
])
async def test_unsupported_task_is_rejected_with_zero_planning_budget(tmp_path, task, code):
    pytest.importorskip("langgraph", reason="Preflight execution uses the optional agent graph")
    from agenttest.workflow import WorkflowBudget

    planner, store = ForbiddenPlanner(), ReportStore(tmp_path)
    report = await run_composed_demo(task, planner, store,
                                     budget=WorkflowBudget(max_model_calls=0))
    assert report.outcome == Outcome.NEEDS_REVIEW and report.diagnostic.code == code
    assert report.validation_issue == report.reason == "TASK_PREFLIGHT_REJECTED"
    assert report.user_task_sha256 == fingerprint(task)
    assert report.planner_calls == planner.calls == report.tool_calls == 0
    assert report.model_call is None and not report.steps and not report.plan_valid
    assert report.workflow_nodes == ["load_contract", "analyze_or_finish", "report"]
    assert report.workflow_completed and report.task_satisfied is False
    assert report == store.read(report.run_id)
    case = CompositionCase(case_id="P301", user_task=task, expected=None, rejection_code=code)
    grade = planning_grade(case, report, None, composed_inputs()[2], "mock_transport")
    assert grade["safe_rejection"] and grade["rejection_kind"] == "task_preflight"


@pytest.mark.asyncio
async def test_normal_wrong_order_is_still_rejected_without_repair(tmp_path):
    pytest.importorskip("langgraph", reason="Preflight execution uses the optional agent graph")
    from agenttest.workflow import FakeLLM

    task = ("alice 创建课程“新偏序边界戊”；验证 bob 读取被拒绝；"
            "验证批准两行 CSV 批处理成功及两条结果；核对创建者读取一致。")
    draft = reference_draft(CompositionGoals(creator="alice", course_name="新偏序边界戊",
                                           goals=["consistency", "ownership", "batch"]))
    plan = draft.scenario()
    by_id = {step.id: step for step in plan.steps}
    plan.steps = [by_id[key] for key in ("login", "create", "other_login", "denied",
                                       "read", "upload", "poll", "results")]
    planner = FakeLLM(plan.model_dump_json())
    report = await run_composed_demo(task, planner, ReportStore(tmp_path))
    assert planner.calls == 1 and report.diagnostic.code == "INTENT_COMPOSED_ORDER_MISMATCH"
    assert report.scenario_sha256 == fingerprint(plan.model_dump())
    assert report.tool_calls == 0 and not report.steps and not report.task_satisfied


@pytest.mark.asyncio
async def test_fixed_operator_plan_does_not_require_controlled_task_grammar(tmp_path):
    pytest.importorskip("langgraph", reason="Preflight execution uses the optional agent graph")
    import httpx

    from agenttest.workflow import FakeLLM, Workflow

    app, config, registry, environ = composed_inputs()
    plan = reference_draft(CompositionGoals(creator="alice", course_name="固定操作员验收己",
                                           goals=["consistency"])).scenario()
    planner = FakeLLM(plan.model_dump_json())
    report = await Workflow(json.dumps(app.openapi()).encode(), config, registry, planner,
        ReportStore(tmp_path), httpx.ASGITransport(app), environ,
        user_task="操作员批准的固定计划", task_mode="operator_plan").run()
    assert report.outcome == Outcome.PASS and planner.calls == 1
    assert report.task_satisfied is None
