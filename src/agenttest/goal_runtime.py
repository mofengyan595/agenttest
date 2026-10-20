"""Natural-language facade around the existing trusted Workflow, never a second executor."""

from copy import deepcopy

import httpx

from agenttest.goal_derivation import DerivedGoal, derive_goal
from agenttest.project_goals import GoalProject, check_goal_credentials
from agenttest.report import fingerprint
from agenttest.task_contract import task_validator


class GoalPlanner:
    """Add reusable public configuration; never expose derived steps or acceptance rubric."""

    def __init__(self, planner, project: GoalProject, goal: DerivedGoal):
        self.planner = planner
        self.public = dict(goal_scope=goal.scope, requested_goals=list(goal.requested_goals),
            project_goal_catalog=project.catalog.model_dump(),
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
        public = dict(context, **deepcopy(self.public))
        return await self.planner.generate(public)


async def run_goal(project, user_task, planner, store, *, environ, transport=None,
                   budget=None, execution_guard=None, derived=None, analyzer=None):
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
    inner = transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    options = {}
    if analyzer is not None and project.capabilities.entries():
        from agenttest.investigation import FeedbackBudget, InvestigationConfig

        options = dict(capabilities=project.capabilities,
            analyzer=BudgetPlanner(analyzer, local),
            investigation=InvestigationConfig(enabled=True, trusted_symptoms=True,
                feedback=FeedbackBudget(
                    max_model_calls=max(0, project.registration.budget.max_model_calls - 1),
                    max_requests=project.registration.budget.max_readonly_investigations)))
    try:
        report = await Workflow(project.document, project.target, project.registry,
            GoalPlanner(BudgetPlanner(planner, local), project, goal), store,
            transport=BudgetTransport(inner, local), environ=environ,
            user_task=user_task, intent_validator=validator, budget=budget,
            execution_guard=execution_guard, **options).run()
    finally:
        if transport is None:
            await inner.aclose()
    return goal, report
