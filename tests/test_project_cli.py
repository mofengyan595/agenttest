"""Project-selected inputs use the existing generic workflow and regression exporter."""

import json

import httpx
import pytest
from test_project import write_inputs

from agenttest.regression import load_regression


@pytest.mark.parametrize("name", ["courses", "tickets"])
def test_project_agent_runs_fake_plan_with_captured_inputs_and_can_export(
    tmp_path, monkeypatch, capsys, name,
):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.__main__ import main

    path, environ, app, draft = write_inputs(tmp_path, name)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    plan = tmp_path / "plan.json"
    plan.write_text(draft.scenario().model_dump_json(), encoding="utf-8")
    original = workflow.Workflow

    def disposable(*args, **kwargs):
        # A later file change cannot silently replace the approved input snapshot.
        (tmp_path / "policies.json").write_text("[]", encoding="utf-8")
        assert all(kwargs["environ"][key] == value for key, value in environ.items())
        return original(*args, transport=httpx.ASGITransport(app), **kwargs)

    monkeypatch.setattr(workflow, "Workflow", disposable)
    assert main(["agent", "--project", str(path), "--fake-plan", str(plan),
                 "--runs-dir", str(tmp_path / "reports"),
                 "--export-regression", str(tmp_path / "bundle")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "PASS" and result["regression"]["exported"]
    assert load_regression(tmp_path / "bundle")[1] == draft.scenario()
