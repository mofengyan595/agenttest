"""Operator fixture: registered approvals and disposable ASGI; all model/network calls trapped."""

from pathlib import Path

import httpx
import pytest

from agenttest.goal_suite import build_suite
from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.negative_demo import create_negative_target
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionRuntime
from agenttest.report import ReportStore


@pytest.fixture
def agenttest_regression_runtime(tmp_path, monkeypatch, request):
    def forbidden(*args, **kwargs):
        raise AssertionError("MODEL_AND_REAL_HTTP_DISABLED")

    from agenttest.evidence_context import EvidenceFeedbackModel
    from agenttest.native_tools import NativeFunctionPlanner

    for cls in (OpenAICompatiblePlanner, EvidenceFeedbackModel, NativeFunctionPlanner):
        monkeypatch.setattr(cls, "generate", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    root = Path(__file__).resolve().parents[2] / "trusted-testing/development/validation"
    project = load_goal_project(root / "registered.project.json")
    policy = {"normal": "validation_normal", "negative": "missing_message"}[
        request.node.path.parent.name]
    suite = build_suite(project, "测试创建接口", environ={})
    goal = next(c.goal for c in suite.cases if c.goal and c.goal.task
                and c.goal.task.requirements.steps[-1].policy_id == policy)
    return RegressionRuntime(project.document, project.target, project.registry, goal.task,
        {}, httpx.ASGITransport(app=create_negative_target()), ReportStore(tmp_path / "reports"))
