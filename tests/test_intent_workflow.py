import json

import httpx
import pytest
from test_planner_input import scenario

pytest.importorskip("langgraph", reason="Intent workflow needs optional graph runtime")

from agenttest.natural_demo import natural_inputs, run_natural_demo
from agenttest.report import Outcome, ReportStore
from agenttest.workflow import FakeLLM, Workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("cross,name,creator,code", [
    (True, "错误课程名", "bob", "INTENT_NAME_MISMATCH"),
    (True, "机器学习", "alice", "INTENT_CREATOR_MISMATCH"),
    (False, "机器学习", "bob", "INTENT_PATH_MISMATCH"),
])
async def test_valid_but_wrong_t12_plan_never_executes(tmp_path, cross, name, creator, code):
    task = '由 bob 创建“机器学习”并核对自己的读取结果，再验证 alice 读取同一个课程时被拒绝。'
    report = await run_natural_demo(task, FakeLLM(scenario(cross, name, creator).model_dump_json()),
                                    ReportStore(tmp_path))
    assert report.plan_valid and not report.intent_verified
    assert report.outcome == Outcome.NEEDS_REVIEW and report.diagnostic.code == code
    assert report.tool_calls == 0 and not report.steps
    assert name not in report.model_dump_json()


@pytest.mark.asyncio
async def test_open_task_cannot_be_misrepresented_as_pass(tmp_path):
    report = await run_natural_demo('alice 创建“线性代数”并查询，还需验证收费是免费',
        FakeLLM(scenario().model_dump_json()), ReportStore(tmp_path))
    assert report.plan_valid and report.outcome == Outcome.NEEDS_REVIEW
    assert report.diagnostic.code == "INTENT_UNSUPPORTED_LANGUAGE" and report.tool_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    ("missing_read", "INTENT_PATH_MISMATCH"),
    ("missing_denial", "INTENT_PATH_MISMATCH"),
    ("extra_read", "INTENT_PATH_MISMATCH"),
    ("wrong_creator", "INTENT_CREATOR_MISMATCH"),
    ("auth_order", "DSL_AUTHENTICATION_ORDER"),
    ("resource_source", "POLICY_DEPENDENCY_MISMATCH"),
    ("resource_selector", "POLICY_DEPENDENCY_MISMATCH"),
    ("read_order", "INTENT_READ_ORDER_MISMATCH"),
])
async def test_missing_extra_role_auth_and_resource_goals_stop_before_http(tmp_path,
                                                                         mutation, code):
    plan = scenario(True, "本轮依赖课程")
    if mutation == "missing_read":
        plan.steps.pop(3)
    elif mutation == "missing_denial":
        plan.steps.pop()
    elif mutation == "extra_read":
        extra = plan.steps[3].model_copy(deep=True)
        extra.id = "extra"
        plan.steps.append(extra)
    elif mutation == "wrong_creator":
        plan = scenario(True, "本轮依赖课程", "bob")
    elif mutation == "auth_order":
        plan.steps.append(plan.steps.pop(0))
    elif mutation == "resource_source":
        plan.steps[-1].path_parameters["courseId"].from_step = "read"
    elif mutation == "resource_selector":
        plan.steps[-1].path_parameters["courseId"].select = "$.name"
    else:
        plan.steps[3], plan.steps[4] = plan.steps[4], plan.steps[3]
    fake = FakeLLM(plan.model_dump_json())
    report = await run_natural_demo(
        'alice 创建“本轮依赖课程”并查询核对名称和 ID，再让 bob 查询同一课程被拒绝。',
        fake, ReportStore(tmp_path))
    assert report.outcome in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}
    assert report.diagnostic.code == code and report.tool_calls == 0
    assert not report.steps and not report.intent_verified and report.task_satisfied is False
    assert fake.calls == 1  # No replan, sorting or silent repair.


@pytest.mark.asyncio
async def test_bearer_environment_cannot_hide_requested_login_order(tmp_path):
    app, config, registry, environ = natural_inputs()
    config.roles["alice"].bearer_env = "PREAPPROVED_BEARER"
    environ["PREAPPROVED_BEARER"] = "synthetic-bearer"
    plan = scenario(name="本轮认证课程")
    plan.steps.append(plan.steps.pop(0))
    report = await Workflow(json.dumps(app.openapi()).encode(), config, registry,
        FakeLLM(plan.model_dump_json()), ReportStore(tmp_path), httpx.ASGITransport(app),
        environ, user_task='alice 登录后创建“本轮认证课程”并查询核对名称和 ID。').run()
    assert report.plan_valid and report.outcome == Outcome.NEEDS_REVIEW
    assert report.diagnostic.code == "INTENT_AUTH_ORDER_MISMATCH" and report.tool_calls == 0


@pytest.mark.asyncio
async def test_builtin_mini_intent_cannot_be_overridden_by_callback(tmp_path):
    app, config, registry, environ = natural_inputs()
    report = await Workflow(json.dumps(app.openapi()).encode(), config, registry,
        FakeLLM(scenario(name="错误名称").model_dump_json()), ReportStore(tmp_path),
        httpx.ASGITransport(app), environ, user_task='alice 创建“本轮课程”并查询',
        intent_validator=lambda task, plan: None).run()
    assert report.diagnostic.code == "INTENT_NAME_MISMATCH" and report.tool_calls == 0
