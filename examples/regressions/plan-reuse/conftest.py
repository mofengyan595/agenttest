"""Operator fixture; fresh ASGI resources, all model and real HTTP paths disabled."""

import json
from pathlib import Path

import httpx
import pytest

from agenttest.evidence_context import EvidenceFeedbackModel
from agenttest.goal_cli import synthetic_runtime
from agenttest.goal_spec import GoalSpecModel
from agenttest.goal_suite import build_suite
from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.native_tools import NativeFunctionPlanner
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionRuntime
from agenttest.report import ReportStore


@pytest.fixture
def agenttest_regression_runtime(tmp_path, monkeypatch, request):
    def forbidden(*args, **kwargs):
        raise AssertionError("MODEL_AND_REAL_HTTP_DISABLED_IN_DERIVED_REGRESSION")

    for model in (OpenAICompatiblePlanner, EvidenceFeedbackModel, GoalSpecModel,
                  NativeFunctionPlanner, CatalogFixturePlanner):
        monkeypatch.setattr(model, "generate", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    root = Path(__file__).parent
    metadata = json.loads((root / "asset.json").read_bytes())
    repo = next(p for p in root.parents if (p / "pyproject.toml").is_file())
    project = load_goal_project(repo /
        "examples/trusted-testing/projects/tickets/registered.project.json")
    case_id = metadata["case_ids"][request.node.path.parent.name]
    suite = build_suite(project, metadata["user_task"])
    goal = next(case.goal for case in suite.cases if case.id == case_id)
    env, transport = synthetic_runtime(project)
    return RegressionRuntime(project.document, project.target, project.registry, goal.task,
        env, transport, ReportStore(tmp_path / "reports"))
