"""Deterministic execution budget: estimate per task, grow on progress, stop on hard limits.

One autonomous testing round is bounded by *five independent* budgets, because they are
authorised by different things and must never be interchangeable:

==========================  ==========================================================
dimension                   what authorises it
==========================  ==========================================================
business HTTP requests      the operator's target configuration (``max_requests``)
model calls / tokens        the deployment's spend configuration
agent turns / tool calls    the shape of one agent loop
wall clock                  the task deadline of the service
cleanup reserve             safety: a created resource must still be deletable
==========================  ==========================================================

Raising one of them never raises another. In particular, a larger model budget never
widens the business-HTTP permission: that number comes from the approved project and is
only ever *lowered* here.

Two modes share this module and the same Deep Agents runtime:

``FIXED``     the historical experiment contract: exactly the configured numbers, no
              estimation, no growth, no drift between A/B cells.
``ADAPTIVE``  a real autonomous task: the budget starts at a deterministic estimate of
              what this round's approved obligations need, and grows in bounded steps
              while the round keeps producing *new* evidence (a new legal plan, a new
              trusted test result, a newly closed obligation, a completed cleanup).
              Repeating a failing plan without new evidence is not progress.

Everything here is a plain function of already measured state: no model decides a
budget, and no heuristic is allowed to report a precise cost it cannot justify. An
estimate whose input is incomplete is published as a range with its reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, model_validator

from agenttest.safety import StrictModel

FIXED = "FIXED"
ADAPTIVE = "ADAPTIVE"
MODES = (FIXED, ADAPTIVE)

READ_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Hard product safety cap. A deployment can configure anything up to it; nothing can
# raise it at runtime, so "the model kept making progress" can never become a loop that
# never ends. It is deliberately much larger than any plausible single task.
ABSOLUTE_MAX_MODEL_CALLS = 96
ABSOLUTE_MAX_REQUESTS = 400
ABSOLUTE_MAX_SECONDS = 3_600.0


class ExecutionBudget(StrictModel):
    """Configured execution policy; every ``None`` falls back to an existing setting.

    Keeping ``None`` (instead of copying today's numbers) means a project that never
    mentions the field keeps exactly the limits it had, while a deployment that wants a
    real autonomous round sets one field instead of editing source constants.
    """

    # The product default: a real autonomous task may continue while it produces new
    # evidence. A frozen A/B baseline requests ``FIXED`` explicitly.
    mode: Literal["FIXED", "ADAPTIVE"] = ADAPTIVE
    # Model spend. ``model_calls``/``total_tokens`` default to the project's existing
    # per-task numbers; the ceilings are the absolute stop for growth in ADAPTIVE mode.
    model_calls: int | None = Field(default=None, ge=1, le=ABSOLUTE_MAX_MODEL_CALLS)
    model_calls_ceiling: int = Field(default=24, ge=1, le=ABSOLUTE_MAX_MODEL_CALLS)
    total_tokens: int | None = Field(default=None, ge=1, le=4_000_000)
    total_tokens_ceiling: int = Field(default=400_000, ge=1, le=4_000_000)
    # Loop shape.
    agent_turns: int | None = Field(default=None, ge=1, le=80)
    agent_turns_ceiling: int = Field(default=40, ge=1, le=80)
    agent_tool_calls: int | None = Field(default=None, ge=1, le=200)
    agent_tool_calls_ceiling: int = Field(default=60, ge=1, le=200)
    # Business HTTP. ``None`` means "the operator's approved number", which is also the
    # ceiling: this dimension can never be widened by the runtime.
    http_requests: int | None = Field(default=None, ge=1, le=ABSOLUTE_MAX_REQUESTS)
    http_requests_ceiling: int | None = Field(default=None, ge=1, le=ABSOLUTE_MAX_REQUESTS)
    # Wall clock.
    seconds: float | None = Field(default=None, gt=0, le=ABSOLUTE_MAX_SECONDS)
    seconds_ceiling: float = Field(default=900.0, gt=0, le=ABSOLUTE_MAX_SECONDS)
    # Per-tool loop shape. These were fixed middleware constants; they now scale with the
    # estimated obligations so 31 approved checks are not abandoned after 8 actions.
    group_runs: int = Field(default=4, ge=1, le=40)
    group_runs_ceiling: int = Field(default=8, ge=1, le=40)
    obligation_runs: int = Field(default=8, ge=1, le=96)
    obligation_runs_ceiling: int = Field(default=32, ge=1, le=96)
    # Safety reservations and the deterministic no-progress stop.
    cleanup_reserve_requests: int = Field(default=2, ge=0, le=20)
    cleanup_reserve_seconds: float = Field(default=30.0, ge=0, le=600)
    no_progress_limit: int = Field(default=2, ge=1, le=8)
    # Growth steps (ADAPTIVE only), each capped by its ceiling.
    growth_model_calls: int = Field(default=4, ge=1, le=32)
    growth_total_tokens: int = Field(default=60_000, ge=0, le=1_000_000)
    growth_http_requests: int = Field(default=6, ge=0, le=40)
    growth_seconds: float = Field(default=120.0, ge=0, le=900)

    @model_validator(mode="after")
    def ceilings_bound_bases(self):
        for base, ceiling in (("model_calls", "model_calls_ceiling"),
                              ("total_tokens", "total_tokens_ceiling"),
                              ("agent_turns", "agent_turns_ceiling"),
                              ("agent_tool_calls", "agent_tool_calls_ceiling"),
                              ("http_requests", "http_requests_ceiling"),
                              ("seconds", "seconds_ceiling"),
                              ("group_runs", "group_runs_ceiling"),
                              ("obligation_runs", "obligation_runs_ceiling")):
            value, limit = getattr(self, base), getattr(self, ceiling)
            if value is not None and limit is not None and value > limit:
                raise ValueError(f"budget base exceeds ceiling: {base}>{ceiling}")
        return self


# --------------------------------------------------------------------------- estimation


@dataclass
class Estimate:
    """A deterministic, explained request/model estimate for one round."""

    requests_min: int
    requests_max: int
    model_calls_min: int
    model_calls_max: int
    obligations: int
    reads: int
    writes: int
    negatives: int
    prerequisites: int
    cleanup: int
    mergeable: int
    basis: list[dict] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    def public(self) -> dict:
        """Never present the range as a precise cost."""
        return dict(
            schema="agenttest.execution-estimate.v1",
            requests=dict(min=self.requests_min, max=self.requests_max,
                          basis="已批准义务、依赖图与资源生命周期的最小/最大组合"),
            model_calls=dict(min=self.model_calls_min, max=self.model_calls_max),
            obligations=self.obligations, reads=self.reads, writes=self.writes,
            approved_negatives=self.negatives, prerequisites=self.prerequisites,
            cleanup_reserved=self.cleanup, mergeable_checks=self.mergeable,
            rows=self.basis[:24], unknowns=self.unknowns[:8],
            note=("估算只给出范围：合并读取、清理请求和负向准备的实际数量取决于设计，"
                  "不能提前给出精确数字。"))


def _mergeable_reads(policies, operations) -> int:
    """Read policies that share one operation/role/arguments can often be one request."""
    groups: dict[tuple, int] = {}
    for policy in policies:
        operation = operations.get(policy.operation_id)
        if operation is None or operation.method not in READ_METHODS:
            continue
        if policy.negative is not None or policy.poll or policy.csv_fixture:
            continue
        key = (policy.operation_id, tuple(sorted(policy.roles)))
        groups[key] = groups.get(key, 0) + 1
    return sum(max(0, count - 1) for count in groups.values())


def estimate_round(*, project, summary, group_operations=(), executable_checks=(),
                   requirements=(), negative_variants=True) -> Estimate:
    """Estimate one round from the approved policies, lifecycles and dependencies.

    The estimate deliberately treats every approved negative check as needing its own
    preparation and cleanup when its operation creates resources, and reports the
    *range*: the number of obligations is not the number of HTTP requests, and it is
    never presented as if it were. ``negative_variants`` is the goal-kind decision: a
    functional round that will not run the approved invalid-input variants must not be
    estimated as if it would, or the authority diagnostic cries wolf.
    """
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    allowed = set(project.target.allowed_operations)
    logins = {role.login_operation for role in project.target.roles.values()}
    lifecycle_creates = {rule.create_operation for rule in project.target.resource_lifecycles}
    creation_rules = set(getattr(project.target, "resource_creations", {}) or {})
    scoped = {operation for operation in group_operations if operation in allowed}
    scoped.update(item.get("operation_id") for item in summary.get("groups") or ()
                  if item.get("operation_id"))
    if not scoped:
        scoped = {operation.operation_id for operation in project.contract.operations
                  if operation.operation_id in allowed} - logins
    policies = [policy for policy in project.registry.definitions()
                if policy.operation_id in scoped and policy.operation_id not in logins]
    rows, unknowns = [], []
    reads = writes = negatives = prerequisites = cleanup = 0
    for policy in policies:
        operation = operations.get(policy.operation_id)
        if operation is None:
            continue
        if policy.negative is not None and not negative_variants:
            # Approved but not requested by this goal kind: counted for the report, never
            # planned as a request (the scope ledger records the same difference).
            negatives += 1
            continue
        requests = 1
        reason = "一次已批准请求"
        if policy.negative is not None:
            negatives += 1
            reason = "一次已批准异常取值请求"
            if operation.method == "POST":
                prerequisites += 1
                cleanup += 1
                requests += 1
                reason += "（若需先建资源：+1 前置，+1 清理）"
        elif operation.method in READ_METHODS:
            reads += 1
        elif operation.method in WRITE_METHODS:
            writes += 1
            if policy.operation_id in lifecycle_creates or policy.operation_id in creation_rules:
                cleanup += 1
                requests += 1
                reason = "一次创建 + 一次已批准清理删除"
        if policy.prerequisite_policy_ids:
            prerequisites += len(policy.prerequisite_policy_ids)
        rows.append(dict(policy_id=policy.id, operation_id=policy.operation_id,
                         method=operation.method, requests=requests, reason=reason))
    mergeable = _mergeable_reads(policies, operations)
    for requirement in requirements or ():
        if getattr(requirement, "disposition", None) == "needs_review":
            unknowns.append("存在待人工确认的需求子句；实际执行量可能低于估算。")
            break
    if executable_checks:
        rows.append(dict(kind="requested_checks", requests=0, reason=(
            f"{len(executable_checks)} 项原文指定的检查会并入已批准请求。")))
    roles_used = {role for policy in policies for role in policy.roles if role}
    logins_needed = len({role.login_operation for role in project.target.roles.values()
                         if role.login_operation and role.alias in roles_used})
    if logins_needed:
        prerequisites += logins_needed
        rows.append(dict(kind="authentication", requests=logins_needed,
                         reason="每个需要登录的身份一次会话请求"))
    requests_max = sum(row["requests"] for row in rows if "requests" in row) + logins_needed
    requests_min = max(1, requests_max - mergeable) if policies else 0
    if not policies:
        unknowns.append("本轮没有可执行的已批准义务。")
    groups = max(1, len(summary.get("groups") or ()) or 1)
    model_calls_min = 3 + groups           # focus + one design per group + one answer
    model_calls_max = 6 + 2 * groups + max(0, negatives // 4) + len(unknowns)
    if unknowns:
        requests_min = max(0, requests_min - len(unknowns))
    return Estimate(requests_min=requests_min, requests_max=requests_max,
                    model_calls_min=model_calls_min, model_calls_max=model_calls_max,
                    obligations=len(policies), reads=reads, writes=writes,
                    negatives=negatives, prerequisites=prerequisites, cleanup=cleanup,
                    mergeable=mergeable, basis=rows, unknowns=unknowns)


# --------------------------------------------------------------------------- resolution


@dataclass
class Grant:
    """One budget dimension with where its number came from and what stops it."""

    dimension: str
    limit: int | float
    ceiling: int | float
    source: str
    scope: str

    def public(self) -> dict:
        return dict(limit=self.limit, ceiling=self.ceiling, source=self.source,
                    scope=self.scope)


@dataclass
class BudgetPlan:
    """The resolved budgets for one task, before or after adaptive growth."""

    mode: str
    grants: dict[str, Grant]
    estimate: Estimate | None = None
    authority: dict | None = None
    notes: list[str] = field(default_factory=list)

    def limit(self, dimension: str):
        grant = self.grants.get(dimension)
        return None if grant is None else grant.limit

    def public(self) -> dict:
        return dict(schema="agenttest.execution-budget.v1", mode=self.mode,
                    dimensions={key: grant.public()
                                for key, grant in self.grants.items()},
                    estimate=(self.estimate.public() if self.estimate else None),
                    authority=self.authority, notes=self.notes[:12])


def resolve_plan(*, budget: ExecutionBudget, target, fallback, estimate: Estimate | None):
    """Resolve configured/legacy numbers into per-dimension grants.

    ``fallback`` carries the numbers the product already used (project budget, task
    deadline, approved target requests). In FIXED mode those numbers are used verbatim;
    in ADAPTIVE mode the *initial* limits take the estimate into account and are then
    bounded by the ceilings, while the business-HTTP permission stays untouched.
    """
    mode = budget.mode
    approved_requests = int(getattr(target, "max_requests", 0) or fallback["http_requests"])
    http_ceiling = budget.http_requests_ceiling
    http_ceiling = (approved_requests if http_ceiling is None
                    else min(int(http_ceiling), approved_requests))
    model_base = (budget.model_calls if budget.model_calls is not None
                  else fallback["model_calls"])
    token_base = (budget.total_tokens if budget.total_tokens is not None
                  else fallback["total_tokens"])
    turn_base = (budget.agent_turns if budget.agent_turns is not None
                 else fallback["agent_turns"])
    tool_base = (budget.agent_tool_calls if budget.agent_tool_calls is not None
                 else fallback["agent_tool_calls"])
    second_base = (budget.seconds if budget.seconds is not None
                   else fallback["seconds"])
    grants, notes, authority = {}, [], None
    if mode == ADAPTIVE and estimate is not None:
        wanted_models = max(model_base, int(estimate.model_calls_max))
        model_base = min(max(model_base, min(wanted_models, budget.model_calls_ceiling)),
                         budget.model_calls_ceiling)
        # The HTTP dimension is the operator's permission: the estimate never shrinks it and
        # the runtime never widens it. When the estimate needs more than the approval, that
        # is an authority question, reported below instead of being spent automatically.
        http_base = min(int(budget.http_requests) if budget.http_requests is not None
                        else http_ceiling, http_ceiling)
        if int(estimate.requests_max) > http_ceiling:
            authority = dict(
                code="HTTP_AUTHORITY_INSUFFICIENT",
                required_requests=int(estimate.requests_max),
                approved_requests=http_ceiling,
                obligations=int(estimate.obligations),
                message=("本轮已批准义务的估算请求数超过项目授权上限；"
                         "需要操作员扩大授权后才能完整覆盖，未自动扩大业务请求权限。"))
            notes.append("估算请求数超过已授权上限；保留该诊断，等待明确授权。")
    else:
        http_base = min(budget.http_requests or http_ceiling, http_ceiling)
    grants["model_calls"] = Grant("model_calls", int(model_base),
                                  int(budget.model_calls_ceiling),
                                  "project.execution.model_calls"
                                  if budget.model_calls is not None else "project budget",
                                  "one task")
    grants["total_tokens"] = Grant("total_tokens", int(token_base),
                                   int(budget.total_tokens_ceiling),
                                   "project budget" if budget.total_tokens is None
                                   else "project.execution.total_tokens", "one task")
    grants["agent_turns"] = Grant("agent_turns", int(turn_base),
                                  int(budget.agent_turns_ceiling), "project budget",
                                  "one agent loop")
    grants["agent_tool_calls"] = Grant("agent_tool_calls", int(tool_base),
                                       int(budget.agent_tool_calls_ceiling),
                                       "project budget", "one agent loop")
    grants["http_requests"] = Grant("http_requests", int(http_base), int(http_ceiling),
                                    "target.max_requests (operator approval)",
                                    "one task, never widened by the runtime")
    grants["seconds"] = Grant("seconds", float(second_base),
                              float(min(budget.seconds_ceiling, fallback.get(
                                  "seconds_ceiling", budget.seconds_ceiling))),
                              "task deadline", "one task")
    grants["group_runs"] = Grant("group_runs", int(budget.group_runs),
                                 int(budget.group_runs_ceiling), "loop shape",
                                 "one group")
    obligation_base = int(budget.obligation_runs)
    if mode == ADAPTIVE and estimate is not None:
        # One candidate action per open obligation is the realistic upper bound; reading a
        # report or repairing a rejection is already counted by the tool-call budget.
        wanted = min(budget.obligation_runs_ceiling,
                     max(obligation_base, int(estimate.obligations)))
        obligation_base = wanted
    grants["obligation_runs"] = Grant("obligation_runs", obligation_base,
                                      int(budget.obligation_runs_ceiling), "loop shape",
                                      "one obligation action")
    grants["cleanup_reserve_requests"] = Grant(
        "cleanup_reserve_requests", int(budget.cleanup_reserve_requests),
        int(budget.cleanup_reserve_requests), "safety reserve",
        "kept back while a created resource is still owned")
    grants["cleanup_reserve_seconds"] = Grant(
        "cleanup_reserve_seconds", float(budget.cleanup_reserve_seconds),
        float(budget.cleanup_reserve_seconds), "safety reserve", "one task")
    if mode == FIXED:
        notes.append("FIXED：使用配置的固定上限，不做估算扩展，便于 A/B 复现。")
    return BudgetPlan(mode=mode, grants=grants, estimate=estimate, authority=authority,
                      notes=notes)


# --------------------------------------------------------------------------- progress


def progress_signature(summary: dict) -> dict:
    """The measured facts that count as *progress*; nothing about intent or prose.

    A repeated failing design, a repeated read with the same result, or another model
    turn with no new evidence leaves this signature unchanged on purpose.
    """
    cases = summary.get("cases") or ()
    evidence = set()
    for case in cases:
        if case.get("status") != "FINISHED":
            continue
        for step in case.get("evidence") or ():
            if step.get("executed") and step.get("policy_id"):
                evidence.add(step["policy_id"])
        for step in case.get("cleanup_evidence") or ():
            if step.get("executed") and step.get("policy_id"):
                evidence.add(step["policy_id"])
    receipts = {}
    for case in cases:
        for row in [*(case.get("resource_lifecycle") or ()),
                    *(case.get("created_resources") or ())]:
            key = str(row.get("create_step") or row.get("cleanup_step") or "")
            receipts[key] = (row.get("status"), row.get("cleanup_result"))
    plans = {str(case.get("scenario_fingerprint") or case.get("case_id"))
             for case in cases if case.get("status") == "FINISHED"}
    blocked = len(summary.get("input_blocked_operations") or ()) + len(
        summary.get("source_blocked_operations") or ())
    return dict(evidence=frozenset(evidence), receipts=frozenset(receipts.items()),
                plans=frozenset(plans),
                unresolved_blocks=blocked,
                questions=len(summary.get("questions") or ()))


def progress_delta(previous: dict | None, current: dict) -> dict:
    """Which of the five accepted progress kinds actually changed."""
    if previous is None:
        initial = bool(current["plans"] or current["evidence"])
        return dict(new_plans=len(current["plans"]), new_evidence=len(current["evidence"]),
                    closed_obligations=len(current["evidence"]),
                    resolved_blocks=0, cleanup_completed=0, progress=initial,
                    kinds=["INITIAL_EVIDENCE"] if initial else [])
    kinds = []
    new_plans = len(current["plans"] - previous["plans"])
    new_evidence = len(current["evidence"] - previous["evidence"])
    resolved = max(0, previous["unresolved_blocks"] - current["unresolved_blocks"])
    cleaned = sum(1 for key, value in current["receipts"]
                  if key not in previous["receipts"] and value[0] == "CLEANED")
    if new_plans:
        kinds.append("NEW_LEGAL_PLAN")
    if new_evidence:
        kinds.append("NEW_TRUSTED_EVIDENCE")
        kinds.append("OBLIGATIONS_COVERED")
    if resolved:
        kinds.append("BLOCK_RESOLVED")
    if cleaned:
        kinds.append("CLEANUP_COMPLETED")
    return dict(new_plans=new_plans, new_evidence=new_evidence,
                closed_obligations=new_evidence, resolved_blocks=resolved,
                cleanup_completed=cleaned, progress=bool(kinds), kinds=kinds)


class ExecutionState:
    """Runtime state of one round: usage, current allowance, growth and stop reason."""

    def __init__(self, plan: BudgetPlan, *, budget: ExecutionBudget):
        self.plan = plan
        self.budget = budget
        self.growths: list[dict] = []
        self.no_progress_rounds = 0
        self.signature = None
        self.delta: dict = {}
        self.stop_reason: str | None = None
        self.stop_detail: str | None = None
        self.started_progress = 0

    # ------------------------------------------------------------------ dimensions
    def limit(self, dimension: str):
        return self.plan.limit(dimension)

    def growth_for(self, dimension: str):
        return {"model_calls": self.budget.growth_model_calls,
                "total_tokens": self.budget.growth_total_tokens,
                "http_requests": self.budget.growth_http_requests,
                "seconds": self.budget.growth_seconds,
                "agent_turns": 4, "agent_tool_calls": 6,
                "group_runs": 1, "obligation_runs": 4}.get(dimension, 0)

    def extend(self, dimension: str, amount, *, reason: str) -> bool:
        """Raise one allowance inside its ceiling; the permission dimension never moves."""
        grant = self.plan.grants.get(dimension)
        if grant is None or amount <= 0:
            return False
        if dimension == "http_requests":
            # Growing the business request budget needs operator authority, never progress.
            return False
        ceiling = grant.ceiling
        if dimension == "seconds":
            # Keep the cleanup time reserve intact: growth may not consume the last slice
            # before the outer deadline, where an unfinished cleanup must still fit.
            ceiling = max(grant.limit, ceiling - self.budget.cleanup_reserve_seconds)
        grown = min(ceiling, grant.limit + amount)
        if grown <= grant.limit:
            return False
        previous = grant.limit
        grant.limit = grown
        self.growths.append(dict(dimension=dimension, previous=previous, limit=grown,
                                 ceiling=grant.ceiling, reason=reason))
        return True

    def observe(self, summary: dict, *, active: bool = True) -> dict:
        """Record one observation point; grows after progress, counts stagnation otherwise."""
        if not active:
            return dict(progress=False, kinds=[], growths=[], no_progress_rounds=0)
        current = progress_signature(summary)
        delta = progress_delta(self.signature, current)
        self.signature = current
        self.delta = delta
        growths = []
        if self.plan.mode != ADAPTIVE:
            return dict(delta, growths=[], no_progress_rounds=0)
        if delta["progress"]:
            self.no_progress_rounds = 0
            self.started_progress += 1
            if self.started_progress > 1:
                for dimension in ("model_calls", "total_tokens", "agent_turns",
                                  "agent_tool_calls", "seconds", "group_runs",
                                  "obligation_runs"):
                    if self.extend(dimension, self.growth_for(dimension),
                                   reason="progress:" + ",".join(delta["kinds"])):
                        growths.append(dimension)
        else:
            self.no_progress_rounds += 1
            if self.no_progress_rounds >= self.budget.no_progress_limit:
                self.stop("NO_PROGRESS",
                          "连续 " + str(self.no_progress_rounds)
                          + " 次观察没有新的合法规划、可信证据、义务覆盖、阻塞解除或清理完成。")
        return dict(delta, growths=growths,
                    no_progress_rounds=self.no_progress_rounds)

    def stop(self, reason: str, detail: str) -> None:
        if self.stop_reason is None:
            self.stop_reason, self.stop_detail = reason, detail

    @property
    def stopped(self) -> bool:
        return self.stop_reason is not None

    # ------------------------------------------------------------------ publishing
    def public(self) -> dict:
        return dict(
            schema="agenttest.execution-budget.v1", mode=self.plan.mode,
            dimensions={key: grant.public()
                        for key, grant in self.plan.grants.items()},
            estimate=(self.plan.estimate.public() if self.plan.estimate else None),
            authority=self.plan.authority, notes=self.plan.notes[:12],
            growths=self.growths[:20], no_progress_rounds=self.no_progress_rounds,
            progress=self.delta, stop_reason=self.stop_reason,
            stop_detail=self.stop_detail)


def resolve_fallback(project, summary) -> dict:
    """The numbers the product already used, before any adaptive estimation."""
    budget = project.registration.budget
    return dict(model_calls=max(1, int(getattr(budget, "suite_max_model_calls", 8) or 8)),
                total_tokens=max(1, int(getattr(budget, "suite_max_total_tokens",
                                                100_000) or 100_000)),
                agent_turns=int(getattr(budget, "agent_max_turns", 16) or 16),
                agent_tool_calls=int(getattr(budget, "agent_max_tool_calls", 24) or 24),
                http_requests=int(getattr(project.target, "max_requests", 24) or 24),
                seconds=float((summary.get("budget") or {}).get("max_seconds", 600.0)))


__all__ = [
    "ABSOLUTE_MAX_MODEL_CALLS",
    "ABSOLUTE_MAX_REQUESTS",
    "ABSOLUTE_MAX_SECONDS",
    "ADAPTIVE",
    "BudgetPlan",
    "Estimate",
    "ExecutionBudget",
    "ExecutionState",
    "FIXED",
    "Grant",
    "MODES",
    "estimate_round",
    "progress_delta",
    "progress_signature",
    "resolve_fallback",
    "resolve_plan",
]
