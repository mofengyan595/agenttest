"""Single/flow/project suites reuse one trusted workflow and one fixed aggregate budget."""

from copy import deepcopy
from dataclasses import dataclass

import httpx

from agenttest.discovery import discover
from agenttest.discovery_schema import fields, variations
from agenttest.goal_derivation import (
    DerivedGoal,
    _derive_requirements,
    derive_goal,
    public_goal_plan,
)
from agenttest.llm_planner import PlannerFailure
from agenttest.policies import NeedsReview
from agenttest.project_goals import check_goal_credentials
from agenttest.report import ModelCallStats, Outcome, fingerprint
from agenttest.request_validation import validate_value


@dataclass(repr=False)
class SuiteCase:
    id: str
    goal: DerivedGoal | None
    operation_id: str | None
    kinds: list[str]
    reason: str | None = None

    def preview(self):
        return dict(case_id=self.id, operation_id=self.operation_id, kinds=self.kinds,
            status="READY" if self.goal and self.goal.task and not self.reason else "NEEDS_REVIEW",
            reason=self.reason,
            question=self.goal.question if self.goal else None,
            requested_goals=self.goal.requested_goals if self.goal else [],
            required_plan=public_goal_plan(self.goal) if self.goal else [],
            max_business_requests=len(self.goal.task.requirements.steps)
                if self.goal and self.goal.task else 0)


@dataclass(repr=False)
class GoalSuite:
    project_identity: str
    user_task_sha256: str
    scope: str
    cases: list[SuiteCase]
    discovery: dict
    operation_ids: list[str]
    budgets: dict

    def allocated_cases(self, max_requests=None):
        used, allowed = 0, set()
        for case in self.cases:
            if case.goal and case.goal.task and not case.reason:
                required = len(case.goal.task.requirements.steps)
                if used + required <= (self.budgets["max_requests"]
                                       if max_requests is None else max_requests):
                    allowed.add(case.id)
                    used += required
        return allowed

    def preview(self):
        allowed = self.allocated_cases()
        cases = [c.preview() for c in self.cases]
        for case in cases:
            if case["status"] == "READY" and case["case_id"] not in allowed:
                case.update(status="NOT_EXECUTED", reason="SUITE_BUDGET_OR_USAGE_STOP")
        return dict(schema_version="agenttest.goal-suite.v1",
            project_identity=self.project_identity,
            user_task_sha256=self.user_task_sha256, scope=self.scope,
            cases=cases, budget=self.budgets,
            side_effects="AUTHORIZED_ISOLATED_WRITES_POSSIBLE",
            candidate_generation=self.discovery, http_requests=0, model_calls=0,
            coverage=dict(operation_denominator=len(self.operation_ids),
                scenario_denominator=len(self.cases), executed_operations=0, executed_scenarios=0))


def build_suite(project, user_task, *, environ=None) -> GoalSuite:
    """Expand approved goals; static negatives retain unknown expectations."""
    base = derive_goal(project, user_task)
    cases, signatures = [], {}
    policies = {p.id: p for p in project.registry.definitions()}

    def add(goal, op, kind, reason=None):
        signature = (fingerprint(goal.task.requirements.model_dump(mode="json"))
                     if goal and goal.task else fingerprint([op, kind, reason]))
        if signature in signatures:
            signatures[signature].kinds.append(kind)
            return
        case = SuiteCase("case_" + signature[:24], goal, op, [kind], reason)
        signatures[signature] = case
        cases.append(case)

    if base.scope == "project" and base.reason == "INTENT_PROJECT_GROUPING_REQUIRED":
        for template in project.catalog.goals:
            variants = [([], "normal")] + [([v.after_goal], "state:" + v.after_goal)
                                          for v in template.variants]
            for prerequisites, kind in variants:
                goal = DerivedGoal(project.identity(), fingerprint(user_task),
                    "flow" if prerequisites else "single", [*prerequisites, template.id])
                try:
                    _derive_requirements(project, user_task, goal, None)
                except NeedsReview as exc:
                    goal.reason = exc.diagnostic.code
                except ValueError:
                    goal.reason = "INTENT_GOAL_CONFIGURATION_UNSUPPORTED"
                add(goal, policies[template.policy_id].operation_id,
                    template.id + ":" + kind, goal.reason)
        represented = {c.operation_id for c in cases}
        represented.update(s.operation_id for c in cases if c.goal and c.goal.task
                           for s in c.goal.task.requirements.steps)
        for operation_id in sorted(set(project.target.allowed_operations) - represented):
            add(None, operation_id, "unmapped_project_operation",
                "PROJECT_OPERATION_WITHOUT_APPROVED_GOAL")
    elif base.task:
        template = next(g for g in project.catalog.goals if g.id == base.requested_goals[-1])
        operation_id = policies[template.policy_id].operation_id
        add(base, operation_id, "normal" if base.scope == "single" else "resource_flow")
        if base.scope == "single":
            _single_candidates(project, base, operation_id, add)
    else:
        add(base, None, "unverified_goal", base.reason)
    if environ is not None:
        for case in cases:
            if case.goal and case.goal.task:
                try:
                    check_goal_credentials(project, case.goal.task, environ)
                except ValueError:
                    case.reason = "PROJECT_CREDENTIAL_PREFLIGHT_FAILED"
    discovery = discover(project.contract, project.registry, project.target)
    scope_ops = (set(project.target.allowed_operations) if base.scope == "project" else
        {s.operation_id for c in cases if c.goal and c.goal.task
         for s in c.goal.task.requirements.steps})
    selected = [c for c in discovery.candidates if c.operation_id in scope_ops]
    return GoalSuite(project.identity(), fingerprint(user_task), base.scope, cases,
        dict(candidates=len(selected), normal=sum(c.polarity == "normal" for c in selected),
             negative=sum(c.polarity == "negative" for c in selected),
             review=sum(c.polarity == "review" for c in selected),
             configuration_operations=len(scope_ops), candidate_operations=len({
                 c.operation_id for c in selected}), execution_count=0), sorted(scope_ops),
        dict(max_requests=project.target.max_requests,
             max_model_calls=project.registration.budget.suite_max_model_calls,
             max_total_tokens=project.registration.budget.suite_max_total_tokens,
             per_run_model_calls=project.registration.budget.max_model_calls,
             per_run_readonly_investigations=project.registration.budget.max_readonly_investigations))


def _single_candidates(project, base, operation_id, add):
    operation = next(o for o in project.contract.operations if o.operation_id == operation_id)
    try:
        specs, _ = fields(operation, project.contract.version, "#/operations")
    except (NeedsReview, ValueError):
        add(None, operation_id, "unsupported_schema", "SCHEMA_SUBSET_UNSUPPORTED")
        return
    definitions = {p.id: p for p in project.registry.definitions()}
    for spec in specs:
        for kind, polarity, description, value in variations(spec):
            name = f"{kind}:{spec.location}:{spec.name}:{fingerprint(description.model_dump())[:8]}"
            if polarity == "negative":
                add(None, operation_id, name, "EXPECTED_ERROR_BEHAVIOR_NOT_APPROVED")
                continue
            # An explicit literal restricts this task; do not silently replace user data.
            if any(q in base.task.user_task for q in ('"', "“", "”")):
                add(None, operation_id, name, "EXPLICIT_INPUT_VALUE_FIXED")
                continue
            goal = deepcopy(base)
            steps = [s for s in goal.task.requirements.steps if s.operation_id == operation_id]
            if len(steps) != 1:
                add(None, operation_id, name, "VARIATION_TARGET_AMBIGUOUS")
                continue
            step = steps[0]
            args = [a for a in step.arguments if (a.location, a.name) == (spec.location, spec.name)]
            if len(args) != 1 or args[0].source is not None:
                add(None, operation_id, name, "DYNAMIC_RESOURCE_VARIATION_REQUIRES_REVIEW")
                continue
            rule = next((r for r in definitions[step.policy_id].binding_rules
                         if (r.location, r.name) == (spec.location, spec.name)), None)
            try:
                if rule is None or rule.literal_schema is None:
                    raise ValueError("No literal approval")
                validate_value(value, spec.schema)
                validate_value(value, rule.literal_schema)
            except (ValueError, NeedsReview):
                add(None, operation_id, name, "VARIATION_OUTSIDE_APPROVED_POLICY")
                continue
            args[0].value = value
            for default in goal.input_defaults:
                if (default["policy_id"], default["location"], default["name"]) == (
                        step.policy_id, spec.location, spec.name):
                    default["value"] = value
            add(goal, operation_id, name)


@dataclass
class SuiteLedger:
    max_requests: int
    max_model_calls: int
    max_total_tokens: int
    requests: int = 0
    model_calls: int = 0
    known_tokens: int = 0
    usage_unknown: bool = False

    def summary(self):
        return dict(requests=self.requests, model_calls=self.model_calls,
            known_tokens=self.known_tokens, total_tokens=None if self.usage_unknown else
            self.known_tokens, usage_known=not self.usage_unknown)


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner, ledger, guard=None):
        self.inner, self.ledger = inner, ledger
        self.guard = guard

    async def handle_async_request(self, request):
        if self.guard is not None:
            try:
                self.guard()
            except Exception as exc:
                raise httpx.RequestError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED") from exc
        if self.ledger.requests >= self.ledger.max_requests:
            raise httpx.RequestError("SUITE_REQUEST_BUDGET_EXHAUSTED")
        # Count before dispatch: a timeout may already have produced external effects.
        self.ledger.requests += 1
        return await self.inner.handle_async_request(request)


class BudgetPlanner:
    def __init__(self, inner, ledger, guard=None):
        self.inner, self.ledger = inner, ledger
        self.guard = guard
        self.stats = ModelCallStats()

    @property
    def token_reservation(self):
        return getattr(self.inner, "token_reservation", 0)

    @property
    def evidence_relations(self):
        return getattr(self.inner, "evidence_relations", False)

    @property
    def last_proposal(self):
        return getattr(self.inner, "last_proposal", None)

    async def generate(self, context):
        self.stats = ModelCallStats()
        if self.guard is not None:
            self.guard()
        if (self.ledger.usage_unknown or self.ledger.model_calls >= self.ledger.max_model_calls
                or self.ledger.known_tokens + self.token_reservation
                > self.ledger.max_total_tokens):
            raise PlannerFailure(Outcome.NEEDS_REVIEW, "SUITE_MODEL_BUDGET_EXHAUSTED")
        try:
            return await self.inner.generate(context)
        finally:
            stats = getattr(self.inner, "stats", None)
            if isinstance(stats, ModelCallStats):
                self.stats = stats.model_copy(deep=True)
                self.ledger.model_calls += stats.calls
                if stats.calls:
                    if stats.total_tokens is None:
                        self.ledger.usage_unknown = True
                    else:
                        self.ledger.known_tokens += stats.total_tokens
            else:
                # Fake protocol attempts still consume coordination slots; no paid usage.
                self.ledger.model_calls += 1


async def run_suite(project, user_task, planner, store, *, environ, transport=None,
                    budget=None, execution_guard=None, analyzer=None, ledger=None, on_report=None):
    from agenttest.goal_runtime import run_goal

    suite = build_suite(project, user_task, environ=environ)
    ledger = ledger or SuiteLedger(**{key: suite.budgets[key] for key in (
        "max_requests", "max_model_calls", "max_total_tokens")})
    own_transport = transport is None
    inner = transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    bounded_transport = BudgetTransport(inner, ledger)
    bounded_planner = BudgetPlanner(planner, ledger)
    rows = []
    allocated = suite.allocated_cases(max(0, ledger.max_requests - ledger.requests))
    try:
        for case in suite.cases:
            row = case.preview()
            goal = case.goal
            if goal is None or goal.task is None:
                rows.append(row)
                continue
            if execution_guard is not None:
                try:
                    execution_guard()
                except Exception:
                    row.update(status="NOT_EXECUTED",
                               reason="PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")
                    rows.append(row)
                    continue
            try:
                check_goal_credentials(project, goal.task, environ)
            except ValueError:
                row.update(status="NEEDS_REVIEW", reason="PROJECT_CREDENTIAL_PREFLIGHT_FAILED")
                rows.append(row)
                continue
            if (case.id not in allocated
                    or ledger.requests + len(goal.task.requirements.steps) > ledger.max_requests
                    or ledger.model_calls >= ledger.max_model_calls or ledger.usage_unknown
                    or ledger.known_tokens + bounded_planner.token_reservation
                    > ledger.max_total_tokens):
                row.update(status="NOT_EXECUTED", reason="SUITE_BUDGET_OR_USAGE_STOP")
                rows.append(row)
                continue
            _, report = await run_goal(project, user_task, bounded_planner, store,
                environ=environ, transport=bounded_transport, budget=budget,
                execution_guard=execution_guard, derived=goal,
                analyzer=BudgetPlanner(analyzer, ledger) if analyzer else None)
            row.update(status="FINISHED", outcome=report.outcome.value, run_id=report.run_id,
                intent_verified=report.intent_verified, task_satisfied=report.task_satisfied,
                planned_operations=[s.operation_id for s in report.plan_structure],
                executed_operations=[s.operation_id for s in report.steps if s.attempts],
                passed_operations=[s.operation_id for s in report.steps
                                   if s.attempts and s.outcome == Outcome.PASS],
                http_requests=sum(len(s.attempts) for s in report.steps),
                reason=report.reason)
            if on_report is not None:
                on_report(goal, report)
            rows.append(row)
    finally:
        if own_transport:
            await inner.aclose()
    finished = [r for r in rows if r["status"] == "FINISHED"]
    executed = [r for r in finished if r["http_requests"]]
    passed = [r for r in finished if r["task_satisfied"]]
    configured = {r["operation_id"] for r in rows if r["status"] != "NEEDS_REVIEW"}
    result = suite.preview()
    result.update(cases=rows, usage=ledger.summary(), coverage=dict(
        operation_denominator=len(suite.operation_ids), scenario_denominator=len(rows),
        configuration_operations=len(configured - {None}),
        candidate_operations=suite.discovery["candidate_operations"],
        planned_operations=len({o for r in finished for o in r["planned_operations"]}),
        executed_operations=len({o for r in finished for o in r["executed_operations"]}),
        assertion_passed_operations=len({o for r in finished for o in r["passed_operations"]}),
        executed_scenarios=len(executed), assertion_passed_scenarios=len(passed),
        not_executed=len(rows) - len(executed)),
        complete=len(passed) == len(rows), http_requests=ledger.requests,
        model_calls=ledger.model_calls)
    return result
