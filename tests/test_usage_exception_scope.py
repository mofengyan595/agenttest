"""The approved exception covers exactly one archived call; the approved budget is enforced."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import authority  # noqa: E402
import usage_exception  # noqa: E402

from agenttest.report import ModelCallStats  # noqa: E402
from agenttest.task_store import TaskConflict, TaskStore  # noqa: E402

CALL, TASK, SHA = "b" * 32, "a" * 32, "c" * 64
DECISION = "operator authorizes one exception for the archived unknown call"


def archived(tmp_path, calls=1):
    db = TaskStore(tmp_path / "tasks.sqlite3")
    db.create(TASK, "owner", "submission", "project", "identity", "request", "FINISHED", {})
    db.begin_model_call(CALL, TASK)
    db.finish_model_call(CALL, ModelCallStats(calls=1, request_sha256=SHA,
                                             reason="MODEL_TIMEOUT"))
    if calls > 1:
        # A second timeout recorded by an earlier uninterrupted run; the gate already stops
        # new calls, so the fixture writes the journal row it would have produced.
        with db.connect() as connection:
            connection.execute("INSERT INTO model_usage_journal VALUES (?,?,?,?)", (
                "d" * 32, TASK, "UNKNOWN", json.dumps(dict(
                    call_id="d" * 32, calls=1, request_sha256="e" * 64,
                    reason="MODEL_TIMEOUT", total_tokens=None))))
    return db


def test_exception_is_exact_leaves_unknown_and_covers_only_that_call(tmp_path):
    db = archived(tmp_path, calls=2)
    assert db.model_usage_blocked()
    with pytest.raises(SystemExit, match="DECISION_DOES_NOT_MATCH_ARCHIVED_CALL"):
        usage_exception.register(str(tmp_path), CALL, TASK, "e" * 64, DECISION, None)
    with pytest.raises(TaskConflict, match="USAGE_EXCEPTION_CALL_MISMATCH"):
        db.record_usage_exception(dict(call_id=CALL, task_id=TASK, request_sha256="e" * 64,
            source="EXPLICIT_OPERATOR_EXCEPTION", authorization_sha256="f" * 64,
            scope="THIS_CALL_ONLY_PRESERVE_UNKNOWN"))
    assert db.model_usage_blocked()
    audit = usage_exception.register(str(tmp_path), CALL, TASK, SHA, DECISION, None)
    assert audit["limits"]["tokens_recorded"] is False
    assert audit["limits"]["receipt_recorded"] is False
    assert audit["archived_call"]["state_before"] == audit["archived_call"]["state_after"]
    assert audit["archived_call"]["stats"]["total_tokens"] is None
    assert audit["archived_call"]["timeout_evidence"]["reason"] == "MODEL_TIMEOUT"
    # The second unknown call has no authorization of its own, so the gate stays closed.
    assert db.model_usage_blocked()
    usage_exception.register(str(tmp_path), "d" * 32, TASK, "e" * 64, DECISION, None)
    assert not db.model_usage_blocked()


def test_exception_audit_is_written_without_tokens_or_receipt(tmp_path):
    archived(tmp_path)
    path = tmp_path / "audit.json"
    usage_exception.register(str(tmp_path), CALL, TASK, SHA, DECISION, str(path))
    audit = json.loads(path.read_text(encoding="utf-8"))
    assert audit["authorization_sha256"] == usage_exception.hashlib.sha256(
        DECISION.encode("utf-8")).hexdigest()
    assert set(audit["exception"]) == {"call_id", "task_id", "request_sha256", "source",
                                      "authorization_sha256", "scope"}
    assert audit["exception"]["scope"] == "THIS_CALL_ONLY_PRESERVE_UNKNOWN"


def test_approved_budget_and_timeout_are_startable_and_audited():
    assert authority.WRITE_AUTHORITY["model_timeout"] == 45
    assert authority.WRITE_AUTHORITY["budgets"] == dict(max_requests=12, max_seconds=150,
                                                        max_model_calls=2)
    assert authority.startable(dict(authority.WRITE_AUTHORITY, operator_approval="GRANTED"))
    for change in (dict(model_timeout=0), dict(model_timeout=121),
                   dict(budgets=dict(max_requests=12, max_seconds=80, max_model_calls=2))):
        with pytest.raises(PermissionError):
            authority.startable(dict(authority.WRITE_AUTHORITY, operator_approval="GRANTED",
                                     **change))
    with pytest.raises(PermissionError, match="WRITE_AUTHORITY_NOT_GRANTED"):
        authority.startable()
