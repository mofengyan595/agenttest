import json

import pytest

from agenttest.report import ModelCallStats
from agenttest.task_store import TaskConflict, TaskStore


def pending(tmp_path):
    db = TaskStore(tmp_path / "tasks.sqlite3")
    db.create("a" * 32, "owner", "submission", "project", "identity", "request", "FINISHED", {})
    db.begin_model_call("b" * 32, "a" * 32)
    stats = ModelCallStats(calls=1, request_sha256="c" * 64, reason="MODEL_TIMEOUT")
    db.finish_model_call("b" * 32, stats)
    receipt = dict(call_id="b" * 32, task_id="a" * 32, request_sha256="c" * 64,
        source="OPERATOR_VERIFIED_PROVIDER_RECORD", provider_reference="provider-record-1",
        evidence_sha256="d" * 64, input_tokens=100, output_tokens=20, total_tokens=120)
    return db, receipt


def test_verified_receipt_unblocks_without_rewriting_original_timeout_or_task(tmp_path):
    db, receipt = pending(tmp_path)
    assert db.model_usage_blocked()
    db.reconcile_model_usage(receipt)
    assert not db.model_usage_blocked()
    assert db.reconcile_model_usage(receipt)["total_tokens"] == 120
    with db.connect() as connection:
        raw = connection.execute("SELECT state,stats FROM model_usage_journal").fetchone()
        assert raw["state"] == "UNKNOWN"
        assert json.loads(raw["stats"])["total_tokens"] is None
    assert db.get("a" * 32, "owner")["summary"] == {}
    restarted = TaskStore(db.path)
    restarted.begin_model_call("e" * 32, "next-task")
    assert restarted.model_usage_blocked()  # A different pending call is never cleared.


@pytest.mark.parametrize("change", [dict(request_sha256="e" * 64), dict(task_id="e" * 32),
    dict(total_tokens=121), dict(input_tokens=-1), dict(input_tokens=True),
    dict(source="MODEL_SUGGESTION"), dict(input_tokens=0, output_tokens=0, total_tokens=0)])
def test_unmatched_invalid_or_model_authored_receipt_never_clears_stop(tmp_path, change):
    db, receipt = pending(tmp_path)
    with pytest.raises(ValueError):
        db.reconcile_model_usage({**receipt, **change})
    assert db.model_usage_blocked()


def test_conflicting_receipt_cannot_replace_a_recorded_provider_result(tmp_path):
    db, receipt = pending(tmp_path)
    db.reconcile_model_usage(receipt)
    with pytest.raises(TaskConflict, match="USAGE_RECEIPT_ALREADY_RECORDED"):
        db.reconcile_model_usage({**receipt, "input_tokens": 101, "total_tokens": 121})


def test_explicit_operator_exception_preserves_unknown_and_does_not_cover_the_next_call(tmp_path):
    db, receipt = pending(tmp_path)
    authorization = {k: receipt[k] for k in ("call_id", "task_id", "request_sha256")}
    authorization.update(source="EXPLICIT_OPERATOR_EXCEPTION", authorization_sha256="f" * 64,
                         scope="THIS_CALL_ONLY_PRESERVE_UNKNOWN")
    with pytest.raises(TaskConflict):
        db.record_usage_exception({**authorization, "request_sha256": "e" * 64})
    assert db.model_usage_blocked()
    db.record_usage_exception(authorization)
    assert not db.model_usage_blocked()
    with db.connect() as connection:
        row = connection.execute("SELECT state,stats FROM model_usage_journal").fetchone()
        assert row["state"] == "UNKNOWN" and json.loads(row["stats"])["total_tokens"] is None
    db.begin_model_call("e" * 32, "next-task")
    db.finish_model_call("e" * 32, ModelCallStats(calls=1))
    assert db.model_usage_blocked()
