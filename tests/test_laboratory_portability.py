"""Third project adds fixture/configuration; core receives the original request and registry."""

import runpy
from pathlib import Path

import httpx
import pytest

from agenttest.goal_suite import build_suite, run_suite
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.project_goals import load_goal_project
from agenttest.report import ReportStore

ROOT = Path(__file__).resolve().parents[1] / "examples/trusted-testing/laboratory"


def runtime(fault="none"):
    return runpy.run_path(str(ROOT / "fixture.py"))["runtime"](fault)


def test_resource_graph_has_two_independent_producers_and_an_indirect_consumer():
    project = load_goal_project(ROOT / "registered.project.json")
    suite = build_suite(project, "测试这个项目已授权的核心接口")
    assert len(suite.cases) == 24
    assert suite.preview()["budget_preview"]["complete_project_within_budget"]
    assert suite.preview()["budget_preview"]["required_business_requests"] == 42
    placement = next(p for p in project.registry.definitions() if p.id == "lab_place_v1")
    assert {b.source_policy_id for b in placement.binding_rules if b.source_policy_id} == {
        "lab_tray_v1", "lab_specimen_v1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,text", [("single", "测试登记样本接口"),
    ("flow", "先登记容器，再登记样本，然后装载、查验、跨角色拒绝"),
    ("project", "测试这个项目已授权的核心接口")])
async def test_three_paths_without_new_core_branches_or_task_specific_steps(tmp_path, scope, text):
    pytest.importorskip("langgraph")
    project = load_goal_project(ROOT / "registered.project.json")
    app, env = runtime()
    store = ReportStore(tmp_path)
    result = await run_suite(project, text, CatalogFixturePlanner(), store,
        environ=env, transport=httpx.ASGITransport(app=app))
    assert result["complete"] and result["http_requests"] == app.state.requests
    assert result["coverage"]["planned_scenarios"] == len(result["cases"])
    assert result["coverage"]["executed_scenarios"] == len(result["cases"])
    if scope == "flow":
        report = store.read(result["cases"][0]["run_id"])
        assert [s.operation_id for s in report.steps] == [
            "makeTray", "makeSpecimen", "placeSpecimen", "getPlacement", "getPlacement"]
    elif scope == "project":
        assert result["model_calls"] == 5 and result["http_requests"] == 42
        assert result["coverage"]["derived_executed"] == 19
        assert sum(r["negative_rejections_passed"] for r in result["cases"]) == 15
        assert result["coverage"]["executed_operations"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["accept_invalid", "readback", "owner_leak"])
async def test_same_assertions_detect_faults_in_the_new_graph(tmp_path, fault):
    pytest.importorskip("langgraph")
    project = load_goal_project(ROOT / "registered.project.json")
    text = "测试登记样本接口" if fault == "accept_invalid" else "装载、查验、跨角色拒绝"
    app, env = runtime(fault)
    result = await run_suite(project, text, CatalogFixturePlanner(), ReportStore(tmp_path),
        environ=env, transport=httpx.ASGITransport(app=app))
    assert not result["complete"]
    assert any(c.get("outcome") == "ASSERTION_FAILED" for c in result["cases"])


def test_unknown_business_and_path_mutations_remain_explicit_review():
    project = load_goal_project(ROOT / "registered.project.json")
    unknown = build_suite(project, "装载后自动计算保存年限").preview()
    assert unknown["cases"][0]["status"] == "NEEDS_REVIEW"
    placed = build_suite(project, "测试装载接口")
    assert any(c.reason == "EXPECTED_ERROR_BEHAVIOR_NOT_APPROVED" for c in placed.cases)
