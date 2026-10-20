"""Operator ASGI fixture; TaskContract is derived from original text."""
import json
from pathlib import Path

import httpx
import pytest

from agenttest.evidence_context import EvidenceFeedbackModel
from agenttest.goal_cli import synthetic_runtime
from agenttest.goal_derivation import derive_goal
from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionRuntime
from agenttest.report import ReportStore


@pytest.fixture
def agenttest_regression_runtime(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NO_MODEL_OR_BUSINESS_NETWORK_IN_REGRESSION")

    monkeypatch.setattr(OpenAICompatiblePlanner, "generate", forbidden)
    monkeypatch.setattr(EvidenceFeedbackModel, "generate", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    root = Path(__file__).parent
    request = json.loads((root / "request.json").read_bytes())
    project = load_goal_project(root / "projects" / request["project"] / "registered.project.json")
    goal = derive_goal(project, request["user_task"])
    env, transport = synthetic_runtime(project)
    return RegressionRuntime(project.document, project.target, project.registry,
        goal.task, env, transport, ReportStore(tmp_path / "reports"))
