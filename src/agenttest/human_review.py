"""Owner decisions bound to durable graph and HTTP evidence."""

import json
import time
from typing import Literal

from pydantic import Field

from agenttest.report import fingerprint
from agenttest.safety import StrictModel
from agenttest.task_store import TaskConflict

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WAITING = "WAITING_APPROVAL"
UNKNOWN = "REVIEW_REQUIRED"
PAUSED_FOR_REVIEW = "HUMAN_APPROVAL_REQUIRED"


class ReviewDecision(StrictModel):
    decision: Literal["approve", "reject", "archive", "request_verification"]
    version: int = Field(ge=1)
    note: str = Field(default="", max_length=500)


def configuration(environ):
    if environ.get("AGENTTEST_HITL") != "1":
        return None
    return tuple(sorted(set(environ.get("AGENTTEST_HITL_OPERATIONS", "").split())))


def group_operations(project, scenarios, selected):
    """Expose contract paths and fingerprints without publishing rendered request values."""
    if selected is None:
        return None
    operations = {op.operation_id: op for op in project.contract.operations}
    steps = []
    for scenario in scenarios:
        if isinstance(scenario, ValueError):
            continue
        for step in scenario.steps:
            op = operations[step.operation_id]
            steps.append(dict(step_id=step.id, operation_id=step.operation_id,
                method=op.method, path=op.path, policy_id=step.policy_id, role=step.role,
                resource_sha256=fingerprint(step.path_parameters),
                request_sha256=fingerprint(step.model_dump(mode="json"))))
    if any((step["operation_id"] in selected if selected else
            step["method"] not in READ_METHODS) for step in steps):
        return steps
    return None


def pending_interrupt(checkpoint, review_id):
    matches = [item.id for _, channel, batch in checkpoint.pending_writes
               if channel == "__interrupt__" for item in batch
               if item.value == {"review_id": review_id}]
    if len(matches) > 1:
        raise TaskConflict("REVIEW_INTERRUPT_MISMATCH")
    return matches[0] if matches else None


def verification_candidates(project, scenarios):
    """Retain the exact approved post-observation, when it requires only one public GET."""
    from agenttest.negative_post_observation import observations

    if project.target.roles:
        return []
    return [dict(plan_sha256=fingerprint(scenario.model_dump()), step_id=write.id,
                 prior_step_id=before.id, probe=after.model_dump(mode="json"),
                 count_selector=rule.count_selector)
            for scenario in scenarios if not isinstance(scenario, ValueError)
            for before, write, after, rule in observations(project.target, scenario)
            if after.role is None and not after.poll and not after.read_retries
            and after.operation_id not in project.target.credential_inputs]


class HumanReviews:
    def __init__(self, store):
        self.store = store

    def list(self, task_id, owner, review_id=None):
        with self.store.connect() as db:
            self.store._owned_row(db, task_id, owner)
            rows = db.execute("SELECT payload FROM human_reviews WHERE task_id=? "
                              "ORDER BY rowid", (task_id,))
            reviews = [json.loads(row["payload"]) for row in rows]
        if review_id is None:
            return reviews
        for review in reviews:
            if review["review_id"] == review_id:
                return review
        raise KeyError("REVIEW_NOT_FOUND")

    @staticmethod
    def insert(db, review):
        db.execute("INSERT OR IGNORE INTO human_reviews VALUES (?,?,?)",
                   (review["task_id"], review["review_id"], json.dumps(review)))

    def record_unknown(self, task_id, owner, review):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self.store._owned_row(db, task_id, owner)
            if task["state"] != "INTERRUPTED":
                return
            self.insert(db, review)

    def decide(self, task_id, owner, review, decision):
        with self.store.connect() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            task = self.store._owned_row(db, task_id, owner)
            row = db.execute("SELECT payload FROM human_reviews WHERE task_id=? AND review_id=?",
                             (task_id, review["review_id"])).fetchone()
            if row is None:
                raise KeyError("REVIEW_NOT_FOUND")
            current = json.loads(row["payload"])
            outcome = {"approve": "APPROVED", "reject": "REJECTED", "archive": "ARCHIVED",
                       "request_verification": "VERIFICATION_REQUESTED"}[decision.decision]
            entry = dict(status=outcome, decision_by=owner, decision_note=decision.note,
                         version=decision.version + 1)
            history = current.get("decisions", [])
            if any(all(item[key] == value for key, value in entry.items()) for item in history):
                return current, False
            if (task["state"] != "INTERRUPTED" or current != review
                    or decision.version != current["version"]
                    or current["status"] not in {WAITING, UNKNOWN}):
                raise TaskConflict("REVIEW_STATE_OR_VERSION_CONFLICT")
            if decision.decision not in current["allowed_decisions"]:
                raise TaskConflict("REVIEW_DECISION_NOT_ALLOWED")
            entry["decided_at"] = time.time()
            history.append(entry)
            current.update(entry, allowed_decisions=[], decisions=history)
            db.execute("UPDATE human_reviews SET payload=? WHERE task_id=? AND review_id=?",
                       (json.dumps(current), task_id, current["review_id"]))
            if outcome == "VERIFICATION_REQUESTED":
                db.execute("UPDATE tasks SET state='RUNNING' WHERE id=?", (task_id,))
            elif outcome != "APPROVED":
                summary = json.loads(task["summary"])
                summary.update(complete=False, checks_passed=False,
                    reason=("人工拒绝本批测试，任务已停止。" if outcome == "REJECTED"
                            else "人工已归档待核实写入；原有证据及清理义务保留。"))
                db.execute("UPDATE tasks SET state='NEEDS_REVIEW', summary=? WHERE id=?",
                           (json.dumps(summary, ensure_ascii=False), task_id))
                db.execute("INSERT INTO task_events(task_id,phase,requests) VALUES "
                           "(?,'NEEDS_REVIEW',?)",
                           (task_id, summary.get("context_usage", {}).get("requests", 0)))
        return current, True

    @staticmethod
    def cancel_pending(db, task_id):
        rows = db.execute("SELECT payload FROM human_reviews WHERE task_id=?", (task_id,))
        for row in rows.fetchall():
            review = json.loads(row["payload"])
            if review["status"] not in {WAITING, UNKNOWN, "VERIFICATION_REQUESTED"}:
                continue
            review.update(status="CANCELLED", allowed_decisions=[], version=review["version"] + 1)
            db.execute("UPDATE human_reviews SET payload=? WHERE task_id=? AND review_id=?",
                       (json.dumps(review), task_id, review["review_id"]))

    def finish_verification(self, task_id, owner, review_id, result):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self.store._owned_row(db, task_id, owner)
            row = db.execute("SELECT payload FROM human_reviews WHERE task_id=? AND review_id=?",
                             (task_id, review_id)).fetchone()
            review = json.loads(row["payload"])
            if task["state"] != "RUNNING" or review["status"] != "VERIFICATION_REQUESTED":
                return
            review.update(status=UNKNOWN, verification=result, allowed_decisions=["archive"],
                          version=review["version"] + 1)
            db.execute("UPDATE human_reviews SET payload=? WHERE task_id=? AND review_id=?",
                       (json.dumps(review), task_id, review_id))
            db.execute("UPDATE tasks SET state='INTERRUPTED' WHERE id=?", (task_id,))

    def recover_verification(self, task_id, owner):
        """An interrupted verification is never rescheduled from a stored decision."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self.store._owned_row(db, task_id, owner)
            if task["state"] != "INTERRUPTED":
                return
            rows = db.execute("SELECT payload FROM human_reviews WHERE task_id=?", (task_id,))
            for row in rows.fetchall():
                review = json.loads(row["payload"])
                if review["status"] != "VERIFICATION_REQUESTED":
                    continue
                review.update(status=UNKNOWN, allowed_decisions=["archive"],
                    version=review["version"] + 1,
                    verification=dict(status="OBSERVATION_INTERRUPTED"))
                db.execute("UPDATE human_reviews SET payload=? WHERE task_id=? AND review_id=?",
                           (json.dumps(review), task_id, review["review_id"]))
