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
            row = db.execute("SELECT * FROM tasks WHERE id=? AND owner=?",
                             (task_id, owner)).fetchone()
        if row is None:
            raise KeyError("TASK_NOT_FOUND")
        return self.public(row)

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
        return self.get(task_id, owner)

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
        self.get(task_id, owner)
        with self.connect() as db:
            rows = db.execute("SELECT sequence,phase,run_id,requests FROM task_events "
                "WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                (task_id, after)).fetchall()
        return [dict(row) for row in rows]


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
