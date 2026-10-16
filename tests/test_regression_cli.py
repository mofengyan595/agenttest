"""Opt-in capture exports only after the whole natural-language workflow succeeds."""

import json

import httpx
import pytest

from agenttest.portability_demo import project_inputs, task_inputs
from agenttest.regression import load_regression


def arguments(tmp_path, monkeypatch):
    app, document, target, registry, environ = project_inputs("tickets")
    task, draft = task_inputs("tickets", "read")
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    values = {"contract": document, "target": target.model_dump_json().encode(),
              "policies": json.dumps([
                  p.model_dump() for p in registry.definitions()]).encode(),
              "task-contract": task.model_dump_json().encode(),
              "fake-plan": draft.scenario().model_dump_json().encode()}
    args = ["agent", "--user-task", task.user_task, "--runs-dir", str(tmp_path / "reports"),
            "--export-regression", str(tmp_path / "bundle")]
    for name, raw in values.items():
        path = tmp_path / (name + ".json")
        path.write_bytes(raw)
        args.extend(["--" + name, str(path)])
    return app, environ, draft, args


@pytest.mark.parametrize("broken", [False, True])
def test_agent_capture_exports_only_a_complete_success(tmp_path, monkeypatch, capsys, broken):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.__main__ import main

    app, environ, draft, args = arguments(tmp_path, monkeypatch)
    if broken:
        from agenttest.ticket_target import create_ticket_target

        app = create_ticket_target({"owner": environ["TICKET_OWNER_BEARER"],
                                    "visitor": environ["TICKET_VISITOR_BEARER"]}, "wrong_subject")
    original = workflow.Workflow

    def local(*args, **kwargs):
        return original(*args, transport=httpx.ASGITransport(app), environ=environ, **kwargs)

    monkeypatch.setattr(workflow, "Workflow", local)
    assert main(args) == int(broken)
    result = json.loads(capsys.readouterr().out)
    assert result["regression"]["exported"] is not broken
    if broken:
        assert result["outcome"] == "ASSERTION_FAILED" and not (tmp_path / "bundle").exists()
    else:
        assert result["outcome"] == "PASS"
        assert load_regression(tmp_path / "bundle")[1] == draft.scenario()


def test_export_preflight_rejects_existing_destination_before_model_settings(
    tmp_path, monkeypatch, capsys,
):
    pytest.importorskip("agenttest.workflow")
    from agenttest.__main__ import main
    from agenttest.llm_planner import ModelSettings

    _, _, _, args = arguments(tmp_path, monkeypatch)
    index = args.index("--fake-plan")
    del args[index:index + 2]
    args.append("--real")
    (tmp_path / "bundle").mkdir()

    def forbidden(*args, **kwargs):
        raise AssertionError("Model configuration must not be read")

    monkeypatch.setattr(ModelSettings, "from_environment", forbidden)
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "INVALID_INPUT_OR_REPORT_IO"
