"""Register one explicit operator exception for a single unknown model call.

The operator authorises a *call*, not a token estimate: the store only accepts the exact
``call_id``/``task_id``/``request_sha256`` of an existing ``UNKNOWN`` journal row, keeps that
row ``UNKNOWN``, writes no usage figures, and leaves every other call blocked.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

from agenttest.task_store import TaskConflict, TaskStore  # noqa: E402


def journal(db, call_id):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM model_usage_journal WHERE id=?",
                                 (call_id,)).fetchone()
        exceptions = connection.execute(
            "SELECT call_id,at,authorization FROM model_usage_exceptions").fetchall()
    public = None if row is None else dict(
        call_id=row["id"], task_id=row["task_id"], state=row["state"],
        stats=json.loads(row["stats"]),
        exceptions=[dict(call_id=item["call_id"], recorded=item["at"],
                         authorization=json.loads(item["authorization"]))
                    for item in exceptions])
    return public


def register(workspace, call_id, task_id, request_sha256, decision, audit_path):
    """Validate the operator's decision against the archived record, then record it once."""
    store = TaskStore(Path(workspace) / "tasks.sqlite3")
    before = journal(store, call_id)
    if before is None:
        raise SystemExit("CALL_NOT_FOUND")
    if (before["task_id"], before["stats"].get("request_sha256")) != (task_id, request_sha256):
        raise SystemExit("DECISION_DOES_NOT_MATCH_ARCHIVED_CALL")
    authorization_sha256 = hashlib.sha256(decision.encode("utf-8")).hexdigest()
    public = store.record_usage_exception(dict(
        call_id=call_id, task_id=task_id, request_sha256=request_sha256,
        source="EXPLICIT_OPERATOR_EXCEPTION", authorization_sha256=authorization_sha256,
        scope="THIS_CALL_ONLY_PRESERVE_UNKNOWN"))
    after = journal(store, call_id)
    audit = dict(
        decision="usage-exception", operator_decision=decision,
        authorization_sha256=authorization_sha256, exception=public,
        archived_call=dict(call_id=call_id, task_id=task_id, state_before=before["state"],
            state_after=after["state"], request_sha256=request_sha256,
            stats=after["stats"], timeout_evidence={
                key: after["stats"].get(key) for key in (
                    "reason", "http_status", "elapsed_ms", "response_bytes", "total_tokens")}),
        gate=dict(model_usage_blocked_after=store.model_usage_blocked(),
                  other_calls_still_blocked=None),
        limits=dict(tokens_recorded=False, receipt_recorded=False,
                    historical_verdict_changed=False, gate_relaxed_outside_this_call=False))
    if audit_path:
        Path(audit_path).write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--audit", default="")
    args = parser.parse_args()
    try:
        register(args.workspace, args.call_id, args.task_id, args.request_sha256,
                 args.decision, args.audit or None)
    except TaskConflict as error:
        raise SystemExit(str(error)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
