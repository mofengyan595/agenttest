"""The frozen v4 record is only usable when the running code proves it still matches."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import acceptance  # noqa: E402
import authority  # noqa: E402
import execution_gate  # noqa: E402
import plan_v4  # noqa: E402

INITIAL = dict(state_sha256="1" * 64, namespace="e2e-freeze-0001", items=0, audit=0)


@pytest.fixture(autouse=True)
def offline_identity(tmp_path, monkeypatch):
    """No real credential file: the identity under test is the deterministic offline one."""
    monkeypatch.setenv("AGENTTEST_MODEL_ENV", str(tmp_path / "absent.env"))
    monkeypatch.setattr(acceptance, "PLAN", plan_v4)
    monkeypatch.setattr(acceptance, "CASES", acceptance.planned_cases())
    monkeypatch.setattr(acceptance, "CONSUMED_POSITIONS", plan_v4.CONSUMED_POSITIONS)
    monkeypatch.setattr(acceptance, "SELECTED",
                        [case["key"] for case in acceptance.planned_cases()])


def frozen_view():
    """A freeze-shaped record built from the live plan and approved scope."""
    identity = acceptance.model_identity(require_credentials=False)
    view = execution_gate.snapshot(
        ROOT, plan=plan_v4, cases=acceptance.cases(),
        consumed=plan_v4.CONSUMED_POSITIONS, approved=acceptance.APPROVED,
        checklist=authority.APPROVAL_CHECKLIST, document=authority.document(),
        setup=authority.setup(), identity=identity, initial=INITIAL,
        base_url=acceptance.APPROVED["base_url"])
    view["rubric_check"] = plan_v4.consistency()
    view["frozen_at"] = "2026-11-09T21:00:00+0800"
    return view


def current_view(identity=None, initial=INITIAL):
    return acceptance.current_view(identity or acceptance.model_identity(False), initial)


def frozen_folder(tmp_path, mutate=None):
    folder = tmp_path / "frozen"
    folder.mkdir()
    frozen = frozen_view()
    if mutate:
        mutate(frozen)
    (folder / "freeze.json").write_text(json.dumps(frozen, ensure_ascii=False), encoding="utf-8")
    return folder, frozen


def codes(report):
    return [item["reason"] for item in report["blocking"]]


def test_a_matching_view_has_no_blocking_drift():
    frozen = frozen_view()
    report = execution_gate.compare(frozen, current_view())
    assert report["allowed"] is True
    assert report["blocking"] == []
    assert report["code"]["changed"] is False
    assert report["identity"]["drift"] == []
    assert report["target"]["frozen_initial"] == INITIAL["state_sha256"]


def test_changed_wording_rubric_budget_or_plan_blocks():
    frozen = frozen_view()
    changed = json.loads(json.dumps(frozen))
    changed["requirements"]["cases"][0]["text"] += "并且立即再读一次。"
    changed["requirements"]["digest"] = execution_gate.digest(changed["requirements"]["cases"])
    assert "CASES_CHANGED" in codes(execution_gate.compare(changed, current_view()))

    rubric = json.loads(json.dumps(frozen))
    rubric["requirements"]["rubric"]["M2"]["audit"] = ["POST 201"]
    assert "RUBRIC_CHANGED" in codes(execution_gate.compare(rubric, current_view()))

    budget = json.loads(json.dumps(frozen))
    budget["budgets"]["max_requests"] = 13
    assert "BUDGETS_CHANGED" in codes(execution_gate.compare(budget, current_view()))

    plan = json.loads(json.dumps(frozen))
    plan["requirements"]["plan_sha256"] = "0" * 64
    assert "PLAN_FILE_CHANGED" in codes(execution_gate.compare(plan, current_view()))

    contract = json.loads(json.dumps(frozen))
    contract["contract"]["setup_sha256"] = "0" * 64
    assert "CONTRACT_SETUP_CHANGED" in codes(execution_gate.compare(contract, current_view()))

    target = json.loads(json.dumps(frozen))
    target["target"]["initial"]["state_sha256"] = "2" * 64
    assert "INITIAL_STATE_CHANGED" in codes(execution_gate.compare(target, current_view()))


def test_a_stripped_freeze_record_cannot_bypass_the_gate():
    frozen = frozen_view()
    stripped = json.loads(json.dumps(frozen))
    stripped.pop("requirements")
    report = execution_gate.compare(stripped, current_view())
    assert report["allowed"] is False
    assert set(codes(report)) == {"FROZEN_DECLARATION_MISSING"}
    assert "requirements.cases" in [item["detail"] for item in report["blocking"]]


def test_a_code_upgrade_is_auditable_only_when_the_executing_commit_is_named(tmp_path,
                                                                            monkeypatch):
    original = execution_gate.source_snapshot

    def clean(root):
        snapshot = original(root)
        snapshot["status"] = ""
        return snapshot

    monkeypatch.setattr(execution_gate, "source_snapshot", clean)

    def older(frozen):
        frozen["source"]["head"] = "0" * 40
        frozen["source"]["src_sha256"] = "0" * 64

    folder, frozen = frozen_folder(tmp_path, older)
    report = execution_gate.compare(frozen, current_view())
    assert report["allowed"] is True
    assert report["code"]["changed"] is True
    assert {"SOURCE_CHANGED"} <= {item["reason"] for item in report["warnings"]}

    with pytest.raises(SystemExit) as refused:
        acceptance.guard(folder, identity=acceptance.model_identity(False), initial=INITIAL)
    assert str(refused.value).startswith("CODE_DRIFT_UNRECORDED:")

    head = execution_gate.source_snapshot(ROOT)["head"]
    report, manifest = acceptance.guard(folder, identity=acceptance.model_identity(False),
                                        initial=INITIAL, accept_code_change=head[:7])
    payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
    assert payload["frozen_head"] == "0" * 40
    assert payload["executing_head"] == head
    assert payload["accept_code_change"] == head[:7]
    assert payload["source"]["changed"] is True
    assert payload["source"]["changed_files"]
    # A synthetic frozen head cannot be diffed; the summary must still account for every line.
    assert sum(payload["source"]["change_summary"].values()) == len(
        payload["source"]["changed_files"])
    assert payload["acceptance_conditions"]["positions"] == ["M2", "F1"]
    digests = payload["artifact_digests"]
    assert digests["authority"]["executing"] != digests["cases"]["executing"]
    assert len(digests["authority"]["executing"]) == 64


def test_the_manifest_is_never_overwritten(tmp_path):
    folder, _ = frozen_folder(tmp_path)
    first = execution_gate.write_manifest(folder, dict(executing_head="a" * 40))
    second = execution_gate.write_manifest(folder, dict(executing_head="a" * 40))
    assert first != second and first.exists() and second.exists()
    assert first.name == "execution-manifest-aaaaaaa.json"
    assert second.name == "execution-manifest-aaaaaaa-2.json"


def test_a_previous_request_or_a_consumed_position_blocks_its_case():
    frozen = frozen_view()
    report = execution_gate.compare(frozen, current_view())
    manifest = "execution-manifest-0000000.json"
    checklist = execution_gate.case_checklist(
        frozen, report, cases=acceptance.cases(), consumed=("M1",), manifest=manifest,
        identity_offline=True, result=dict(results=[dict(case="M2", requests=4)]))
    assert checklist["cases"]["M2"]["verdict"] == "BLOCKED"
    items = {row["item"]: row["verdict"] for row in checklist["cases"]["M2"]["items"]}
    assert items["no_replay_of_unknown_writes"] == "BLOCKED"
    assert items["position_unconsumed"] == "READY"

    consumed = execution_gate.case_checklist(
        frozen, report, cases=acceptance.cases(), consumed=("M1", "F1"), manifest=manifest,
        identity_offline=True, result=None)
    assert consumed["cases"]["F1"]["verdict"] == "BLOCKED"
    assert consumed["verdict"] == "BLOCKED"


def test_the_offline_checklist_is_ready_except_for_the_unproven_model_identity():
    frozen = frozen_view()
    report = execution_gate.compare(frozen, current_view())
    checklist = execution_gate.case_checklist(
        frozen, report, cases=acceptance.cases(), consumed=("M1",),
        manifest="execution-manifest-0000000.json", identity_offline=True, result=None)
    assert set(checklist["cases"]) == {"M2", "F1"}
    for case in checklist["cases"].values():
        items = {row["item"]: row["verdict"] for row in case["items"]}
        assert items["position_unconsumed"] == "READY"
        assert items["writes_within_scope"] == "READY"
        assert items["budgets_match_freeze"] == "READY"
        assert items["initial_state_matches"] == "READY"
        assert items["no_replay_of_unknown_writes"] == "READY"
        assert items["model_identity_matches"] == "NEEDS_REVIEW"
        assert case["verdict"] == "NEEDS_REVIEW"


def real_initial_state(tmp_path):
    """The fixture's own reset hash, captured in a subprocess like the other fixture tests."""
    import subprocess

    helper = tmp_path / "initial.py"
    helper.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{ROOT / 'scripts/rw_lifecycle'}')\n"
        "import preflight\n"
        "with preflight.served():\n"
        "    print(preflight.reset('e2e-freeze-0001')['state_sha256'])\n", encoding="utf-8")
    finished = subprocess.run([sys.executable, str(helper)], cwd=ROOT, capture_output=True,
                              text=True)
    assert finished.returncode == 0, finished.stderr
    return finished.stdout.strip()


def test_preflight_writes_an_exclusive_report_without_executing_a_position(tmp_path):
    import os
    import subprocess

    folder, frozen = frozen_folder(tmp_path)
    frozen["target"]["initial"]["state_sha256"] = real_initial_state(tmp_path)
    (folder / "freeze.json").write_text(json.dumps(frozen, ensure_ascii=False), encoding="utf-8")
    script = ROOT / "scripts/rw_lifecycle/acceptance.py"
    env = dict(os.environ, AGENTTEST_MODEL_ENV=str(tmp_path / "absent.env"))
    command = [sys.executable, str(script), "preflight", str(folder), "--plan", "plan_v4"]
    finished = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env)
    assert finished.returncode == 0, finished.stderr
    assert "M2" in finished.stdout and "F1" in finished.stdout
    records = sorted(folder.glob("preflight-*.json"))
    assert len(records) == 1
    assert sorted(folder.glob("execution-manifest-*.json"))
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["checklist"]["verdict"] in {"READY", "NEEDS_REVIEW"}
    assert payload["manifest"].startswith("execution-manifest-")
    assert "M2" in payload["checklist"]["cases"]
    assert "F1" in payload["checklist"]["cases"]
    # No business request was sent: the loopback audit stays empty in the report.
    assert payload["report"]["allowed"] is True

    again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env)
    assert again.returncode != 0
    assert "PREFLIGHT_RECORD_EXISTS_NO_OVERWRITE" in again.stderr + again.stdout


def test_a_dirty_tree_cannot_be_executed_but_can_be_reported(tmp_path, monkeypatch):
    folder, _ = frozen_folder(tmp_path)
    original = execution_gate.source_snapshot

    def dirty(root):
        snapshot = original(root)
        snapshot["status"] = " M src/agenttest/executor.py"
        return snapshot

    monkeypatch.setattr(execution_gate, "source_snapshot", dirty)
    identity = acceptance.model_identity(False)
    with pytest.raises(SystemExit) as refused:
        acceptance.guard(folder, identity=identity, initial=INITIAL)
    assert str(refused.value) == "EXECUTION_REQUIRES_CLEAN_TREE"
    # Reporting mode records the warning instead of refusing.
    report, manifest = acceptance.guard(folder, identity=identity, initial=INITIAL,
                                        enforce_code_change=False)
    assert {"EXECUTING_TREE_DIRTY"} <= {item["reason"] for item in report["warnings"]}
    assert manifest.exists()


def test_freeze_refuses_to_overwrite_a_record(tmp_path):
    folder, _ = frozen_folder(tmp_path)
    with pytest.raises(SystemExit) as refused:
        acceptance.freeze(folder)
    assert str(refused.value) == "FREEZE_RECORD_EXISTS_NO_OVERWRITE"
