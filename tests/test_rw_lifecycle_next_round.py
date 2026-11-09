"""The next frozen round may only select unconsumed positions and the approved budget."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

SCRIPT = str(ROOT / "scripts/rw_lifecycle/acceptance.py")


def drive(folder, *extra, env=None):
    import os

    merged = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, SCRIPT, "dry", str(folder), *extra],
                          cwd=ROOT, capture_output=True, text=True, env=merged)


def test_offline_dry_run_needs_no_credential_file(tmp_path):
    """CI has no ``.env``: configuration validation must not read real credentials."""
    folder = tmp_path / "offline"
    missing = str(tmp_path / "absent.env")
    finished = drive(folder, env={"AGENTTEST_MODEL_ENV": missing})
    assert finished.returncode == 0, finished.stderr
    payload = json.loads((folder / "dry.json").read_text(encoding="utf-8"))
    assert payload["ready"] is True
    assert payload["model_timeout"] == 45


def test_run_refuses_offline_without_a_model_identity(tmp_path):
    """A real run may never fall back to the offline placeholder identity."""
    folder = tmp_path / "offline-run"
    folder.mkdir()
    (folder / "freeze.json").write_text(json.dumps(dict(
        source=dict(status="", head="0" * 40), model=dict(model="ecnu-max"),
        target=dict(base_url="http://127.0.0.1:19097"))), encoding="utf-8")
    import os

    finished = subprocess.run(
        [sys.executable, SCRIPT, "run", str(folder)],
        cwd=ROOT, capture_output=True, text=True,
        env=dict(os.environ, AGENTTEST_MODEL_ENV=str(tmp_path / "absent.env")))
    assert finished.returncode != 0
    assert "MODEL_CREDENTIALS_MISSING" in finished.stderr + finished.stdout


def test_same_model_name_at_a_different_address_is_identity_drift(tmp_path):
    """A frozen model name alone may not authorize a different service address."""
    import acceptance

    frozen = dict(model="ecnu-max", protocol="openai_compatible",
                  base_url="https://approved.example/v1", timeout=45,
                  max_output_tokens=4096, max_input_tokens=65536,
                  api_key="sk-frozen-never-logged")
    moved = {**frozen, "base_url": "https://elsewhere.example/v1"}
    assert acceptance.identity_drift(frozen, moved) == ["base_url"]
    assert acceptance.identity_drift(frozen, {**frozen, "base_url":
                                              "HTTPS://APPROVED.EXAMPLE/v1/"}) == []
    assert acceptance.identity_drift(frozen, {**frozen, "model": "other-max"}) == ["model"]
    assert acceptance.identity_drift(frozen, {**frozen, "timeout": 30}) == ["timeout"]
    assert acceptance.identity_drift(frozen, {**frozen, "protocol": "chatecnu"}) == ["protocol"]
    # Credential contents never participate in the identity comparison.
    assert acceptance.identity_drift(frozen, {**frozen, "api_key": "another-key"}) == []
    normal = acceptance.normalize_identity(frozen)
    assert "api_key" not in normal and normal["base_url"] == "https://approved.example/v1"

    env = tmp_path / "model.env"
    env.write_text("OPENAI_API_KEY=offline-placeholder\n"
                   "OPENAI_BASE_URL=https://elsewhere.example/v1\n"
                   "AGENTTEST_MODEL=ecnu-max\n", encoding="utf-8")
    folder = tmp_path / "drift"
    folder.mkdir()
    (folder / "freeze.json").write_text(json.dumps(dict(
        source=dict(status="", head="0" * 40), model=frozen,
        target=dict(base_url="http://127.0.0.1:19097"))), encoding="utf-8")
    import os

    finished = subprocess.run(
        [sys.executable, SCRIPT, "run", str(folder)],
        cwd=ROOT, capture_output=True, text=True,
        env=dict(os.environ, AGENTTEST_MODEL_ENV=str(env)))
    assert finished.returncode != 0
    assert "MODEL_IDENTITY_DRIFT:base_url" in finished.stderr + finished.stdout
    assert "sk-frozen-never-logged" not in finished.stderr + finished.stdout


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


def test_fourth_round_wording_only_uses_the_approved_literals():
    import plan_v4

    result = plan_v4.consistency()
    assert result["ok"], result["problems"]
    assert plan_v4.CONSUMED_POSITIONS == ("M1",)
    assert {case["key"] for case in plan_v4.FINAL_CASES} == {"M2", "F1"}
    for case in plan_v4.FINAL_CASES:
        assert plan_v4.literals(case["text"]) <= plan_v4.APPROVED_VALUES
    # The guard that would have caught the consumed 4a wording rejects an invented value.
    invented = [dict(plan_v4.FINAL_CASES[0],
                     text="创建名称是 alpha 的条目，读取后删除。")]
    problems = plan_v4.consistency(invented)["problems"]
    assert any(problem.get("reason") == "unapproved_literal"
               and problem.get("detail") == ["alpha"] for problem in problems)
