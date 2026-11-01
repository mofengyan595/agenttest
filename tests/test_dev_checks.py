"""Parallel development checks preserve file coverage and propagate every failed batch."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def checks(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/check_dev.py"
    spec = importlib.util.spec_from_file_location("dev_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.AREAS = {"fixture": ["test_*"]}
    (tmp_path / "tests").mkdir()
    for name in ("test_first.py", "test_second.py", "test_third.py"):
        (tmp_path / "tests" / name).write_text("def test_example(): pass\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(path), "--area", "fixture", "--jobs", "3"])
    return module


def test_selection_discovers_new_files_and_partitions_each_once(checks):
    files = checks.selected_files("fixture", checks.ROOT)
    weights = dict(zip(files, (20, 8, 1)))
    batches = checks.partition(files, 2, weights)
    assert sorted(file for batch in batches for file in batch) == files
    assert len({file for batch in batches for file in batch}) == len(files)
    assert sorted(map(len, batches)) == [1, 2]
    assert len(checks.partition(files, 3, weights)) == 3
    checks.AREAS["missing"] = ["test_not_present"]
    with pytest.raises(ValueError, match="test_not_present"):
        checks.selected_files("missing", checks.ROOT)


def test_historical_durations_only_balance_work_and_invalid_reports_are_ignored(checks):
    files = checks.selected_files("fixture", checks.ROOT)
    output = checks.ROOT / "reports"
    output.mkdir()
    (output / "broken.xml").write_text("<invalid", encoding="utf-8")
    (output / "valid.xml").write_text(
        '<testsuites><testsuite><testcase classname="tests.test_second" time="12"/>'
        '<testcase classname="tests.test_second.SomeClass" time="3"/></testsuite></testsuites>',
        encoding="utf-8")
    weights = checks.duration_weights(files, output)
    assert weights["tests/test_second.py"] == 15
    assert weights["tests/test_first.py"] == 1
    assert sum(map(len, checks.partition(files, 3, weights))) == len(files)


@pytest.mark.parametrize("fault", [None, "pytest", "report", "missing_report", "timeout"])
def test_parallel_runner_records_every_batch_and_never_masks_failures(checks, monkeypatch, fault):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "ruff" in command:
            return SimpleNamespace(returncode=0)
        is_first = "tests/test_first.py" in command
        if is_first and fault == "timeout":
            raise subprocess.TimeoutExpired(command, 900)
        report = Path(command[command.index("--junitxml") + 1])
        if not (is_first and fault == "missing_report"):
            failures = int(is_first and fault == "report")
            report.write_text(f'<testsuites><testsuite tests="1" failures="{failures}" '
                'errors="0" skipped="0"/></testsuites>', encoding="utf-8")
        return SimpleNamespace(returncode=int(is_first and fault == "pytest"))

    monkeypatch.setattr(checks.subprocess, "run", run)
    assert checks.main() == int(fault is not None)
    import json

    record = json.loads(next((checks.ROOT / "runs/dev-checks").glob("*/result.json"))
                        .read_text(encoding="utf-8"))
    assert len(record["results"]) == record["jobs"] == 3
    assert record["status"] == ("PASSED" if fault is None else "FAILED")
    assert len([c for c in calls if "ruff" in c]) == 1
    assert sorted(file for command in calls for file in command if file.startswith("tests/")) == (
        checks.selected_files("fixture", checks.ROOT))
    assert record["wall_seconds"] >= 0 and record["batch_seconds_sum"] >= 0


def test_list_only_never_starts_tests(checks, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_dev.py", "--area", "fixture", "--list"])

    def forbidden(*args, **kwargs):
        pytest.fail("Preview must not start any process")

    monkeypatch.setattr(checks.subprocess, "run", forbidden)
    assert checks.main() == 0


def test_lint_failure_stops_before_any_test_process(checks, monkeypatch):
    calls = []

    def lint_failure(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(checks.subprocess, "run", lint_failure)
    assert checks.main() == 2
    assert len(calls) == 1 and "ruff" in calls[0]
