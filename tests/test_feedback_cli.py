"""Explicit operator capability files enable feedback without adding browser execution."""

import json

import httpx
import pytest

from agenttest.feedback_check import CASES, inputs
from agenttest.report import ReportStore


def test_agent_feedback_cli_reuses_project_inputs_and_preserves_failed_verdict(
    tmp_path, monkeypatch, capsys,
):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.__main__ import main

    app, document, target, registry, environ, task, draft, capabilities = inputs(CASES[1])
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    files = {"contract.json": document, "target.json": target.model_dump_json().encode(),
        "policies.json": json.dumps([p.model_dump() for p in registry.definitions()]).encode(),
        "task.json": task.model_dump_json().encode(),
        "plan.json": draft.scenario().model_dump_json().encode(),
        "capabilities.json": json.dumps([c.model_dump() for c in capabilities.entries()]).encode()}
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / "project.json").write_text(json.dumps({
        "schema_version": "agenttest.project.v1", "contract": "contract.json",
        "target": "target.json", "policies": "policies.json", "task_contract": "task.json"}),
        encoding="utf-8")
    original = workflow.Workflow

    def disposable(*args, **kwargs):
        assert kwargs["investigation"].feedback.max_model_calls == 3
        assert kwargs["investigation"].feedback.max_requests == 2
        return original(*args, transport=httpx.ASGITransport(app), **kwargs)

    monkeypatch.setattr(workflow, "Workflow", disposable)
    assert main(["agent", "--project", str(tmp_path / "project.json"), "--fake-plan",
        str(tmp_path / "plan.json"), "--feedback-capabilities", str(tmp_path / "capabilities.json"),
        "--runs-dir", str(tmp_path / "reports")]) == 1
    output = json.loads(capsys.readouterr().out)
    report = ReportStore(tmp_path / "reports").read(output["run_id"])
    assert output["outcome"] == "ASSERTION_FAILED"
    assert output["feedback_state"] == "NEEDS_REVIEW"
    assert report.feedback.requests_used == 1 and report.feedback.model_calls_used == 2
