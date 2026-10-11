import pytest
from test_planner_input import scenario

pytest.importorskip("langgraph", reason="Intent workflow needs optional graph runtime")

from agenttest.natural_demo import run_natural_demo
from agenttest.report import Outcome, ReportStore
from agenttest.workflow import FakeLLM


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
