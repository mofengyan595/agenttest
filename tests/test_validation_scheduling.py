"""Full coverage and fresh isolated scheduling remain mandatory when work overlaps."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name + "_validation_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return load("check_full"), load("run_colleague_acceptance")


def schedule():
    return dict(order=[f"{r}:{p}" for r in (1, 2) for p in
                      ("parcels", "reservations", "directory")],
                tasks=[dict(project=p) for p in ("parcels", "reservations", "directory")])


def test_old_order_stays_serial_and_parallel_requires_a_new_declared_schedule(harness):
    _, model = harness
    manifest = schedule()
    assert model.project_lanes(manifest, 1) == {"serial": manifest["order"]}
    with pytest.raises(ValueError, match="NEW_ISOLATED_PARALLEL"):
        model.project_lanes(manifest, 3)
    manifest["execution_mode"] = "PARALLEL_PROJECTS_ISOLATED"
    lanes = model.project_lanes(manifest, 3)
    assert lanes == {p: [f"1:{p}", f"2:{p}"] for p in ("parcels", "reservations", "directory")}
    assert sorted(p for lane in lanes.values() for p in lane) == sorted(manifest["order"])
    manifest["order"].append("1:parcels")
    with pytest.raises(ValueError, match="UNIQUE_NONEMPTY"):
        model.project_lanes(manifest, 3)


def test_recorded_first_attempt_folder_cannot_be_replayed(harness, tmp_path):
    _, model = harness
    model.claim_version(tmp_path / "old")
    claim = (tmp_path / "old/first-attempt.claim").read_bytes()
    with pytest.raises(ValueError, match="ALREADY_RECORDED"):
        model.claim_version(tmp_path / "old")
    assert (tmp_path / "old/first-attempt.claim").read_bytes() == claim


@pytest.mark.parametrize("observed,executed", [(["a"], ["a"]),
    (["a", "b"], ["a"]), (["a", "b", "b"], ["a", "b"]),
    (["a", "b"], ["a", "b", "b"]), (["a", "extra"], ["a", "extra"])])
def test_full_audit_rejects_missing_duplicate_or_unexecuted_ids(harness, observed, executed):
    full, _ = harness
    assert not full.exact_coverage(["a", "b"], observed, executed)
    assert full.exact_coverage(["a", "b"], ["b", "a"], ["a", "b"])


@pytest.mark.parametrize("changed,missing", [(False, False), (True, False), (False, True)])
def test_full_audit_never_substitutes_collection_for_terminal_execution(
        harness, tmp_path, monkeypatch, changed, missing):
    full, _ = harness
    snapshot = dict(sha256="original")
    monkeypatch.setattr(full, "source_snapshot", lambda _: dict(
        sha256="changed" if changed else "original"))
    if not missing:
        full.write(tmp_path / "batch-1-terminal.json", dict(
            exit_code=0, collected=["a", "b"], executed=["a", "b"]))
        (tmp_path / "batch-1.xml").write_text('<testsuites><testsuite tests="2" failures="0" '
            'errors="0" skipped="1" time="5"/></testsuites>', encoding="utf-8")
    result = full.audit(tmp_path, ["a", "b"], ["batch-1"], snapshot, 3)
    assert result["successful"] == (not changed and not missing)
    if not missing:
        assert result["passed"] == result["skipped"] == 1
        assert result["wall_seconds"] == 3 and result["suite_seconds_sum"] == 5
        failed_process = full.audit(tmp_path, ["a", "b"], ["batch-1"], snapshot, 3,
                                    {"batch-1": 1})
        assert not failed_process["successful"]


@pytest.mark.parametrize("fail", [False, True])
def test_model_lanes_have_separate_workspaces_keep_order_and_propagate_failures(
        harness, tmp_path, monkeypatch, fail):
    _, model = harness
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(model, "__file__", str(tmp_path / "scripts/runner.py"))
    monkeypatch.setattr(model, "source_snapshot", lambda _: dict(sha256="source", files={}))
    monkeypatch.setattr(model, "acknowledge_unknowns", lambda *args: None)
    monkeypatch.setattr(model.shutil, "which", lambda _: "existing-npx")
    manifest = {**schedule(), "execution_mode": "PARALLEL_PROJECTS_ISOLATED",
                "evidence_folder": "speedcheck-v1", "classification": "DEVELOPMENT_PERFORMANCE"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    authority = tmp_path / "authority.json"
    authority.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["runner", "--manifest", str(path), "--project-jobs", "3",
                                      "--continuation-authorization", str(authority)])
    calls = []

    def run(command, **kwargs):
        env = kwargs["env"]
        calls.append((env["AGENTTEST_COLLEAGUE_PROJECT"], env["AGENTTEST_COLLEAGUE_ROUND"],
                      env["AGENTTEST_COLLEAGUE_WORKSPACE"]))
        assert "--retries=0" in command and "--workers=1" in command
        return SimpleNamespace(returncode=int(fail and env["AGENTTEST_COLLEAGUE_ROUND"] == "1"))

    monkeypatch.setattr(model.subprocess, "run", run)
    assert model.main() == int(fail)
    assert len(calls) == 6
    workspaces = {}
    for project, round_, workspace in calls:
        workspaces.setdefault(project, []).append((round_, workspace))
    assert len({rows[0][1] for rows in workspaces.values()}) == 3
    assert all([r for r, _ in rows] == ["1", "2"] for rows in workspaces.values())
    report = next((tmp_path / "runs/testing-colleague/speedcheck-v1")
                  .glob("*/scheduler-result.json"))
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["finished"] == 6 and result["unexecuted"] == []
    assert result["failed"] == (3 if fail else 0)
