"""The next frozen round may only select unconsumed positions and the approved budget."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

SCRIPT = str(ROOT / "scripts/rw_lifecycle/acceptance.py")


def drive(folder, *extra):
    return subprocess.run([sys.executable, SCRIPT, "dry", str(folder), *extra],
                          cwd=ROOT, capture_output=True, text=True)


def test_consumed_positions_can_never_be_selected_again(tmp_path):
    refused = drive(tmp_path / "round", "--cases", "T1")
    assert refused.returncode != 0
    assert "CONSUMED_POSITION_REFUSED:T1" in refused.stderr + refused.stdout
    unknown = drive(tmp_path / "round", "--cases", "T9")
    assert unknown.returncode != 0
    assert "UNKNOWN_CASES:T9" in unknown.stderr + unknown.stdout
    # Refusal happens before the fixture starts, so nothing was created or frozen.
    assert not (tmp_path / "round" / "freeze.json").exists()


def test_approved_dry_run_uses_the_new_budget_and_only_reports_unconsumed_cases(tmp_path):
    folder = tmp_path / "round"
    finished = drive(folder)
    assert finished.returncode == 0, finished.stderr
    payload = json.loads((folder / "dry.json").read_text(encoding="utf-8"))
    assert payload["ready"] is True
    assert payload["budgets"] == dict(max_requests=12, max_seconds=150, max_model_calls=2)
    assert payload["model_timeout"] == 45
    assert payload["consumed_positions"] == ["T1", "T2"]
    assert payload["cases"] == ["T1", "T2", "T3"]
    assert payload["target"]["max_requests"] == 12

def test_third_round_plan_keeps_unconsumed_positions_and_matches_its_rubric():
    import plan_v3

    result = plan_v3.consistency()
    assert result["ok"] is True, result["problems"]
    assert [case["key"] for case in plan_v3.FINAL_CASES] == ["T1V3", "T2V3", "T3"]
    assert plan_v3.CONSUMED_POSITIONS == ()
    expect = {case["key"]: case["expect"] for case in plan_v3.FINAL_CASES}
    assert expect["T1V3"]["statuses"] == [201, 200, 200, 200, 204, 404]
    assert expect["T2V3"]["statuses"] == [201, 200, 409, 204, 404]
    assert expect["T2V3"]["conflict"] == 1
    assert expect["T3"]["statuses"] == [201, 200, 403, 401, 403, 401, 204, 404]
    assert [tuple(item) for item in expect["T3"]["approved_denials"]] == [
        ("getItem", "auditor", 403), ("getItem", None, 401),
        ("updateItem", "auditor", 403), ("updateItem", None, 401)]
    # Fresh wording, but the approved values stay inside the operator-approved set.
    text = " ".join(case["text"] for case in plan_v3.FINAL_CASES[:2])
    assert "draft-" not in text
    assert set(plan_v3.REQUIRED_POLICIES) == set(__import__("plan_v2").REQUIRED_POLICIES)
