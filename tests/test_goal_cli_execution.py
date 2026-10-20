"""CLI closes the loop using the original public requirement and synthetic target."""

import json

import pytest

from agenttest.portability_demo import EXAMPLES


def test_cli_runs_without_per_request_task_contract(tmp_path, capsys):
    pytest.importorskip("langgraph")
    from test_goal_entry import ticket_plan

    from agenttest.__main__ import main

    path = tmp_path / "reply.json"
    path.write_text(ticket_plan().model_dump_json(), encoding="utf-8")
    result = main(["goal", "--project", str(EXAMPLES / "tickets/registered.project.json"),
        "--user-task", "验证创建、关闭、查询，以及其他用户不能读取的流程。",
        "--fake-plan", str(path), "--synthetic-target", "--runs-dir", str(tmp_path / "reports"),
        "--export-regression", str(tmp_path / "bundle")])
    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["outcome"] == "PASS" and summary["task_satisfied"]
    assert summary["required_steps"] == 4
    assert (tmp_path / "bundle/manifest.json").is_file()
