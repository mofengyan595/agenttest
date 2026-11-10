"""One frozen acceptance position can be claimed exactly once, even across crashes.

The tests use the real filesystem and real subprocesses for the claim, the crash and the
concurrency cases: an in-process mock cannot prove that ``mkdir`` is the atomic token.
"""

import json
import subprocess
import sys
import textwrap
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

FROZEN_DIGEST = "f" * 64
EXECUTING_HEAD = "a" * 40
MANIFEST = "execution-manifest-0000000.json"
BUDGETS = dict(max_requests=12, max_seconds=150, max_model_calls=2)
IDENTITY_DIGEST = "i" * 64
INITIAL = dict(state_sha256="1" * 64, namespace="e2e-freeze-0001", items=0, audit=0)


def claim(folder, key="M2", **extra):
    payload = dict(frozen_digest=FROZEN_DIGEST, executing_head=EXECUTING_HEAD,
                   manifest=MANIFEST, budgets=BUDGETS, identity_digest=IDENTITY_DIGEST,
                   initial=INITIAL)
    payload.update(extra)
    return consumption.claim(folder, key, **payload)


def evidence(verdict="PASS", requests=4, calls=2, unknown=False, case="M2"):
    return dict(case=case, verdict=verdict, metrics=dict(requests=requests),
                product=dict(model_calls=calls,
                             outcome="UNKNOWN" if unknown else "EXECUTED",
                             report_reason=None),
                findings=[], checks=dict(statuses=[200, 201]), task=dict(elapsed=1.5))


def test_a_fresh_claim_is_exclusive_and_records_the_attempt(tmp_path):
    record = claim(tmp_path)
    assert record["case"] == "M2"
    assert len(record["attempt_id"]) == 32
    assert record["frozen_digest"] == FROZEN_DIGEST
    assert record["executing_head"] == EXECUTING_HEAD
    assert record["manifest"] == MANIFEST
    assert record["budgets"] == BUDGETS
    assert record["identity_digest"] == IDENTITY_DIGEST
    assert record["claimed_at"]
    # No credential material may be written into the journal.
    assert "api_key" not in json.dumps(record)
    assert "sk-" not in json.dumps(record)
    with pytest.raises(consumption.PositionConsumed) as refused:
        claim(tmp_path)
    assert "POSITION_ALREADY_CONSUMED_NO_REPLAY:M2:CLAIMED" in str(refused.value)
    assert consumption.status(tmp_path, "M2")["status"] == "CLAIMED"


def test_m2_and_f1_are_independent_positions(tmp_path):
    claim(tmp_path, "M2")
    assert consumption.status(tmp_path, "F1")["status"] == "UNCONSUMED"
    claim(tmp_path, "F1")
    assert consumption.status(tmp_path, "M2")["status"] == "CLAIMED"
    assert consumption.status(tmp_path, "F1")["status"] == "CLAIMED"


def test_completing_one_case_never_rewrites_the_other_case(tmp_path):
    claim(tmp_path, "M2")
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.5)
    before = {path.name: path.read_bytes() for path in (tmp_path / "positions/M2").iterdir()}
    claim(tmp_path, "F1")
    consumption.start(tmp_path, "F1")
    consumption.complete(tmp_path, "F1", verdict="FAIL",
                         evidence=evidence("FAIL", 1, 1, case="F1"),
                         requests=1, model_calls=1, unknown=False, elapsed=2.0)
    after = {path.name: path.read_bytes() for path in (tmp_path / "positions/M2").iterdir()}
    assert before == after
    view = consumption.summary(tmp_path, cases=["M2", "F1"])
    assert view["positions"]["M2"]["status"] == "COMPLETED"
    assert view["positions"]["F1"]["status"] == "COMPLETED"
    assert view["positions"]["M2"]["completed"]["verdict"] == "PASS"


def test_a_zero_request_focus_failure_still_consumes_the_position(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="NEEDS_REVIEW",
                         evidence=dict(case="M2", verdict="NEEDS_REVIEW",
                                       metrics=dict(requests=0),
                                       product=dict(model_calls=2, report_reason="FOCUS_REFUSED"),
                                       findings=[dict(reason="driver_no_target_requests")],
                                       task=dict(elapsed=3.0)),
                         requests=0, model_calls=2, unknown=False, elapsed=3.0)
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "COMPLETED"
    assert row["completed"]["requests"] == 0
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_a_failed_model_call_leaves_an_interrupted_position(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.interrupt(tmp_path, "M2", reason="TimeoutError", detail="model did not answer")
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "INTERRUPTED_OR_UNKNOWN"
    assert row["interrupted"]["reason"] == "TimeoutError"
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)
    # A second interruption never replaces the first explanation.
    consumption.interrupt(tmp_path, "M2", reason="Other", detail="later")
    assert consumption.status(tmp_path, "M2")["interrupted"]["reason"] == "TimeoutError"


def test_a_truncated_claim_record_still_counts_as_consumed(tmp_path):
    folder = tmp_path / "positions" / "M2"
    folder.mkdir(parents=True)
    (folder / "claim.json").write_text('{"case": "M2"', encoding="utf-8")
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "CLAIMED"
    assert row["claim"] is None  # unreadable is still consumed
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_a_failed_final_evidence_write_keeps_the_position_consumed(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    (tmp_path / "positions/M2/evidence.json").mkdir()
    with pytest.raises(consumption.PositionWriteFailed) as failed:
        consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                             requests=4, model_calls=2, unknown=False, elapsed=1.0)
    assert "POSITION_EVIDENCE_WRITE_FAILED:M2:evidence" in str(failed.value)
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "INTERRUPTED_OR_UNKNOWN"
    assert (tmp_path / "positions/M2/write_failure.json").is_file()
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_a_crash_after_the_claim_before_the_model_call_blocks_a_retry(tmp_path):
    script = tmp_path / "crash_claim.py"
    script.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT / 'scripts/rw_lifecycle')!r})
        import consumption
        consumption.claim({str(tmp_path)!r}, "M2", frozen_digest="f" * 64,
                          executing_head="a" * 40, manifest="execution-manifest-0000000.json",
                          budgets={BUDGETS!r}, identity_digest="i" * 64)
        consumption.start({str(tmp_path)!r}, "M2")
        os._exit(9)
        """), encoding="utf-8")
    finished = subprocess.run([sys.executable, str(script)], cwd=ROOT, capture_output=True,
                              text=True)
    assert finished.returncode == 9, finished.stderr
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "RUNNING"
    assert row["signals"] == ["claim.json", "running.json"]
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)
    view = consumption.summary(tmp_path, cases=["M2", "F1"])
    assert view["runnable"] == ["F1"]


def test_a_crash_after_a_real_post_keeps_the_position_consumed(tmp_path):
    script = tmp_path / "crash_post.py"
    script.write_text(textwrap.dedent(f"""
        import http.server, os, sys, threading
        sys.path.insert(0, {str(ROOT / 'scripts/rw_lifecycle')!r})
        import consumption, httpx

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{{}}")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        consumption.claim({str(tmp_path)!r}, "M2", frozen_digest="f" * 64,
                          executing_head="a" * 40, manifest="execution-manifest-0000000.json",
                          budgets={BUDGETS!r}, identity_digest="i" * 64)
        consumption.start({str(tmp_path)!r}, "M2")
        response = httpx.post(f"http://127.0.0.1:{{server.server_address[1]}}/items",
                              json={{}}, timeout=5)
        print("POST", response.status_code, flush=True)
        os._exit(9)
        """), encoding="utf-8")
    finished = subprocess.run([sys.executable, str(script)], cwd=ROOT, capture_output=True,
                              text=True)
    assert finished.returncode == 9, finished.stderr
    assert "POST 201" in finished.stdout
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "RUNNING"  # an unknown write result is never replayed
    assert not (tmp_path / "positions/M2/completed.json").exists()
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_many_processes_racing_for_one_position_have_at_most_one_winner(tmp_path):
    script = tmp_path / "race.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT / 'scripts/rw_lifecycle')!r})
        import consumption
        try:
            consumption.claim({str(tmp_path)!r}, "M2", frozen_digest="f" * 64,
                              executing_head="a" * 40,
                              manifest="execution-manifest-0000000.json",
                              budgets={BUDGETS!r}, identity_digest="i" * 64)
        except consumption.PositionConsumed:
            print("REFUSED", flush=True)
            sys.exit(2)
        print("CLAIMED", flush=True)
        """), encoding="utf-8")
    processes = [subprocess.Popen([sys.executable, str(script)], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(6)]
    outputs = [process.communicate() for process in processes]
    codes = [process.returncode for process in processes]
    claims = [stdout for stdout, _ in outputs].count("CLAIMED\n")
    assert claims == 1
    assert codes.count(0) == 1
    assert codes.count(2) == 5
    recorded = consumption.status(tmp_path, "M2")["claim"]
    assert recorded and recorded["case"] == "M2"


def test_a_legacy_result_file_consumes_every_case_it_mentions(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps(dict(
        verdicts={"M2": "FAIL"}, requests={"M2": 0},
        results=[dict(case="M2", verdict="FAIL", metrics=dict(requests=0))])), encoding="utf-8")
    assert consumption.legacy_consumed(tmp_path, "M2") is True
    assert consumption.legacy_consumed(tmp_path, "F1") is False
    assert consumption.status(tmp_path, "M2")["status"] == "LEGACY_CONSUMED"
    assert "M2" in consumption.summary(tmp_path, cases=["M2", "F1"])["consumed"]


def test_a_corrupt_legacy_file_blocks_every_planned_case(tmp_path):
    (tmp_path / "result.json").write_text('{"results": [', encoding="utf-8")
    assert consumption.legacy_consumed(tmp_path, "M2") is True
    assert consumption.legacy_consumed(tmp_path, "F1") is True


def test_a_summary_is_regenerated_without_any_result_json(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.0)
    assert not (tmp_path / "result.json").exists()
    view = consumption.summary(tmp_path, cases=["M2", "F1", "M1"],
                               consumed_positions=("M1",))
    assert view["positions"]["M2"]["status"] == "COMPLETED"
    assert view["positions"]["M2"]["completed"]["verdict"] == "PASS"
    assert view["positions"]["M1"]["status"] == "HISTORICAL_CONSUMED"
    first = consumption.write_summary(tmp_path, cases=["M2", "F1"])
    second = consumption.write_summary(tmp_path, cases=["M2", "F1"])
    assert first.name == "summary-1.json" and second.name == "summary-2.json"
    payload = json.loads(second.read_text(encoding="utf-8"))
    assert payload["positions"]["M2"]["status"] == "COMPLETED"
    assert payload["result_json"]["present"] is False


def test_m1_stays_historical_consumed(tmp_path):
    row = consumption.status(tmp_path, "M1", consumed_positions=("M1",))
    assert row["status"] == "HISTORICAL_CONSUMED"
    view = consumption.summary(tmp_path, cases=["M1", "M2"], consumed_positions=("M1",))
    assert view["runnable"] == ["M2"]


def test_a_corrupt_completion_marker_is_unknown_and_not_replayable(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.0)
    (tmp_path / "positions/M2/completed.json").write_text("{not json", encoding="utf-8")
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "INTERRUPTED_OR_UNKNOWN"
    assert "completed-not-object" in row["integrity"]
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_a_completion_without_its_evidence_is_unknown(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.0)
    (tmp_path / "positions/M2/evidence.json").unlink()
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "INTERRUPTED_OR_UNKNOWN"
    assert "evidence-missing" in row["integrity"]
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_tampered_evidence_is_unknown(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.0)
    path = tmp_path / "positions/M2/evidence.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metrics"]["requests"] = 9
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                    newline="\n")
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "INTERRUPTED_OR_UNKNOWN"
    assert "evidence-digest-mismatch" in row["integrity"]
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_a_legacy_completion_without_a_digest_is_not_trusted(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    payload = evidence()
    (tmp_path / "positions/M2/evidence.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        newline="\n")
    legacy = dict(schema=1, case="M2", verdict="PASS", requests=4, model_calls=2,
                  unknown=False, evidence="evidence.json")
    (tmp_path / "positions/M2/completed.json").write_text(
        json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "INTERRUPTED_OR_UNKNOWN"
    assert "evidence-digest-missing" in row["integrity"]
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_evidence_without_a_completion_marker_stays_running(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.0)
    (tmp_path / "positions/M2/completed.json").unlink()
    row = consumption.status(tmp_path, "M2")
    # Partial evidence is never presented as a finished report.
    assert row["status"] == "RUNNING"
    assert row.get("completed") is None
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_the_completed_summary_exposes_the_verified_integrity(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.0)
    view = consumption.summary(tmp_path, cases=["M2"])
    row = view["positions"]["M2"]
    assert row["status"] == "COMPLETED"
    assert row["integrity"] == []
    assert row["completed"]["evidence_sha256"]


def test_a_completion_cannot_be_rewritten(tmp_path):
    claim(tmp_path)
    consumption.start(tmp_path, "M2")
    consumption.complete(tmp_path, "M2", verdict="PASS", evidence=evidence(),
                         requests=4, model_calls=2, unknown=False, elapsed=1.0)
    with pytest.raises(consumption.PositionWriteFailed):
        consumption.complete(tmp_path, "M2", verdict="FAIL", evidence=evidence("FAIL", 1, 1),
                             requests=1, model_calls=1, unknown=False, elapsed=1.0)


# --- run(): the claim happens after the gate and before the first task -----------------------

IDENTITY = dict(model="ecnu-max", protocol="chatecnu",
                base_url="https://chat.ecnu.edu.cn/open/api/v1", timeout=45,
                max_output_tokens=4096, max_input_tokens=20000, credentials="PRESENT")
REAL_GUARD = acceptance.guard


def frozen_folder(tmp_path):
    folder = tmp_path / "frozen"
    folder.mkdir()
    model = {key: value for key, value in IDENTITY.items() if key != "credentials"}
    (folder / "freeze.json").write_text(json.dumps(dict(
        source=dict(status="", head="b" * 40), model=model,
        target=dict(base_url="http://127.0.0.1:19097"),
        authority=dict(declaration=dict(model_timeout=45, operator_approval="GRANTED"),
                       digest="x"),
        budgets=BUDGETS)), encoding="utf-8")
    return folder


def authorize_for(folder, key="M2", head=None, **overrides):
    """A test-identity operator record bound to the current commit and the frozen budgets."""
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    values = dict(operator="test-operator", allow_model_calls=True,
                  allow_loopback_writes=True, max_requests=12, max_model_calls=2,
                  max_seconds=150, model_timeout=45)
    values.update(overrides)
    return run_authorization.authorize(
        folder, case=key, executing_head=head or execution_gate.source_snapshot(ROOT)["head"],
        frozen=frozen, **values)


def write_authorization(folder, key, *, head, frozen_digest, budgets, model_timeout=45,
                        allow_model_calls=True, allow_loopback_writes=True):
    """Craft a syntactically valid record for cases where the frozen artifact itself drifts."""
    payload = dict(schema=run_authorization.SCHEMA,
                   authorization_id="t" * 32, case=key, executing_head=head,
                   frozen_sha256=frozen_digest, operator="test-operator",
                   created_at="2026-11-10T00:00:00+0800",
                   allow_model_calls=allow_model_calls,
                   allow_loopback_writes=allow_loopback_writes,
                   budgets=dict(budgets), model_timeout=model_timeout,
                   statement="test record")
    payload["integrity"] = run_authorization._record_integrity(payload)
    target = run_authorization.folder_for(folder)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"authorize-{key}-{head[:7]}-{payload['authorization_id'][:6]}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def run_offline(tmp_path, monkeypatch):
    """Wire run() to an in-process call order recorder instead of a fixture or a model."""
    monkeypatch.setattr(acceptance, "PLAN", plan_v4)
    monkeypatch.setattr(acceptance, "CASES", acceptance.planned_cases())
    monkeypatch.setattr(acceptance, "CONSUMED_POSITIONS", plan_v4.CONSUMED_POSITIONS)
    monkeypatch.setattr(acceptance, "SELECTED", ["M2"])
    monkeypatch.setattr(acceptance, "model_identity", lambda **_: dict(IDENTITY))
    calls = []

    class Fixture:
        def __init__(self):
            self.served = self

        def __enter__(self):
            return self

        def __exit__(self, *error):
            return False

    monkeypatch.setattr(acceptance.preflight, "served", lambda: Fixture())
    monkeypatch.setattr(acceptance.preflight, "reset", lambda namespace: dict(INITIAL))

    def guard(folder, *, identity, initial=None, accept_code_change=None, **kwargs):
        calls.append("guard")
        manifest = folder / MANIFEST
        manifest.write_text("{}", encoding="utf-8")
        return dict(code=dict(executing_head=EXECUTING_HEAD, frozen_head="b" * 40,
                              changed=True),
                    blocking=[], warnings=[], identity=dict(current=dict(IDENTITY)),
                    target=dict(current_base_url="http://127.0.0.1:19097",
                                current_initial=INITIAL["state_sha256"])), manifest

    monkeypatch.setattr(acceptance, "guard", guard)
    monkeypatch.setattr(acceptance, "service",
                        lambda root, tokens: (object(), dict(id="project-1")))

    def run_case(folder, svc, project_id, case, run_id, initial):
        calls.append("task")
        # The claim and the running marker must already be on disk before the task starts.
        assert (folder / "positions/M2/claim.json").is_file()
        assert (folder / "positions/M2/running.json").is_file()
        assert not (folder / "positions/M2/completed.json").exists()
        return evidence()

    monkeypatch.setattr(acceptance, "run_case", run_case)
    return calls


def test_run_claims_before_the_task_and_persists_per_case_evidence(tmp_path, run_offline):
    folder = frozen_folder(tmp_path)
    authorize_for(folder)
    summary = acceptance.run(folder)
    assert run_offline == ["guard", "task"]
    rows = (folder / "positions/M2").iterdir()
    names = {path.name for path in rows}
    assert {"claim.json", "running.json", "evidence.json", "completed.json"} <= names
    assert summary["verdicts"] == {"M2": "PASS"}
    assert summary["requests"] == {"M2": 4}
    # The old all-in-one report is not written; the immutable summary replaces it.
    assert not (folder / "result.json").exists()
    assert sorted(path.name for path in folder.glob("summary-*.json")) == ["summary-1.json"]
    completed = json.loads((folder / "positions/M2/completed.json").read_text(encoding="utf-8"))
    assert completed["verdict"] == "PASS" and completed["requests"] == 4
    with pytest.raises(SystemExit) as refused:
        acceptance.run(folder)
    assert "POSITION_ALREADY_CONSUMED_NO_REPLAY:M2:COMPLETED" in str(refused.value)


def test_the_old_result_shape_never_reached_the_old_guard(tmp_path):
    """The pre-fix guard read ``row["requests"]``; the driver only ever wrote ``metrics``."""
    done = dict(results=[dict(case="M2", verdict="PASS", metrics=dict(requests=4),
                              product=dict(requests=4))])
    (tmp_path / "result.json").write_text(json.dumps(done), encoding="utf-8")
    assert any(row.get("requests") for row in done["results"]) is False  # old check passed
    assert consumption.legacy_consumed(tmp_path, "M2") is True           # new check refuses
    assert consumption.status(tmp_path, "M2")["status"] == "LEGACY_CONSUMED"


def test_a_claim_directory_without_any_file_still_consumes(tmp_path):
    (tmp_path / "positions" / "M2").mkdir(parents=True)
    row = consumption.status(tmp_path, "M2")
    assert row["status"] == "CLAIMED"
    assert row["signals"] == []
    with pytest.raises(consumption.PositionConsumed):
        claim(tmp_path)


def test_status_mode_is_read_only_and_needs_no_freeze(tmp_path, run_offline):
    folder = tmp_path / "runtime"
    folder.mkdir()
    claim(folder, "M2")
    consumption.start(folder, "M2")
    payload = acceptance.status_report(folder)
    assert payload["positions"]["M2"]["status"] == "RUNNING"
    assert payload["positions"]["F1"]["status"] == "UNCONSUMED"
    assert payload["runnable"] == ["F1"]
    # No claim for the read itself, and no evidence that pretends the attempt finished.
    assert consumption.status(folder, "M2")["status"] == "RUNNING"
    assert not (folder / "positions/M2/completed.json").exists()
    assert (folder / "summary-1.json").is_file()


def test_the_status_command_reads_records_without_starting_anything(tmp_path):
    folder = tmp_path / "runtime"
    folder.mkdir()
    claim(folder, "M2")
    script = ROOT / "scripts/rw_lifecycle/acceptance.py"
    finished = subprocess.run(
        [sys.executable, str(script), "status", str(folder), "--plan", "plan_v4"],
        cwd=ROOT, capture_output=True, text=True)
    assert finished.returncode == 0, finished.stderr
    assert '"M2": "CLAIMED"' in finished.stdout
    payload = json.loads((folder / "summary-1.json").read_text(encoding="utf-8"))
    assert payload["positions"]["M2"]["status"] == "CLAIMED"
    assert payload["positions"]["F1"]["status"] == "UNCONSUMED"
    assert payload["positions"]["M1"]["status"] == "HISTORICAL_CONSUMED"


def test_run_refuses_two_positions_in_one_command(tmp_path, run_offline, monkeypatch):
    folder = frozen_folder(tmp_path)
    monkeypatch.setattr(acceptance, "SELECTED", ["M2", "F1"])
    with pytest.raises(SystemExit) as refused:
        acceptance.run(folder)
    assert str(refused.value) == "ONE_POSITION_PER_COMMAND:M2,F1"
    assert not (folder / "positions").exists()


def test_run_refuses_a_legacy_consumed_position_before_the_fixture(tmp_path, run_offline):
    folder = frozen_folder(tmp_path)
    (folder / "result.json").write_text(json.dumps(dict(requests={"M2": 0},
                                                         verdicts={"M2": "NEEDS_REVIEW"})),
                                        encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        acceptance.run(folder)
    assert "POSITION_ALREADY_CONSUMED_NO_REPLAY:M2:LEGACY_CONSUMED" in str(refused.value)
    assert not (folder / "positions").exists()


def test_run_refuses_a_historical_position(tmp_path, run_offline, monkeypatch):
    folder = frozen_folder(tmp_path)
    monkeypatch.setattr(acceptance, "CONSUMED_POSITIONS", ("M1",))
    monkeypatch.setattr(acceptance, "SELECTED", ["M1"])
    with pytest.raises(SystemExit) as refused:
        acceptance.run(folder)
    assert str(refused.value) == "CONSUMED_POSITION_REFUSED:M1"
    assert not (folder / "positions").exists()


def test_the_frozen_gate_refuses_before_a_claim(tmp_path, monkeypatch, run_offline):
    folder = frozen_folder(tmp_path)
    record = execution_gate.snapshot(
        ROOT, plan=plan_v4, cases=acceptance.cases(),
        consumed=plan_v4.CONSUMED_POSITIONS, approved=acceptance.APPROVED,
        checklist=authority.APPROVAL_CHECKLIST, document=authority.document(),
        setup=authority.setup(), initial=INITIAL,
        identity={key: value for key, value in IDENTITY.items() if key != "credentials"},
        base_url=acceptance.APPROVED["base_url"])
    record["rubric_check"] = plan_v4.consistency()
    record["frozen_at"] = "2026-11-09T21:00:00+0800"
    record["model"] = {key: value for key, value in IDENTITY.items() if key != "credentials"}
    record["budgets"] = dict(BUDGETS)
    record["target"] = dict(record["target"], base_url="http://127.0.0.1:19097")
    record["source"] = dict(record["source"], status="")
    (folder / "freeze.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(acceptance, "guard", REAL_GUARD)
    original = execution_gate.source_snapshot

    def clean(root):
        snapshot = original(root)
        snapshot["status"] = ""
        return snapshot

    monkeypatch.setattr(execution_gate, "source_snapshot", clean)

    # A matching record passes the gate; mutate one frozen budget to prove the refusal. The
    # authorization is re-crafted for the drifted artifact so the gate, not the permission
    # check, is the thing under test here.
    drifted = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    drifted["budgets"]["max_requests"] = 13
    (folder / "freeze.json").write_text(json.dumps(drifted, ensure_ascii=False), encoding="utf-8")
    head = execution_gate.source_snapshot(ROOT)["head"]
    write_authorization(folder, "M2", head=head,
                        frozen_digest=run_authorization.digest(
                            (folder / "freeze.json").read_bytes()),
                        budgets=drifted["budgets"])
    with pytest.raises(SystemExit) as refused:
        acceptance.run(folder)
    assert "FROZEN_ARTIFACT_DRIFT" in str(refused.value)
    assert "BUDGETS_CHANGED" in str(refused.value)
    assert not (folder / "positions").exists()

    # A dirty executing tree is refused by the same gate, also before any claim.
    (folder / "freeze.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    write_authorization(folder, "M2", head=head,
                        frozen_digest=run_authorization.digest(
                            (folder / "freeze.json").read_bytes()),
                        budgets=record["budgets"])

    def dirty(root):
        snapshot = original(root)
        snapshot["status"] = " M src/agenttest/executor.py"
        return snapshot

    monkeypatch.setattr(execution_gate, "source_snapshot", dirty)
    with pytest.raises(SystemExit) as refused:
        acceptance.run(folder)
    assert str(refused.value) == "EXECUTION_REQUIRES_CLEAN_TREE"
    assert not (folder / "positions").exists()
