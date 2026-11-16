"""Durable boundaries for the product's existing Deep Agents graph."""

import re
import sqlite3
import time
from contextlib import asynccontextmanager

from agenttest.credentials import CredentialVault
from agenttest.human_review import (
    READ_METHODS,
    UNKNOWN,
    HumanReviews,
    configuration,
    pending_interrupt,
)
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
        self.hitl = configuration(service.environ)
        if self.hitl is not None:
            self.identity = fingerprint((self.identity, "agenttest.hitl.v1", self.hitl))
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

    def publish(self, phase, *, summary, checkpoint_id=None, state=None, retry_after=None,
                review=None):
        payload = dict(version=VERSION, identity=self.identity, phase=phase,
                       at=time.time(), checkpoint_id=checkpoint_id, state=state,
                       state_sha256=fingerprint(state),
                       evidence=fingerprint((summary.get("cases"), summary.get("groups"))),
                       retry_not_before=time.time() + (retry_after or 0))
        project = self.service.project(self.row["project_id"])
        vault = CredentialVault(project.target, self.service.environ)
        try:
            if (vault.contains_secret([payload, review])
                    or self.service.private_input_guard(str((payload, review)))):
                raise TaskConflict("DURABLE_SECRET_REJECTED")
        finally:
            vault.close()
        self.service.db.save_durable_state(self.task_id, self.owner, payload, summary=summary,
                                           review=review)

    async def load(self, *, for_review=False):
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
                if phase == "TOOL" and (review_id := state.get("review_id")):
                    review = HumanReviews(self.service.db).list(
                        self.task_id, self.owner, review_id)
                    if (review["authority"] != self.identity
                            or review["checkpoint_id"] != payload["checkpoint_id"]
                            or review["plan_sha256"] != fingerprint(state["design"]["plans"])):
                        raise TaskConflict("REVIEW_BINDING_CHANGED")
                    interrupt_id = pending_interrupt(checkpoint, review_id)
                    if for_review and not interrupt_id:
                        raise TaskConflict("REVIEW_INTERRUPT_MISSING")
                    if not for_review and review["status"] != "APPROVED":
                        if interrupt_id or attempts or review["status"] != "WAITING_APPROVAL":
                            raise TaskConflict("HUMAN_APPROVAL_REQUIRED")
                        # The application transaction won the race with LangGraph. Re-enter
                        # the same tool without a decision to materialize its native interrupt.
                        payload["rebuild_interrupt"] = True
                    payload["review"] = review
                    payload["interrupt_id"] = interrupt_id
            except (KeyError, TypeError, ValueError, sqlite3.Error, OSError) as exc:
                if isinstance(exc, TaskConflict):
                    raise
                raise TaskConflict("DURABLE_CHECKPOINT_INVALID") from None
        return payload

    def verification_plan(self, payload, attempts, row):
        from agenttest.dsl import Scenario
        from agenttest.selectors import select

        state = payload["state"]
        identity = json_identity(row)
        if (payload["identity"] != self.identity or identity["method"] not in {"POST", "DELETE"}
                or identity["authority"] != self.identity
                or self.service.project(self.row["project_id"]).identity()
                != self.row["project_identity"]):
            return None
        used = state["usage"]["requests"] + sum(a["phase"] != PREPARED for a in attempts)
        limit = state["design"].get("group_request_limit", 0)
        time_limit = self.row["summary"].get("budget", {}).get("max_seconds", float("inf"))
        active = max(state.get("active_seconds", 0),
                     *(attempt["active_seconds"] for attempt in attempts))
        if used >= limit or active >= time_limit or not state["usage"]["usage_known"]:
            return None
        for candidate in state["design"].get("recovery_probes", []):
            if (candidate["plan_sha256"], candidate["step_id"]) != (
                    identity["plan_sha256"], row["step_id"]):
                continue
            if identity["method"] == "DELETE":
                from agenttest.recovery_observation import deletion_observation

                if candidate.get("method") != "DELETE":
                    continue
                recovered = deletion_observation(
                    self.service.project(self.row["project_id"]), candidate,
                    attempts, row, identity, json_identity)
                if recovered:
                    plan, observation = recovered
                    return plan, used, limit, time_limit - active, observation, None
                continue
            if candidate.get("method") == "DELETE":
                continue
            prior = next((a for a in attempts if a["phase"] in KNOWN
                         and a["run_id"] == row["run_id"]
                         and a["step_id"] == candidate["prior_step_id"]), None)
            if prior is None:
                continue
            prior_identity = json_identity(prior)
            if (prior_identity["method"], prior_identity["authority"],
                    prior_identity["plan_sha256"]) != (
                    "GET", identity["authority"], identity["plan_sha256"]):
                continue
            receipt = load_receipt(prior)
            try:
                count = select(receipt["body"], candidate["count_selector"])
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            if (receipt["replayable"] and receipt["attempt"]["status_code"] == 200
                    and type(count) is int and count == 0):
                return (Scenario.model_validate(dict(name="Approved correlation observation",
                                                     steps=[candidate["probe"]])),
                        used, limit, time_limit - active, None,
                        candidate["count_selector"] if candidate.get("positive") else None)
        return None

    async def verify_review_binding(self, review):
        payload = self.service.db.durable_state(self.task_id, self.owner)
        if (not isinstance(payload, dict) or payload.get("phase") != "TOOL"
                or payload.get("identity") != self.identity
                or payload["checkpoint_id"] != review["checkpoint_id"]
                or fingerprint(payload["state"]) != payload["state_sha256"]):
            raise TaskConflict("REVIEW_BINDING_CHANGED")
        try:
            async with self.saver(create=False) as saver:
                checkpoint = await saver.aget_tuple(self.config)
        except (KeyError, TypeError, ValueError, sqlite3.Error, OSError) as exc:
            if isinstance(exc, TaskConflict):
                raise
            raise TaskConflict("DURABLE_CHECKPOINT_INVALID") from None
        if checkpoint is None or checkpoint.checkpoint["id"] != payload["checkpoint_id"]:
            raise TaskConflict("DURABLE_CHECKPOINT_MISMATCH")
        attempts = tool_attempts(self.service.db, self.task_id, payload["state"]["tool_key"])
        possible = [self.verification_plan(payload, attempts, row) for row in attempts
                    if row["phase"] == DISPATCHED and fingerprint((
                        self.task_id, "unknown", json_identity(row)["attempt_id"]))
                    == review["review_id"]]
        if not possible or possible[0] is None:
            raise TaskConflict("REVIEW_VERIFICATION_UNAVAILABLE")
        plan = possible[0][0]
        if fingerprint(plan.model_dump()) != review["verification_plan_sha256"]:
            raise TaskConflict("REVIEW_BINDING_CHANGED")
        return possible[0]

    def unknown_reviews(self):
        """Project dispatched writes without a receipt. The ledger stays authoritative."""
        if self.hitl is None:
            return
        payload = self.service.db.durable_state(self.task_id, self.owner)
        if not isinstance(payload, dict) or payload.get("phase") not in {"TOOL", "TOOL_DONE"}:
            return
        state = payload.get("state")
        if not isinstance(state, dict) or fingerprint(state) != payload["state_sha256"]:
            return
        reviews = HumanReviews(self.service.db)
        attempts = tool_attempts(self.service.db, self.task_id, state["tool_key"])
        for row in attempts:
            if row["phase"] != DISPATCHED:
                continue
            identity = json_identity(row)
            if identity["method"] in READ_METHODS:
                continue
            verification = self.verification_plan(payload, attempts, row) if (
                payload["phase"] == "TOOL") else None
            options = ["archive"]
            probe_sha = None
            if verification:
                options.append("request_verification")
                probe_sha = fingerprint(verification[0].model_dump())
            review_id = fingerprint((self.task_id, "unknown", identity["attempt_id"]))
            reviews.record_unknown(self.task_id, self.owner, dict(review_id=review_id,
                task_id=self.task_id, run_id=row["run_id"], thread_id=self.task_id,
                checkpoint_id=payload["checkpoint_id"], step_id=row["step_id"],
                kind="UNKNOWN_WRITE", reason="UNKNOWN_OUTCOME",
                risk="结果未知。原写入、后续写入及清理均无法凭审批恢复。",
                authority=payload["identity"], plan_sha256=identity["plan_sha256"],
                operations=[identity], evidence=[dict(run_id=row["run_id"],
                    step_id=row["step_id"], attempt=row["attempt"], phase=DISPATCHED)],
                status=UNKNOWN, version=1, created_at=time.time(),
                verification_plan_sha256=probe_sha, allowed_decisions=options))

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
        for review in HumanReviews(self.service.db).list(self.task_id, self.owner):
            if review["kind"] == "UNKNOWN_WRITE":
                attempts.extend(tool_attempts(self.service.db, self.task_id, review["review_id"]))
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
