"""CLI/export/replay integration uses only the already-consumed ND02 development task."""

import json
import shutil
from uuid import uuid4

import pytest

from agenttest.demo import PROJECT_ROOT

pytest.importorskip("langgraph")

from agenttest import nl_usage as usage
from agenttest.nl_tasks import cases, task_inputs_for
from agenttest.report import ReportStore


@pytest.fixture
def root():
    directory = PROJECT_ROOT / "runs" / ("pytest-nl-usage-" + uuid4().hex)
    yield directory
    if directory.exists():
        assert directory.resolve().is_relative_to((PROJECT_ROOT / "runs").resolve())
        shutil.rmtree(directory)


def test_cli_complete_export_then_no_model_replay(root):
    case = cases("development")[1]
    usage.prepare(case, root)
    fake = root / "nd02-fake.json"
    fake.write_text(task_inputs_for(case)[6].scenario().model_dump_json(), encoding="utf-8")
    result = usage.execute(root, fake_plan=fake)
    assert result["product_complete"] and result["exported"], result
    assert result["model_calls"] == result["real_model_calls"] == 0
    assert result["replay_exit"] == 0
    assert "1 passed" in (root / "replay.stdout.txt").read_text()
    assert not (root / "pytest-temp").exists()
    snapshots = list((root / "replay-reports").glob("*/report.json"))
    assert len(snapshots) == 1
    replay = ReportStore(root / "replay-reports").read(result["replay_run_id"])
    assert replay.model_call is None and replay.task_mode == "operator_plan"
    assert not (root / "project" / "fake-plan.json").exists()
    with pytest.raises(ValueError, match="already attempted"):
        usage.execute(root, fake_plan=fake)


def test_existing_regression_temporary_directory_is_preserved(root):
    usage.prepare(cases("development")[1], root)
    temporary = root.parent / (root.name + "-pytest-temp")
    temporary.mkdir()
    sentinel = temporary / "operator-file.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="temporary directory already exists"):
            usage.execute(root, fake_plan=root / "never-read.json")
        assert sentinel.read_text() == "preserve"
        assert not (root / "freeze.json").exists()
    finally:
        assert temporary.resolve().is_relative_to((PROJECT_ROOT / "runs").resolve())
        shutil.rmtree(temporary)


def test_reviewed_configuration_change_stops_before_model(root):
    usage.prepare(cases("development")[1], root)
    path = root / "project" / "task.json"
    data = json.loads(path.read_bytes())
    data["user_task"] += " Changed after review."
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        usage.execute(root, fake_plan=root / "never-read.json")
    assert not (root / "freeze.json").exists()


def test_refused_incomplete_plan_is_not_exported_or_repaired(root):
    case = cases("development")[1]
    usage.prepare(case, root)
    plan = task_inputs_for(case)[6].scenario()
    plan.steps = plan.steps[:1]
    fake = root / "incomplete.json"
    fake.write_text(plan.model_dump_json(), encoding="utf-8")
    result = usage.execute(root, fake_plan=fake)
    assert not result["exported"] and not result["product_complete"]
    assert result["replay_exit"] is None
    assert not (root / "bundle").exists()
