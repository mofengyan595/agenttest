"""Real-guard integration: a selected position must not look like a frozen drift.

These tests call ``acceptance.guard`` itself (no monkeypatch of the gate) on a temporary
frozen record built from the real plan, and then run ``acceptance.run`` end to end with only
the fixture and the model service stubbed. A genuine change to either case, the rubric, the
budgets or the model identity must still be refused.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import acceptance  # noqa: E402
import authority  # noqa: E402
import consumption  # noqa: E402
import execution_gate  # noqa: E402
import plan_v4  # noqa: E402
import run_authorization  # noqa: E402

INITIAL = dict(state_sha256="1" * 64, namespace="e2e-freeze-0001", items=0, audit=0)
IDENTITY = acceptance.model_identity(require_credentials=False)


def configure(monkeypatch, selected):
    monkeypatch.setattr(acceptance, "PLAN", plan_v4)
    monkeypatch.setattr(acceptance, "CASES", acceptance.planned_cases())
    monkeypatch.setattr(acceptance, "CONSUMED_POSITIONS", plan_v4.CONSUMED_POSITIONS)
    monkeypatch.setattr(acceptance, "SELECTED", list(selected))


def offline_identity():
    return dict(IDENTITY)


def complete_record():
    """The same shape ``freeze.json`` has, built from the real plan with both positions."""
    record = copy.deepcopy(execution_gate.snapshot(
        ROOT, plan=plan_v4, cases=acceptance.planned_cases(),
        consumed=plan_v4.CONSUMED_POSITIONS, approved=acceptance.APPROVED,
        checklist=authority.APPROVAL_CHECKLIST, document=authority.document(),
        setup=authority.setup(), identity=offline_identity(), initial=INITIAL,
        base_url=acceptance.APPROVED["base_url"]))
    # The record itself must claim a clean source; the test checkout may be mid-edit.
    record["source"]["status"] = ""
    return record


def frozen_folder(tmp_path, record):
    folder = tmp_path / "frozen"
    folder.mkdir()
    (folder / "freeze.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return folder


def case(record, key):
    return next(item for item in record["requirements"]["cases"] if item["key"] == key)


def clean_snapshot(monkeypatch):
    """The guard must not depend on this test checkout's own uncommitted files."""
    original = execution_gate.source_snapshot

    def snapshot(root):
        value = original(root)
        value["status"] = ""
        return value

    monkeypatch.setattr(execution_gate, "source_snapshot", snapshot)
    return execution_gate.source_snapshot(ROOT)["head"]


def run_gate(folder, initial=INITIAL, enforce=False):
    return acceptance.guard(folder, identity=offline_identity(), initial=initial,
                            write=False, enforce_code_change=enforce)


def refused(value):
    return str(value.value)


def test_selecting_m2_passes_the_real_guard_on_the_complete_record(tmp_path, monkeypatch):
    configure(monkeypatch, ["M2"])
    folder = frozen_folder(tmp_path, complete_record())
    report, _manifest = run_gate(folder)
    assert report["blocking"] == []


def test_selecting_f1_passes_the_real_guard_on_the_complete_record(tmp_path, monkeypatch):
    configure(monkeypatch, ["F1"])
    folder = frozen_folder(tmp_path, complete_record())
    report, _manifest = run_gate(folder)
    assert report["blocking"] == []


def test_no_selection_also_matches_the_complete_record(tmp_path, monkeypatch):
    configure(monkeypatch, ["M2", "F1"])
    folder = frozen_folder(tmp_path, complete_record())
    report, _manifest = run_gate(folder)
    assert report["blocking"] == []


def test_changing_the_m2_text_is_refused(tmp_path, monkeypatch):
    configure(monkeypatch, ["M2"])
    record = complete_record()
    case(record, "M2")["text"] += " 追加"
    folder = frozen_folder(tmp_path, record)
    with pytest.raises(SystemExit) as output:
        run_gate(folder)
    assert refused(output) == "FROZEN_ARTIFACT_DRIFT:CASES_CHANGED"


def test_changing_the_unselected_f1_text_is_still_refused(tmp_path, monkeypatch):
    """The regression: selecting M2 must not hide a genuine change in F1."""
    configure(monkeypatch, ["M2"])
    record = complete_record()
    case(record, "F1")["text"] += " 追加"
    folder = frozen_folder(tmp_path, record)
    with pytest.raises(SystemExit) as output:
        run_gate(folder)
    assert refused(output) == "FROZEN_ARTIFACT_DRIFT:CASES_CHANGED"


def test_changing_the_rubric_is_refused(tmp_path, monkeypatch):
    configure(monkeypatch, ["M2"])
    record = complete_record()
    record["requirements"]["rubric"] = copy.deepcopy(record["requirements"]["rubric"])
    record["requirements"]["rubric"]["M2"]["items"] = record["requirements"]["rubric"][
        "M2"]["items"] + [["changed", "changed"]]
    folder = frozen_folder(tmp_path, record)
    with pytest.raises(SystemExit) as output:
        run_gate(folder)
    assert refused(output) == "FROZEN_ARTIFACT_DRIFT:RUBRIC_CHANGED"


def test_changing_a_budget_is_refused(tmp_path, monkeypatch):
    configure(monkeypatch, ["M2"])
    record = complete_record()
    record["budgets"]["max_requests"] = 13
    folder = frozen_folder(tmp_path, record)
    with pytest.raises(SystemExit) as output:
        run_gate(folder)
    assert "BUDGETS_CHANGED" in refused(output)


def test_changing_the_model_identity_is_refused(tmp_path, monkeypatch):
    configure(monkeypatch, ["M2"])
    record = complete_record()
    record["model"]["base_url"] = "https://example.invalid/open/api/v1"
    folder = frozen_folder(tmp_path, record)
    with pytest.raises(SystemExit) as output:
        run_gate(folder)
    assert "MODEL_IDENTITY_DRIFT" in refused(output)


def test_a_dirty_tree_is_refused_in_run_mode(tmp_path, monkeypatch):
    configure(monkeypatch, ["M2"])
    folder = frozen_folder(tmp_path, complete_record())
    original = execution_gate.source_snapshot

    def dirty(root):
        value = original(root)
        value["status"] = " M src/agenttest/executor.py"
        return value

    monkeypatch.setattr(execution_gate, "source_snapshot", dirty)
    with pytest.raises(SystemExit) as output:
        run_gate(folder, enforce=True)
    assert refused(output) == "EXECUTION_REQUIRES_CLEAN_TREE"


# --- end to end: one selected position runs, the other stays unconsumed ---------------------


def stubs(monkeypatch, expected_case):
    configure(monkeypatch, [expected_case])
    monkeypatch.setattr(acceptance, "model_identity", lambda **_: dict(IDENTITY))
    steps = []

    class Fixture:
        def __enter__(self):
            return self

        def __exit__(self, *error):
            return False

    monkeypatch.setattr(acceptance.preflight, "served", lambda: Fixture())
    monkeypatch.setattr(acceptance.preflight, "reset",
                        lambda namespace: dict(INITIAL))
    monkeypatch.setattr(acceptance, "service",
                        lambda root, tokens: (object(), dict(id="project-1")))

    def run_case(folder, svc, project_id, item, run_id, initial):
        steps.append(item["key"])
        assert (folder / f"positions/{item['key']}/claim.json").is_file()
        return dict(case=item["key"], verdict="PASS", metrics=dict(requests=1),
                    product=dict(model_calls=1, outcome="EXECUTED"), findings=[],
                    checks=dict(statuses=[201]),
                    task=dict(elapsed=1.0))

    monkeypatch.setattr(acceptance, "run_case", run_case)
    return steps


def authorize_for(folder, key, head):
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    return run_authorization.authorize(
        folder, case=key, executing_head=head, frozen=frozen, operator="test-operator",
        allow_model_calls=True, allow_loopback_writes=True, max_requests=12,
        max_model_calls=2, max_seconds=150, model_timeout=45)


def test_running_m2_consumes_only_m2(tmp_path, monkeypatch):
    steps = stubs(monkeypatch, "M2")
    folder = frozen_folder(tmp_path, complete_record())
    head = clean_snapshot(monkeypatch)
    authorize_for(folder, "M2", head)
    summary = acceptance.run(folder)
    assert steps == ["M2"]
    assert summary["verdicts"] == {"M2": "PASS"}
    assert consumption.status(folder, "M2")["status"] == "COMPLETED"
    assert consumption.status(folder, "F1")["status"] == "UNCONSUMED"
    assert not (folder / "positions/F1").exists()
    assert (folder / f"execution-manifest-{head[:7]}.json").is_file()


def test_running_f1_consumes_only_f1(tmp_path, monkeypatch):
    steps = stubs(monkeypatch, "F1")
    folder = frozen_folder(tmp_path, complete_record())
    head = clean_snapshot(monkeypatch)
    authorize_for(folder, "F1", head)
    summary = acceptance.run(folder)
    assert steps == ["F1"]
    assert summary["verdicts"] == {"F1": "PASS"}
    assert consumption.status(folder, "F1")["status"] == "COMPLETED"
    assert consumption.status(folder, "M2")["status"] == "UNCONSUMED"
    assert not (folder / "positions/M2").exists()


def test_a_gate_failure_never_creates_a_consumption_record(tmp_path, monkeypatch):
    steps = stubs(monkeypatch, "M2")
    record = complete_record()
    record["requirements"]["rubric"] = copy.deepcopy(record["requirements"]["rubric"])
    record["requirements"]["rubric"]["M2"]["items"] = record["requirements"]["rubric"][
        "M2"]["items"] + [["changed", "changed"]]
    folder = frozen_folder(tmp_path, record)
    head = clean_snapshot(monkeypatch)
    authorize_for(folder, "M2", head)
    with pytest.raises(SystemExit) as output:
        acceptance.run(folder)
    assert "FROZEN_ARTIFACT_DRIFT:RUBRIC_CHANGED" in refused(output)
    assert steps == []
    assert not (folder / "positions").exists()


def test_a_run_without_its_own_authorization_is_refused(tmp_path, monkeypatch):
    steps = stubs(monkeypatch, "M2")
    folder = frozen_folder(tmp_path, complete_record())
    clean_snapshot(monkeypatch)
    with pytest.raises(SystemExit) as output:
        acceptance.run(folder)
    assert refused(output) == "RUN_AUTHORIZATION_MISSING:M2"
    assert steps == []
    assert not (folder / "positions").exists()
