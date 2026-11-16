"""Durable boundaries for the product's existing Deep Agents graph."""

import re
import sqlite3
import time
from contextlib import asynccontextmanager

from agenttest.credentials import CredentialVault
from agenttest.report import fingerprint
from agenttest.report_files import pinned_directory
from agenttest.step_recovery import (
    DISPATCHED,
    KNOWN,
    PREPARED,
    StepRecoveryBlocked,
    load_receipt,
    tool_attempts,
)
from agenttest.task_store import TaskConflict

VERSION = "agenttest.durable.v1"
PAUSED = "DURABLE_CHECKPOINT_PAUSED"
GATEWAY_STOP = "DURABLE_MODEL_GATEWAY_UNAVAILABLE"


class ProductDurability:
    def __init__(self, service, row, owner):
        self.service = service
        self.row = row
        self.task_id = row["id"]
        self.owner = owner
        epoch = service.environ.get("AGENTTEST_DURABLE_TARGET_EPOCH", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", epoch):
            raise TaskConflict("DURABLE_TARGET_EPOCH_REQUIRED")
        self.identity = fingerprint((row["project_identity"], epoch))
        self.config = {"configurable": {"thread_id": self.task_id}}
        self.path = service.db.path.with_suffix(".checkpoints.sqlite3")

    @asynccontextmanager
    async def saver(self, *, secret_check=None, create=True):
        import aiosqlite
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        class CheckedSerializer(JsonPlusSerializer):
            def dumps_typed(self, value):
                if secret_check is not None and secret_check(value):
                    raise TaskConflict("DURABLE_SECRET_REJECTED")
                return super().dumps_typed(value)

        serde = CheckedSerializer(allowed_msgpack_modules=None)
        with pinned_directory(self.path.parent, ".", create=False) as directory:
            for suffix in ("", "-wal", "-shm", "-journal"):
                directory.inspect(self.path.name + suffix)
            if not create and not self.path.is_file():
                raise TaskConflict("DURABLE_CHECKPOINT_MISSING")
            async with aiosqlite.connect(self.path) as connection:
                yield AsyncSqliteSaver(connection, serde=serde)

    def publish(self, phase, *, summary, checkpoint_id=None, state=None, retry_after=None):
        payload = dict(version=VERSION, identity=self.identity, phase=phase,
                       at=time.time(), checkpoint_id=checkpoint_id, state=state,
                       state_sha256=fingerprint(state),
                       evidence=fingerprint((summary.get("cases"), summary.get("groups"))),
                       retry_not_before=time.time() + (retry_after or 0))
        project = self.service.project(self.row["project_id"])
        vault = CredentialVault(project.target, self.service.environ)
        try:
            if vault.contains_secret(payload) or self.service.private_input_guard(str(payload)):
                raise TaskConflict("DURABLE_SECRET_REJECTED")
        finally:
            vault.close()
        self.service.db.save_durable_state(self.task_id, self.owner, payload, summary=summary)

    async def load(self):
        try:
            payload = self.service.db.durable_state(self.task_id, self.owner)
        except (sqlite3.Error, ValueError):
            raise TaskConflict("DURABLE_CHECKPOINT_INVALID") from None
        if payload is None:
            raise TaskConflict("DURABLE_CHECKPOINT_MISSING")
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            raise TaskConflict("DURABLE_CHECKPOINT_VERSION_MISMATCH")
        if payload.get("identity") != self.identity or (
            self.service.project(self.row["project_id"]).identity() != self.row["project_identity"]
        ):
            raise TaskConflict("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")
        if payload.get("phase") == "ACTIVE":
            if self.row["summary"].get("failure_detail") == (
                "DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW"
            ):
                raise TaskConflict("DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW")
            raise TaskConflict("DURABLE_EXECUTION_UNCONFIRMED")
        phase = payload.get("phase")
        if phase not in {"FOCUS", "AGENT", "TOOL", "TOOL_DONE"}:
            raise TaskConflict("DURABLE_CHECKPOINT_INVALID")
        summary = self.row["summary"]
        if (phase != "TOOL"
                and fingerprint((summary.get("cases"), summary.get("groups")))
                != payload.get("evidence")):
            raise TaskConflict("DURABLE_CHECKPOINT_MISMATCH")
        state = payload.get("state")
        if (not isinstance(state, dict) or not isinstance(state.get("usage"), dict)
                or fingerprint(state) != payload.get("state_sha256")):
            raise TaskConflict("DURABLE_CHECKPOINT_INVALID")
        if time.time() < payload.get("retry_not_before", 0):
            raise TaskConflict("DURABLE_GATEWAY_RETRY_AFTER")
        if phase == "TOOL":
            attempts = tool_attempts(self.service.db, self.task_id, state["tool_key"])
            for row in attempts:
                if row["phase"] == DISPATCHED:
                    identity = json_identity(row)
                    code = ("DURABLE_READ_OUTCOME_REQUIRES_REVIEW" if identity["method"] in
                            {"GET", "HEAD", "OPTIONS"} else "DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW")
                    raise StepRecoveryBlocked(code)
                if row["phase"] in KNOWN and not load_receipt(row).get("replayable"):
                    raise StepRecoveryBlocked("STEP_RESPONSE_REQUIRES_REVIEW")
                if row["phase"] not in {PREPARED, *KNOWN}:
                    raise StepRecoveryBlocked("STEP_LEDGER_INVALID")
            # Keep accounting for measured activity without including downtime between processes.
            state["active_seconds"] = max([state["active_seconds"],
                                          *(row["active_seconds"] for row in attempts)])
        if phase in {"AGENT", "TOOL", "TOOL_DONE"}:
            from agenttest.task_completion import write_effects_view

            project = self.service.project(self.row["project_id"])
            policies = {policy.id: policy for policy in project.registry.definitions()}
            if phase != "TOOL" and write_effects_view(summary, policies).get(
                    "counts", {}).get("WRITE_OUTCOME_UNKNOWN"):
                raise TaskConflict("DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW")
            try:
                async with self.saver(create=False) as saver:
                    checkpoint = await saver.aget_tuple(self.config)
                if checkpoint is None or checkpoint.checkpoint["id"] != payload["checkpoint_id"]:
                    raise TaskConflict("DURABLE_CHECKPOINT_MISMATCH")
            except (KeyError, TypeError, ValueError, sqlite3.Error, OSError) as exc:
                if isinstance(exc, TaskConflict):
                    raise
                raise TaskConflict("DURABLE_CHECKPOINT_INVALID") from None
        return payload

    async def prune(self):
        """Drop terminal graph state while retaining task evidence and the usage journal."""
        if self.path.is_file():
            async with self.saver(create=False) as saver:
                await saver.adelete_thread(self.task_id)

    def step_budget(self):
        payload = self.service.db.durable_state(self.task_id, self.owner)
        if not isinstance(payload, dict) or payload.get("identity") != self.identity:
            return {}
        state = payload.get("state")
        if (payload.get("phase") != "TOOL" or not isinstance(state, dict)
                or fingerprint(state) != payload.get("state_sha256")):
            return {}
        attempts = tool_attempts(self.service.db, self.task_id, state["tool_key"])
        return dict(http_requests_conservatively_used=state["usage"]["requests"] + sum(
            row["phase"] != PREPARED for row in attempts),
            uncertain_dispatches=sum(row["phase"] == DISPATCHED for row in attempts))


def json_identity(row):
    import json

    try:
        result = json.loads(row["identity"])
        if isinstance(result, dict):
            return result
    except (TypeError, ValueError):
        pass
    raise StepRecoveryBlocked("STEP_LEDGER_INVALID")
