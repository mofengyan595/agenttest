"""The real run needs an external operator record and the shared fixture lock.

Every test is offline: no model endpoint, no business request and no real position. The two
concurrency tests use real child processes, because only the operating system can prove that
the lock is cross-process and that it is dropped when a process dies.
"""

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import acceptance  # noqa: E402
import consumption  # noqa: E402
import execution_gate  # noqa: E402
import plan_v4  # noqa: E402
import run_authorization  # noqa: E402
import run_lock  # noqa: E402

BUDGETS = dict(max_requests=12, max_seconds=150, max_model_calls=2)
INITIAL = dict(state_sha256="1" * 64, namespace="e2e-freeze-0001", items=0, audit=0)
IDENTITY = dict(model="ecnu-max", protocol="chatecnu",
                base_url="https://chat.ecnu.edu.cn/open/api/v1", timeout=45,
                max_output_tokens=4096, max_input_tokens=20000, credentials="PRESENT")
HEAD = execution_gate.source_snapshot(ROOT)["head"]
MANIFEST = "execution-manifest-0000000.json"


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


def authorize(folder, key="M2", head=None, **overrides):
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    values = dict(operator="test-operator", allow_model_calls=True,
                  allow_loopback_writes=True, max_requests=12, max_model_calls=2,
                  max_seconds=150, model_timeout=45)
    values.update(overrides)
    return run_authorization.authorize(folder, case=key, executing_head=head or HEAD,
                                       frozen=frozen, **values)


def crafted(folder, key, *, head=None, budgets=None, model_timeout=45,
            allow_model_calls=True, allow_loopback_writes=True, frozen_digest=None):
    head = head or HEAD
    payload = dict(schema=run_authorization.SCHEMA, authorization_id="c" * 32, case=key,
                   executing_head=head,
                   frozen_sha256=frozen_digest or run_authorization.digest(
                       (folder / "freeze.json").read_bytes()),
                   operator="test-operator", created_at="2026-11-10T00:00:00+0800",
                   allow_model_calls=allow_model_calls,
                   allow_loopback_writes=allow_loopback_writes,
                   budgets=dict(budgets or BUDGETS), model_timeout=model_timeout,
                   statement="test record")
    payload["integrity"] = run_authorization._record_integrity(payload)
    target = run_authorization.folder_for(folder)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"authorize-{key}-{head[:7]}-{payload['authorization_id'][:6]}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def stubbed(monkeypatch):
    """Wire run() to record every step instead of starting a fixture or calling a model."""
    monkeypatch.setattr(acceptance, "PLAN", plan_v4)
    monkeypatch.setattr(acceptance, "CASES", acceptance.planned_cases())
    monkeypatch.setattr(acceptance, "CONSUMED_POSITIONS", plan_v4.CONSUMED_POSITIONS)
    monkeypatch.setattr(acceptance, "SELECTED", ["M2"])
    monkeypatch.setattr(acceptance, "model_identity", lambda **_: dict(IDENTITY))
    steps = []

    class Fixture:
        def __enter__(self):
            return self

        def __exit__(self, *error):
            return False

    def served():
        steps.append("fixture")
        return Fixture()

    monkeypatch.setattr(acceptance.preflight, "served", served)
    monkeypatch.setattr(acceptance.preflight, "reset",
                        lambda namespace: (steps.append("reset"), dict(INITIAL))[1])

    def guard(folder, **kwargs):
        steps.append("guard")
        manifest = folder / MANIFEST
        manifest.write_text("{}", encoding="utf-8")
        return dict(code=dict(executing_head=HEAD), blocking=[], warnings=[]), manifest

    monkeypatch.setattr(acceptance, "guard", guard)
    monkeypatch.setattr(acceptance, "service", lambda root, tokens: (object(), dict(id="p")))
    monkeypatch.setattr(acceptance, "run_case",
                        lambda *args: (steps.append("task"), dict(
                            case="M2", verdict="PASS", metrics=dict(requests=1),
                            product=dict(model_calls=1), findings=[],
                            task=dict(elapsed=1.0)))[1])
    return steps


def refused(output):
    return str(output.value)


def test_an_unauthorized_run_stops_before_the_fixture_and_the_claim(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    with pytest.raises(SystemExit) as output:
        acceptance.run(folder)
    assert refused(output) == "RUN_AUTHORIZATION_MISSING:M2"
    assert stubbed == []
    assert not (folder / "positions").exists()
    # run() never writes an authorization of its own.
    assert run_authorization.candidates(folder, "M2") == []
    assert not (folder / "authorizations").exists()


def test_a_permission_for_m2_never_authorizes_f1(tmp_path, stubbed, monkeypatch):
    folder = frozen_folder(tmp_path)
    authorize(folder, "M2")
    monkeypatch.setattr(acceptance, "SELECTED", ["F1"])
    with pytest.raises(SystemExit) as output:
        acceptance.run(folder)
    assert refused(output) == "RUN_AUTHORIZATION_MISSING:F1"
    assert stubbed == []
    assert not (folder / "positions").exists()


def test_a_permission_for_another_commit_is_refused(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    authorize(folder, "M2", head="c" * 40)
    with pytest.raises(SystemExit) as output:
        acceptance.run(folder)
    assert refused(output) == "RUN_AUTHORIZATION_HEAD_MISMATCH:" + HEAD[:12]
    assert stubbed == []
    assert not (folder / "positions").exists()


def test_the_old_120_second_budget_is_refused(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    with pytest.raises(run_authorization.AuthorizationError) as output:
        authorize(folder, max_seconds=120)
    assert output.value.code == "BUDGET_MISMATCH"
    assert not (folder / "authorizations").exists()

    crafted(folder, "M2", budgets=dict(BUDGETS, max_seconds=120))
    with pytest.raises(SystemExit) as refused_run:
        acceptance.run(folder)
    assert "RUN_AUTHORIZATION_BUDGET_MISMATCH" in refused(refused_run)
    assert stubbed == []
    assert not (folder / "positions").exists()


def test_model_calls_need_an_explicit_consent(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    with pytest.raises(run_authorization.AuthorizationError) as output:
        authorize(folder, allow_model_calls=False)
    assert output.value.code == "MODEL_CALLS_NOT_APPROVED"

    crafted(folder, "M2", allow_model_calls=False)
    with pytest.raises(SystemExit) as refused_run:
        acceptance.run(folder)
    assert refused(refused_run) == "RUN_AUTHORIZATION_MODEL_CALLS_NOT_APPROVED"
    assert stubbed == []
    assert not (folder / "positions").exists()


def test_isolated_writes_need_an_explicit_consent(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    with pytest.raises(run_authorization.AuthorizationError) as output:
        authorize(folder, allow_loopback_writes=False)
    assert output.value.code == "LOOPBACK_WRITES_NOT_APPROVED"

    crafted(folder, "M2", allow_loopback_writes=False)
    with pytest.raises(SystemExit) as refused_run:
        acceptance.run(folder)
    assert refused(refused_run) == "RUN_AUTHORIZATION_LOOPBACK_WRITES_NOT_APPROVED"
    assert stubbed == []
    assert not (folder / "positions").exists()


def test_the_operator_and_every_budget_must_be_supplied(tmp_path):
    folder = frozen_folder(tmp_path)
    with pytest.raises(run_authorization.AuthorizationError) as output:
        run_authorization.authorize(
            folder, case="M2", executing_head=HEAD,
            frozen=json.loads((folder / "freeze.json").read_text(encoding="utf-8")),
            operator="  ", allow_model_calls=True, allow_loopback_writes=True,
            max_requests=12, max_model_calls=2, max_seconds=150, model_timeout=45)
    assert output.value.code == "OPERATOR_REQUIRED"
    with pytest.raises(run_authorization.AuthorizationError) as output:
        authorize(folder, model_timeout=30)
    assert output.value.code == "MODEL_TIMEOUT_MISMATCH"


def test_an_authorization_cannot_be_overwritten(tmp_path):
    folder = frozen_folder(tmp_path)
    path = authorize(folder)
    before = path.read_bytes()
    with pytest.raises(run_authorization.AuthorizationError) as output:
        authorize(folder)
    assert output.value.code == "EXISTS_NO_OVERWRITE"
    assert path.read_bytes() == before


def test_the_authorization_is_checked_before_the_lock(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    with run_lock.execution_lock(folder):
        with pytest.raises(SystemExit) as output:
            acceptance.run(folder)
    # Missing permission wins over a busy fixture: the refusal does not depend on the lock.
    assert refused(output) == "RUN_AUTHORIZATION_MISSING:M2"
    assert stubbed == []


def test_a_second_process_cannot_take_the_fixture_lock(tmp_path):
    folder = tmp_path / "runtime"
    folder.mkdir()
    marker = tmp_path / "held"
    script = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        sys.path.insert(0, {str(ROOT / 'scripts/rw_lifecycle')!r})
        import run_lock
        with run_lock.execution_lock({str(folder)!r}, holder=dict(case="M2")):
            Path({str(marker)!r}).write_text("held", encoding="utf-8")
            time.sleep(2.5)
        """)
    child = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT)
    try:
        deadline = time.time() + 10
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "child never acquired the lock"
        with pytest.raises(run_lock.RunLocked):
            with run_lock.execution_lock(folder, holder=dict(case="F1")):
                pytest.fail("the second process must not enter")
    finally:
        child.wait(timeout=30)
    # The OS drops the lock with the process; no stale marker is cleared by deleting a file.
    with run_lock.execution_lock(folder):
        pass


def test_a_busy_lock_leaves_no_claim_and_starts_nothing(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    authorize(folder)
    marker = tmp_path / "held"
    release = tmp_path / "release"
    script = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        sys.path.insert(0, {str(ROOT / 'scripts/rw_lifecycle')!r})
        import run_lock
        with run_lock.execution_lock({str(folder)!r}, holder=dict(case="M2")):
            Path({str(marker)!r}).write_text("held", encoding="utf-8")
            deadline = time.time() + 20
            while not Path({str(release)!r}).exists() and time.time() < deadline:
                time.sleep(0.05)
        """)
    child = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT)
    try:
        deadline = time.time() + 10
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        with pytest.raises(SystemExit) as output:
            acceptance.run(folder)
        assert refused(output).startswith("EXECUTION_LOCKED:")
        assert stubbed == []
        assert not (folder / "positions").exists()
    finally:
        release.write_text("go", encoding="utf-8")
        child.wait(timeout=30)


def test_a_crash_never_replays_the_position(tmp_path, stubbed):
    folder = frozen_folder(tmp_path)
    authorize(folder)
    frozen_digest = run_authorization.digest((folder / "freeze.json").read_bytes())
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT / 'scripts/rw_lifecycle')!r})
        import consumption
        consumption.claim({str(folder)!r}, "M2", frozen_digest={frozen_digest!r},
                          executing_head={HEAD!r}, manifest="m.json",
                          budgets={BUDGETS!r}, identity_digest="i" * 64)
        consumption.start({str(folder)!r}, "M2")
        os._exit(9)
        """)
    child = subprocess.run([sys.executable, "-c", script], cwd=ROOT)
    assert child.returncode == 9
    with pytest.raises(SystemExit) as output:
        acceptance.run(folder)
    assert "POSITION_ALREADY_CONSUMED_NO_REPLAY:M2:RUNNING" in refused(output)
    assert stubbed == []
    # The new process may take the released lock, but the consumed position stays closed.
    with run_lock.execution_lock(folder):
        pass


def test_environment_defaults_cannot_grant_the_run(tmp_path, stubbed, monkeypatch):
    folder = frozen_folder(tmp_path)
    for name in ("AGENTTEST_APPROVED", "AGENTTEST_OPERATOR_APPROVAL", "AGENTTEST_YES",
                 "WRITE_AUTHORITY", "CI"):
        monkeypatch.setenv(name, "GRANTED")
    with pytest.raises(SystemExit) as output:
        acceptance.run(folder)
    assert refused(output) == "RUN_AUTHORIZATION_MISSING:M2"
    assert stubbed == []
    assert not (folder / "positions").exists()


def test_the_record_carries_no_credential_and_needs_no_model(tmp_path):
    folder = frozen_folder(tmp_path)
    path = authorize(folder)
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert set(payload) == {"schema", "authorization_id", "case", "executing_head",
                            "frozen_sha256", "operator", "created_at", "allow_model_calls",
                            "allow_loopback_writes", "budgets", "model_timeout",
                            "case_attempts", "statement", "note", "integrity"}
    assert "sk-" not in text and "api_key" not in text.lower()
    assert payload["budgets"] == BUDGETS and payload["model_timeout"] == 45
    assert payload["case"] == "M2" and payload["executing_head"] == HEAD
    assert not (folder / "positions").exists()


def test_status_works_without_any_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "PLAN", plan_v4)
    monkeypatch.setattr(acceptance, "CASES", acceptance.planned_cases())
    monkeypatch.setattr(acceptance, "CONSUMED_POSITIONS", plan_v4.CONSUMED_POSITIONS)
    folder = tmp_path / "runtime"
    folder.mkdir()
    consumption.claim(folder, "M2", frozen_digest="f" * 64, executing_head="a" * 40,
                      manifest="m.json", budgets=BUDGETS, identity_digest="i" * 64)
    payload = acceptance.status_report(folder)
    assert payload["positions"]["M2"]["status"] == "CLAIMED"
    assert payload["positions"]["F1"]["status"] == "UNCONSUMED"
    assert payload["runnable"] == ["F1"]
    assert run_authorization.candidates(folder, "M2") == []
