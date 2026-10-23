"""Natural-language facade around the existing trusted Workflow, never a second executor."""

from copy import deepcopy

import httpx

from agenttest.goal_derivation import DerivedGoal, derive_goal
from agenttest.project_goals import GoalProject, check_goal_credentials
from agenttest.report import fingerprint
from agenttest.task_contract import task_validator


class GoalPlanner:
    """Add reusable public configuration; never expose derived steps or acceptance rubric."""

    def __init__(self, planner, project: GoalProject, goal: DerivedGoal, guard=None):
        self.planner = planner
        self.guard = guard
        self.policy_roles = {}
        for step in goal.task.requirements.steps:
            self.policy_roles.setdefault(step.policy_id, set()).add(step.role)
        definitions = {p.id: p for p in project.registry.definitions()}

        def public_policy_ids(template):
            ids = {template.policy_id, *[v.policy_id for v in template.variants]}
            return sorted(p for p in self.policy_roles if p in ids
                or definitions[p].negative and definitions[p].negative.base_policy_id in ids)

        goal_catalog = project.catalog.model_dump()
        goal_catalog["goals"] = [dict(id=g.id, aliases=g.aliases, role=g.role,
            approved_policy_ids=public_policy_ids(g),
            policy_id=public_policy_ids(g)[0],
            variants=[v.model_dump() for v in g.variants if v.policy_id in self.policy_roles])
            for g in project.catalog.goals if g.id in goal.requested_goals
            or g.policy_id in self.policy_roles]
        self.public = dict(goal_scope=goal.scope, requested_goals=list(goal.requested_goals),
            project_goal_catalog=goal_catalog,
            synthetic_input_defaults=deepcopy(goal.input_defaults),
            input_default_rule="Use synthetic_input_defaults for unspecified literal inputs; "
                "explicit user literals take precedence. "
                "This does not grant operations or assertions.",
            coordination_rule="For a bounded suite group, cover only requested_goals in this call; "
                "the coordinator accounts for other groups. Do not add other catalog goals.")

    @property
    def stats(self):
        return getattr(self.planner, "stats", None)

    @property
    def token_reservation(self):
        return getattr(self.planner, "token_reservation", 0)

    async def generate(self, context):
        if self.guard is not None:
            self.guard()
        public = dict(context, **deepcopy(self.public))
        public["policies"] = [dict(p, roles=[r for r in p["roles"]
            if r in self.policy_roles[p["id"]]]) for p in public["policies"]
            if p["id"] in self.policy_roles]
        operation_ids = {p["operation_id"] for p in public["policies"]}
        roles = {r for values in self.policy_roles.values() for r in values if r is not None}
        public["roles"] = [r for r in public["roles"] if r in roles]
        public["operations"] = [o for o in public["operations"] if o["id"] in operation_ids]
        public["authentication"] = [dict(a, before_operation_ids=[o for o in
            a["before_operation_ids"] if o in operation_ids]) for a in
            public.get("authentication", []) if a["role"] in roles]
        schemes = {s for o in public["operations"] for req in o["security"] for s in req}
        public["security_schemes"] = {k: v for k, v in public["security_schemes"].items()
                                     if k in schemes}
        return await self.planner.generate(public)


async def run_goal(project, user_task, planner, store, *, environ, transport=None,
                   budget=None, execution_guard=None, derived=None, analyzer=None,
                   readonly_allowance=None, feedback_model_allowance=None):
    """Preflight before spending model/HTTP budget; immutable derived gate during execution."""
    from agenttest.workflow import Workflow

    goal = derived or derive_goal(project, user_task)
    if (goal.project_identity != project.identity()
            or goal.user_task_sha256 != fingerprint(user_task)):
        raise ValueError("DERIVED_GOAL_VERSION_MISMATCH")
    if goal.task is None:
        return goal, None
    check_goal_credentials(project, goal.task, environ)
    validator = task_validator(goal.task, project.contract, project.target, project.registry)
    from agenttest.goal_suite import BudgetPlanner, BudgetTransport, SuiteLedger

    local = SuiteLedger(project.target.max_requests, project.registration.budget.max_model_calls,
                        project.registration.budget.max_total_tokens)

    def guard():
        if project.identity() != goal.project_identity:
            raise ValueError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")
        if execution_guard is not None:
            execution_guard()
    inner = transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    options = {}
    if analyzer is not None and project.capabilities.entries():
        from agenttest.investigation import FeedbackBudget, InvestigationConfig

        options = dict(capabilities=project.capabilities,
            analyzer=BudgetPlanner(analyzer, local, guard),
            investigation=InvestigationConfig(enabled=True, trusted_symptoms=True,
                feedback=FeedbackBudget(
                    max_model_calls=min(max(0, project.registration.budget.max_model_calls - 1),
                        feedback_model_allowance if feedback_model_allowance is not None else 3),
                    max_requests=min(project.registration.budget.max_readonly_investigations,
                        readonly_allowance if readonly_allowance is not None else 2))))
    try:
        report = await Workflow(project.document, project.target, project.registry,
            GoalPlanner(BudgetPlanner(planner, local), project, goal, guard), store,
            transport=BudgetTransport(inner, local, guard), environ=environ,
            user_task=user_task, intent_validator=validator, budget=budget,
            execution_guard=guard, **options).run()
    finally:
        if transport is None:
            await inner.aclose()
    return goal, report
