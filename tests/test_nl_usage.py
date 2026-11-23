"""CLI/export/replay integration uses only the already-consumed ND02 development task."""

import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from functools import partial
from unittest.mock import patch
from uuid import uuid4

import pytest

from agenttest.demo import PROJECT_ROOT

pytest.importorskip("langgraph")

from agenttest import nl_usage as usage
from agenttest.nl_tasks import cases, task_inputs_for
from agenttest.report import ReportStore


@contextmanager
def _overlay_environment(environ, values):
    with pytest.MonkeyPatch.context() as overlay:
        for key, value in values.items():
            overlay.setitem(environ, key, value)
        yield


@pytest.fixture(autouse=True)
def isolate_cli_environment(monkeypatch):
    if sys.platform == "win32":
        # Restoring a whole environment through os.putenv loses inherited empty values on
        # Windows. Keep the CLI overlay local to this module and restore only its own keys.
        local_patch = partial(patch)
        local_patch.dict = _overlay_environment
        monkeypatch.setattr(usage, "patch", local_patch)


def test_cli_environment_preserves_inherited_git_config():
    env = os.environ.copy()
    first = int(env.get("GIT_CONFIG_COUNT", "0"))
    empty_key = f"GIT_CONFIG_VALUE_{first}"
    env.update({"GIT_CONFIG_COUNT": str(first + 2),
                f"GIT_CONFIG_KEY_{first}": "agenttest.testInheritedEmpty", empty_key: "",
                f"GIT_CONFIG_KEY_{first + 1}": "agenttest.testInheritedValue",
                f"GIT_CONFIG_VALUE_{first + 1}": "fixture-value",
                "AGENTTEST_TEST_CLI_OVERLAY": "outside"})
    # Supply a real inherited empty value in the child environment block. Assigning "" to
    # os.environ in the test process would itself remove that native variable on Windows.
    script = '''
import os
import runpy
import subprocess
import sys

overlay = runpy.run_path(sys.argv[1])["_overlay_environment"]
empty_key = sys.argv[2]
before = os.environ.copy()
marker = "AGENTTEST_TEST_CLI_OVERLAY"
new_key = "AGENTTEST_TEST_CLI_TEMPORARY"

def git(*args, env=None):
    return subprocess.run(["git", *args], env=env, capture_output=True, text=True, check=False)

def assert_git_works():
    head = git("rev-parse", "HEAD")
    assert head.returncode == 0, head.stderr
    empty = git("config", "--get", "agenttest.testInheritedEmpty")
    assert empty.returncode == 0 and empty.stdout == "\\n", empty.stderr
    real = git("config", "--get", "agenttest.testInheritedValue")
    assert real.returncode == 0 and real.stdout.strip() == "fixture-value", real.stderr
    return head.stdout

assert before[empty_key] == ""
head = assert_git_works()
for value in ("first", "adjacent"):
    with overlay(os.environ, {marker: value, new_key: "temporary"}):
        assert os.environ[marker] == value and new_key in os.environ
        assert assert_git_works() == head
    assert os.environ.copy() == before
    assert assert_git_works() == head

malformed = before.copy()
del malformed[empty_key]
rejected = git("rev-parse", "HEAD", env=malformed)
assert rejected.returncode == 128 and empty_key in rejected.stderr, rejected.stderr
'''
    result = subprocess.run([sys.executable, "-B", "-c", script, __file__, empty_key],
                            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode == 0, result.stderr


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
