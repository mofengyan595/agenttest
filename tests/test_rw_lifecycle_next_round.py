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
