"""SQLite facts for one approved tool and its original scenario runs."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from agenttest.report import AttemptEvidence, fingerprint
from agenttest.task_store import TaskConflict

PREPARED = "PREPARED"
DISPATCHED = "DISPATCHED"
RESPONSE = "RESPONSE_RECORDED"
EVIDENCE = "EVIDENCE_COMMITTED"
KNOWN = (RESPONSE, EVIDENCE)


class StepRecoveryBlocked(TaskConflict):
    """A recovery decision refused to use or send a request."""


@dataclass(frozen=True)
class StepScope:
    db: object
    task_id: str
    owner: str
    tool_key: str
    group_id: str
    case_id: str
    run_id: str
    authority: str
    plan_sha256: str
    active_seconds: Callable[[], float] = lambda: 0.0


class StepJournal:
    def __init__(self, scope: StepScope, ledger=None):
        self.scope = scope
        self.ledger = ledger
        self.prior = {(row["step_id"], row["attempt"]): row
                      for row in tool_attempts(scope.db, scope.task_id, scope.tool_key)
                      if row["run_id"] == scope.run_id}
        self.remaining = set(self.prior)
        self.hold_report = bool(self.remaining)

    def boundary(self, phase, result):
        """Hook for the isolated process-exit fixtures."""

    def attempt(self, result, evidence, request, kwargs, vault):
        scope = self.scope
        key = (result.step_id, evidence.attempt)
        request_hash = fingerprint(dict(method=request.method, path=request.url.path,
            query=vault.public_response(kwargs.get("params")),
            body=vault.public_response(kwargs.get("json")),
            upload={key: [item[0], hashlib.sha256(item[1]).hexdigest(), item[2]]
                    for key, item in kwargs.get("files", {}).items()},
            headers={key: value for key, value in vault.public_response(
                dict(kwargs.get("headers", {}))).items() if not vault.contains_secret(value)}))
        identity = dict(group_id=scope.group_id, case_id=scope.case_id, method=request.method,
            operation_id=result.operation_id, resource_sha256=fingerprint(request.url.path),
            request_sha256=request_hash, policy_sha256=result.policy_sha256,
            plan_sha256=scope.plan_sha256, authority=scope.authority,
            attempt_id=fingerprint((scope.task_id, scope.run_id, *key)))
        encoded = json.dumps(identity, sort_keys=True)
        with self.write() as db:
            row = db.execute("SELECT * FROM http_execution WHERE task_id=? AND run_id=? "
                             "AND step_id=? AND attempt=?", self._params(key)).fetchone()
            if row is not None:
                if key not in self.remaining:
                    raise StepRecoveryBlocked("STEP_ATTEMPT_ALREADY_CONSUMED")
                if row["tool_key"] != scope.tool_key or row["identity"] != encoded:
                    raise StepRecoveryBlocked("STEP_REQUEST_IDENTITY_CHANGED")
                self.remaining.discard(key)
                if row["phase"] in KNOWN:
                    receipt = load_receipt(row)
                    if not receipt.get("replayable"):
                        raise StepRecoveryBlocked("STEP_RESPONSE_REQUIRES_REVIEW")
                    if self.ledger is not None:
                        if self.ledger.requests >= self.ledger.max_requests:
                            raise StepRecoveryBlocked("STEP_REQUEST_BUDGET_CHANGED")
                        self.ledger.requests += 1
                    return AttemptEvidence.model_validate(receipt["attempt"]), receipt["body"]
                if row["phase"] != PREPARED:
                    raise StepRecoveryBlocked("DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW")
                if self.remaining:
                    raise StepRecoveryBlocked("STEP_RECEIPT_ORDER_MISMATCH")
            elif self.remaining:
                raise StepRecoveryBlocked("STEP_RECEIPT_ORDER_MISMATCH")
            else:
                self.boundary("BEFORE_PREPARED", result)
                db.execute("INSERT INTO http_execution VALUES (?,?,?,?,?,?,?,NULL,?)",
                           (scope.task_id, scope.tool_key, scope.run_id, *key, PREPARED, encoded,
                            scope.active_seconds()))
        self.boundary(PREPARED, result)
        self.move(key, PREPARED, DISPATCHED)
        self.hold_report = False
        self.boundary(DISPATCHED, result)
        return None

    def response(self, result, evidence, body, vault):
        self.boundary("RESPONSE_RECEIVED", result)
        public = vault.public_response(body)
        replayable = fingerprint(public) == fingerprint(body) and not vault.contains_secret(body)
        receipt = dict(attempt=evidence.model_dump(mode="json"), replayable=replayable,
                       body=public if replayable else None)
        receipt["fingerprint"] = fingerprint(receipt)
        self.move((result.step_id, evidence.attempt), DISPATCHED, RESPONSE,
                  json.dumps(receipt, ensure_ascii=False, allow_nan=False))
        self.boundary(RESPONSE, result)

    def evidence(self, result):
        for item in result.attempts:
            if item.status_code is not None and item.error_code is None:
                self.move((result.step_id, item.attempt), RESPONSE, EVIDENCE, idempotent=True)
        self.boundary(EVIDENCE, result)

    def finish_projection(self):
        if self.remaining:
            raise StepRecoveryBlocked("STEP_RECEIPT_ORDER_MISMATCH")
        self.hold_report = False

    def _params(self, key):
        return (self.scope.task_id, self.scope.run_id, *key)

    def _guard(self, db):
        row = self.scope.db._owned_row(db, self.scope.task_id, self.scope.owner)
        if row["state"] != "RUNNING":
            raise StepRecoveryBlocked("STEP_TASK_NOT_RUNNING")

    def move(self, key, previous, next_phase, receipt=None, *, idempotent=False):
        with self.write() as db:
            changed = db.execute("UPDATE http_execution SET phase=?,receipt=COALESCE(?,receipt), "
                "active_seconds=MAX(active_seconds,?) "
                "WHERE task_id=? AND run_id=? AND step_id=? AND attempt=? AND phase=?",
                (next_phase, receipt, self.scope.active_seconds(), *self._params(key), previous))
            if changed.rowcount != 1:
                row = db.execute("SELECT phase FROM http_execution WHERE task_id=? AND run_id=? "
                                 "AND step_id=? AND attempt=?", self._params(key)).fetchone()
                if not idempotent or row is None or row["phase"] != next_phase:
                    raise StepRecoveryBlocked("STEP_LEDGER_TRANSITION_INVALID")

    @contextmanager
    def write(self):
        try:
            with self.scope.db.connect() as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                self._guard(db)
                yield db
        except (sqlite3.Error, OSError):
            raise StepRecoveryBlocked("STEP_LEDGER_UNAVAILABLE") from None


def tool_attempts(db, task_id, tool_key):
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM http_execution WHERE task_id=? AND tool_key=? ORDER BY rowid",
            (task_id, tool_key))]


def load_receipt(row):
    try:
        receipt = json.loads(row["receipt"])
        digest = receipt.pop("fingerprint")
        if (fingerprint(receipt) == digest and receipt["attempt"]["attempt"] == row["attempt"]
                and receipt["attempt"]["error_code"] is None
                and 100 <= receipt["attempt"]["status_code"] <= 599):
            return receipt
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    raise StepRecoveryBlocked("STEP_RECEIPT_INVALID")
