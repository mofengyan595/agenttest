"""Operator-owned ASGI runtime. Inputs are reviewed files, never model code."""
import json
from pathlib import Path

import httpx
import pytest

from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import project_inputs
from agenttest.regression import RegressionRuntime
from agenttest.report import ReportStore
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract


@pytest.fixture
def agenttest_regression_runtime(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NO_MODEL_OR_BUSINESS_NETWORK_IN_REGRESSION")

    monkeypatch.setattr(OpenAICompatiblePlanner, "generate", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    root = Path(__file__).parent / "project"
    case = json.loads((root / "case.json").read_bytes())
    app, _, _, _, env = project_inputs(case["project"])
    return RegressionRuntime((root / "openapi.json").read_bytes(),
        TargetConfig.model_validate_json((root / "target.json").read_bytes()),
        PolicyRegistry.from_json((root / "policies.json").read_bytes()),
        TaskContract.model_validate_json((root / "task.json").read_bytes()), env,
        httpx.ASGITransport(app), ReportStore(tmp_path / "reports"))
