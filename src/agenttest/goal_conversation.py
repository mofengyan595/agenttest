"""Bounded task context and immutable revisions, not long-term memory or HTTP recovery."""

from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

import httpx

from agenttest.credentials import CredentialVault
from agenttest.goal_derivation import derive_goal
from agenttest.goal_runtime import run_goal
from agenttest.goal_suite import BudgetPlanner, BudgetTransport, SuiteLedger
from agenttest.planner_input import validate_user_task
from agenttest.report import fingerprint


@dataclass(frozen=True)
class GoalRevision:
    id: str
    version: int
    project_identity: str
    request_sha256: str
    status: str


class GoalConversation:
    def __init__(self, project):
        self.id = uuid4().hex
        self.project_id = project.registration.id
        self._turns = []
        self._effective = []
        self._clarifications = []
        self._drafts = {}
        self._states = {}
        self._evidence = []
        self.ledger = SuiteLedger(project.target.max_requests,
            project.registration.budget.suite_max_model_calls,
            project.registration.budget.suite_max_total_tokens)

    def plan(self, project, text, *, environ, expected_version=None, replace_pending=False):
        if (project.registration.id != self.project_id or len(self._turns) >= 8
                or expected_version is not None and expected_version != len(self._turns)):
            raise ValueError("CONVERSATION_VERSION_OR_PROJECT_MISMATCH")
        validate_user_task(text)
        vault = CredentialVault(project.target, environ)
        try:
            if vault.contains_secret(text):
                raise ValueError("CONVERSATION_SECRET_REJECTED")
        finally:
            vault.close()
        # Raw user instructions are bounded, never HTTP response bodies or model prose.
        if replace_pending and (not self._turns or
                self._states[next(reversed(self._drafts))] != "NEEDS_REVIEW"):
            raise ValueError("ONLY_UNVERIFIED_PENDING_REQUEST_CAN_BE_CLARIFIED")
        effective = [text] if replace_pending else [*self._effective, text]
        original = "，".join(effective)
        validate_user_task(original)
        derived = derive_goal(project, original)
        revision = GoalRevision(uuid4().hex, len(self._turns) + 1,
            project.identity(), fingerprint(original), "DRAFT" if derived.task else "NEEDS_REVIEW")
        self._turns.append(text)
        self._effective = effective
        if replace_pending:
            self._clarifications.append(fingerprint(text))
        self._drafts[revision.id] = (revision, deepcopy(derived), original)
        self._states[revision.id] = revision.status
        return revision, derived.summary()

    def snapshot(self):
        return dict(conversation_id=self.id, project_id=self.project_id,
            version=len(self._turns), request_hashes=[fingerprint(t) for t in self._turns],
            clarification_hashes=list(self._clarifications),
            plans=[dict(plan_id=r.id, version=r.version, project_identity=r.project_identity,
                        request_sha256=r.request_sha256, status=self._states[r.id])
                   for r, _, _ in self._drafts.values()],
            evidence_refs=list(self._evidence), usage=self.ledger.summary(),
            remaining=dict(requests=max(0, self.ledger.max_requests - self.ledger.requests),
                model_calls=max(0, self.ledger.max_model_calls - self.ledger.model_calls),
                tokens=None if self.ledger.usage_unknown else max(
                    0, self.ledger.max_total_tokens - self.ledger.known_tokens)))

    def revalidate(self, project, revision_id):
        revision, _, text = self._drafts[revision_id]
        if (project.registration.id != self.project_id
                or self._states[revision_id] not in {"DRAFT", "NEEDS_REVIEW"}
                or revision.version != len(self._turns)):
            raise ValueError("PLAN_CANNOT_BE_REVALIDATED")
        goal = derive_goal(project, text)
        revised = GoalRevision(uuid4().hex, revision.version, project.identity(),
            fingerprint(text), "DRAFT" if goal.task else "NEEDS_REVIEW")
        self._states[revision_id] = "STALE"
        self._drafts[revised.id] = (revised, goal, text)
        self._states[revised.id] = revised.status
        self.ledger.max_requests = min(self.ledger.max_requests, project.target.max_requests)
        self.ledger.max_model_calls = min(self.ledger.max_model_calls,
                                         project.registration.budget.suite_max_model_calls)
        self.ledger.max_total_tokens = min(self.ledger.max_total_tokens,
                                          project.registration.budget.suite_max_total_tokens)
        return revised, goal.summary()

    async def execute(self, project, revision_id, planner, store, *, environ, transport=None,
                      budget=None):
        revision, goal, text = self._drafts[revision_id]
        if (revision.project_identity != project.identity()
                or revision.version != len(self._turns)
                or self._states[revision_id] != "DRAFT"):
            raise ValueError("PLAN_STALE_OR_ALREADY_STARTED")
        required = len(goal.task.requirements.steps)
        if (self.ledger.requests + required > self.ledger.max_requests
                or self.ledger.usage_unknown):
            raise ValueError("CONVERSATION_BUDGET_OR_USAGE_STOP")
        self._states[revision_id] = "STARTED"
        inner = transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        try:
            _, report = await run_goal(project, text, BudgetPlanner(planner, self.ledger), store,
                environ=environ, transport=BudgetTransport(inner, self.ledger), budget=budget,
                derived=deepcopy(goal))
            self._evidence.append(report.run_id)
            self._states[revision_id] = "FINISHED"
            return report
        except BaseException:
            self._states[revision_id] = "INTERRUPTED"
            raise
        finally:
            if transport is None:
                await inner.aclose()
