"""SQLite metadata journal: no credentials, user bodies, model output or replay state."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from agenttest.report_files import pinned_directory, regular

TERMINAL = {"FINISHED", "NEEDS_REVIEW", "CANCELLED", "INTERRUPTED"}
STATES = TERMINAL | {"QUEUED", "RUNNING", "WAITING_REVIEW"}


class TaskConflict(ValueError):
    pass


class TaskStore:
    def __init__(self, path: Path):
        self.path = path.absolute()
        if ".." in self.path.parts:
            raise ValueError("TASK_DB_PATH_INVALID")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, submission TEXT NOT NULL,
                    project TEXT NOT NULL, identity TEXT NOT NULL, request_sha TEXT NOT NULL,
                    state TEXT NOT NULL, version INTEGER NOT NULL, created REAL NOT NULL,
                    summary TEXT NOT NULL, UNIQUE(owner, submission));
                CREATE TABLE IF NOT EXISTS task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    phase TEXT NOT NULL, run_id TEXT, requests INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS task_events_by_task
                    ON task_events(task_id, sequence);
                CREATE TABLE IF NOT EXISTS saved_tests (
                    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, owner TEXT NOT NULL,
                    project_id TEXT NOT NULL, identity TEXT NOT NULL, contract TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_context (
                    task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_usage_journal (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, state TEXT NOT NULL,
                    stats TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_usage_reconciliations (
                    call_id TEXT PRIMARY KEY, at REAL NOT NULL, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_usage_exceptions (
                    call_id TEXT PRIMARY KEY, at REAL NOT NULL, authorization TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS product_assets (
                    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, identity TEXT NOT NULL,
                    payload TEXT NOT NULL);
            """)

    def interrupt_unfinished(self):
        """Only the service that owns the exclusive process lease calls startup recovery."""
        with self.connect() as db:
            db.execute("UPDATE tasks SET state='INTERRUPTED' WHERE state IN "
                       "('QUEUED','RUNNING','WAITING_REVIEW')")

    @contextmanager
    def connect(self):
        with pinned_directory(self.path.parent, ".", create=False) as directory:
            directory.inspect(self.path.name)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def create(self, task_id, owner, submission, project, identity, request_sha, state, summary):
        if state not in STATES:
            raise ValueError("TASK_STATE_INVALID")
        with self.connect() as db:
            old = db.execute("SELECT * FROM tasks WHERE owner=? AND submission=?",
                             (owner, submission)).fetchone()
            if old:
                if (old["project"], old["identity"], old["request_sha"]) != (
                        project, identity, request_sha):
                    raise TaskConflict("SUBMISSION_CONFLICT")
                return self.public(old), False
            if db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] >= 1000:
                raise TaskConflict("TASK_CATALOG_LIMIT")
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", (
                task_id, owner, submission, project, identity, request_sha, state, 1, time.time(),
                json.dumps(summary, ensure_ascii=False)))
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self.public(row), True

    @staticmethod
    def public(row):
        return dict(id=row["id"], project_id=row["project"], project_identity=row["identity"],
            request_sha256=row["request_sha"], status=row["state"], plan_version=row["version"],
            created_at=row["created"], summary=json.loads(row["summary"]))

    def get(self, task_id, owner):
        with self.connect() as db:
            row = self._owned_row(db, task_id, owner)
        return self.public(row)

    @staticmethod
    def _owned_row(db, task_id, owner):
        row = db.execute("SELECT * FROM tasks WHERE id=? AND owner=?",
                         (task_id, owner)).fetchone()
        if row is None:
            raise KeyError("TASK_NOT_FOUND")
        return row

    def list(self, owner):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks WHERE owner=? ORDER BY created DESC LIMIT 100",
                              (owner,)).fetchall()
        return [self.public(row) for row in rows]

    def transition(self, task_id, owner, previous, state, *, summary=None, version=1):
        if state not in STATES:
            raise ValueError("TASK_STATE_INVALID")
        with self.connect() as db:
            values, assignments = [state], "state=?"
            if summary is not None:
                assignments += ",summary=?"
                values.append(json.dumps(summary, ensure_ascii=False))
            values.extend([task_id, owner, previous, version])
            result = db.execute("UPDATE tasks SET " + assignments +
                " WHERE id=? AND owner=? AND state=? AND version=?", values)
            if result.rowcount != 1:
                raise TaskConflict("TASK_STATE_OR_VERSION_CONFLICT")
            row = self._owned_row(db, task_id, owner)
        return self.public(row)

    def event(self, task_id, phase, *, run_id=None, requests=0):
        if phase not in STATES | {"PLANNING", "VALIDATING", "EXECUTING", "REPORT"}:
            raise ValueError("TASK_EVENT_INVALID")
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM task_events WHERE task_id=?",
                               (task_id,)).fetchone()[0]
            if count < 2000:
                db.execute("INSERT INTO task_events(task_id,phase,run_id,requests) VALUES(?,?,?,?)",
                           (task_id, phase, run_id, requests))

    def events(self, task_id, owner, after=0):
        with self.connect() as db:
            self._owned_row(db, task_id, owner)
            rows = db.execute("SELECT sequence,phase,run_id,requests FROM task_events "
                "WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                (task_id, after)).fetchall()
        return [dict(row) for row in rows]

    def save_test(self, run_id, task_id, owner, project_id, identity, contract):
        with self.connect() as db:
            self._owned_row(db, task_id, owner)
            db.execute("INSERT OR IGNORE INTO saved_tests VALUES (?,?,?,?,?,?)",
                       (run_id, task_id, owner, project_id, identity, contract))

    def saved_tests(self, owner):
        with self.connect() as db:
            rows = db.execute("SELECT run_id,task_id,project_id,identity FROM saved_tests "
                              "WHERE owner=? ORDER BY rowid DESC LIMIT 100", (owner,)).fetchall()
        return [dict(r) for r in rows]

    def saved_test(self, task_id, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM saved_tests WHERE task_id=? AND run_id=?",
                             (task_id, run_id)).fetchone()
        if row is None:
            raise KeyError("SAVED_TEST_NOT_FOUND")
        return dict(row)

    def save_context(self, task_id, owner, payload):
        """Only caller-validated, secret-free product context; never HTTP bodies or credentials."""
        with self.connect() as db:
            self._owned_row(db, task_id, owner)
            db.execute("INSERT INTO task_context VALUES (?,?,?) ON CONFLICT(task_id) "
                       "DO UPDATE SET payload=excluded.payload", (
                           task_id, owner, json.dumps(payload, ensure_ascii=False)))

    def context(self, task_id, owner):
        with self.connect() as db:
            self._owned_row(db, task_id, owner)
            row = db.execute("SELECT payload FROM task_context WHERE task_id=? AND owner=?",
                             (task_id, owner)).fetchone()
        if row is None:
            raise TaskConflict("TASK_CONTEXT_UNAVAILABLE")
        return json.loads(row[0])

    def model_usage_blocked(self):
        with self.connect() as db:
            return self._usage_blocked(db)

    @staticmethod
    def _usage_blocked(db):
        return db.execute("SELECT 1 FROM model_usage_journal j WHERE state IN "
            "('PENDING','UNKNOWN') AND NOT EXISTS (SELECT 1 FROM model_usage_reconciliations r "
            "WHERE r.call_id=j.id) AND NOT EXISTS (SELECT 1 FROM model_usage_exceptions e "
            "WHERE e.call_id=j.id) LIMIT 1").fetchone() is not None

    def begin_model_call(self, call_id, task_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._usage_blocked(db):
                raise TaskConflict("MODEL_USAGE_REQUIRES_RECONCILIATION")
            db.execute("INSERT INTO model_usage_journal VALUES (?,?,?,?)",
                       (call_id, task_id, "PENDING", "{}"))

    def finish_model_call(self, call_id, stats):
        state = ("NOT_SENT" if stats.calls == 0 else
                 "KNOWN" if stats.total_tokens is not None else "UNKNOWN")
        with self.connect() as db:
            db.execute("UPDATE model_usage_journal SET state=?,stats=? WHERE id=?",
                       (state, stats.model_dump_json(), call_id))

    def reconcile_model_usage(self, evidence):
        """Operator-only receipt reconciliation; original unknown evidence stays immutable."""
        from agenttest.usage_reconciliation import ProviderUsageReceipt

        receipt = ProviderUsageReceipt.model_validate(evidence)
        public = receipt.model_dump()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            call = db.execute("SELECT * FROM model_usage_journal WHERE id=?",
                              (receipt.call_id,)).fetchone()
            if (call is None or call["state"] != "UNKNOWN"
                    or call["task_id"] != receipt.task_id
                    or json.loads(call["stats"]).get("request_sha256") != receipt.request_sha256):
                raise TaskConflict("USAGE_RECEIPT_CALL_MISMATCH")
            task = db.execute("SELECT state FROM tasks WHERE id=?", (receipt.task_id,)).fetchone()
            if task is None or task["state"] not in TERMINAL:
                raise TaskConflict("USAGE_RECEIPT_TASK_STILL_ACTIVE")
            old = db.execute("SELECT evidence FROM model_usage_reconciliations WHERE call_id=?",
                             (receipt.call_id,)).fetchone()
            if old:
                if json.loads(old[0]) != public:
                    raise TaskConflict("USAGE_RECEIPT_ALREADY_RECORDED")
                return public
            db.execute("INSERT INTO model_usage_reconciliations VALUES (?,?,?)",
                       (receipt.call_id, time.time(), json.dumps(public, ensure_ascii=False)))
        return public

    def record_usage_exception(self, authorization):
        """A new explicit human exception is separate from evidence of known usage."""
        from agenttest.usage_reconciliation import UnknownUsageException

        approved = UnknownUsageException.model_validate(authorization)
        public = approved.model_dump()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            call = db.execute("SELECT * FROM model_usage_journal WHERE id=?",
                              (approved.call_id,)).fetchone()
            if (call is None or call["state"] != "UNKNOWN"
                    or call["task_id"] != approved.task_id
                    or json.loads(call["stats"]).get("request_sha256") != approved.request_sha256):
                raise TaskConflict("USAGE_EXCEPTION_CALL_MISMATCH")
            task = db.execute("SELECT state FROM tasks WHERE id=?", (approved.task_id,)).fetchone()
            if task is None or task["state"] not in TERMINAL:
                raise TaskConflict("USAGE_EXCEPTION_TASK_STILL_ACTIVE")
            old = db.execute("SELECT authorization FROM model_usage_exceptions WHERE call_id=?",
                             (approved.call_id,)).fetchone()
            if old:
                if json.loads(old[0]) != public:
                    raise TaskConflict("USAGE_EXCEPTION_ALREADY_RECORDED")
                return public
            db.execute("INSERT INTO model_usage_exceptions VALUES (?,?,?)",
                       (approved.call_id, time.time(), json.dumps(public, ensure_ascii=False)))
        return public

    def save_product_asset(self, run_id, task_id, identity, payload):
        with self.connect() as db:
            db.execute("INSERT INTO product_assets VALUES (?,?,?,?)", (
                run_id, task_id, identity, json.dumps(payload, ensure_ascii=False)))

    def product_asset(self, task_id, run_id):
        with self.connect() as db:
            row = db.execute("SELECT identity,payload FROM product_assets "
                             "WHERE run_id=? AND task_id=?", (run_id, task_id)).fetchone()
        if row is None:
            raise KeyError("PRODUCT_ASSET_NOT_FOUND")
        return row["identity"], json.loads(row["payload"])


class ServiceLease:
    """One service process per metadata store; OS releases the lock after a crash."""

    def __init__(self, path):
        self.file = None
        with pinned_directory(path.parent, ".", create=False) as directory:
            directory.inspect(path.name)
            source = path.open("a+b")
            try:
                regular(os.fstat(source.fileno()))
                if source.seek(0, 2) == 0:
                    source.write(b"0")
                    source.flush()
                source.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(source.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(source, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (ValueError, OSError):
                source.close()
                raise ValueError("TASK_SERVICE_ALREADY_RUNNING_OR_LOCK_INVALID") from None
            self.file = source

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
