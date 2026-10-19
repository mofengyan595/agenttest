"""Project-neutral, operator-owned task requirements; never accepted from a planner."""

from typing import Literal

from pydantic import Field, model_validator

from agenttest.contracts import ContractSummary
from agenttest.diagnostics import IntentDiagnostic
from agenttest.dsl import Scenario
from agenttest.eval_v2.models import Rubric
from agenttest.eval_v2.scoring import score_intent
from agenttest.planner_input import validate_user_task
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.report import fingerprint
from agenttest.safety import StrictModel, TargetConfig


class TaskContract(StrictModel):
    """An exact task and partial-order requirements, bound to trusted project inputs.

    This is runtime configuration, not a formal-evaluation label or human-review
    certificate. Its file must be selected by the operator, like PolicyRegistry.
    """

    schema_version: Literal["agenttest.task-contract.v1"] = "agenttest.task-contract.v1"
    user_task: str
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements: Rubric

    @model_validator(mode="after")
    def safe_task(self):
        validate_user_task(self.user_task)
        return self


def task_validator(
    task: TaskContract, contract: ContractSummary, target: TargetConfig, registry: PolicyRegistry,
):
    """Snapshot operator requirements without granting operations or changing assertions."""
    task = TaskContract.model_validate_json(task.model_dump_json())
    if (
        task.contract_sha256 != fingerprint(contract.model_dump())
        or task.target_config_sha256 != fingerprint(target.model_dump())
        or task.policy_sha256 != registry.digest()
    ):
        raise ValueError("Task contract does not match trusted project inputs")

    def validate(user_task: str, scenario: Scenario) -> None:
        if fingerprint(user_task) != fingerprint(task.user_task):
            raise NeedsReview("INTENT_TASK_CONTRACT_TASK_MISMATCH")
        score = score_intent(task.requirements, scenario)
        if not score.passed:
            failure = NeedsReview("INTENT_TASK_CONTRACT_PLAN_MISMATCH")
            branches = {"STEP_COUNT_MISMATCH": "STEP_COUNT",
                "OPERATION_ROLE_POLICY_MISMATCH": "OPERATION_ROLE_POLICY",
                "PARAMETER_SET_MISMATCH": "PARAMETERS",
                "VALUE_SOURCE_SELECTOR_MISMATCH": "SOURCE_OR_VALUE", "ORDER_MISMATCH": "ORDER",
                "UNLABELED_EXECUTION_OPTION": "EXECUTION_OPTION",
                "SUBGOAL_MISSING": "GOAL_COVERAGE",
                "EXTRA_OR_MISSING_STEPS": "STEP_SET"}
            keys = [s.key for s in task.requirements.steps]
            failure.diagnostic.intent = []
            for detail in score.diagnostics[:8]:
                family, _, key = detail.partition(":")
                key = key.split(":", 1)[0].split(".", 1)[0]
                failure.diagnostic.intent.append(IntentDiagnostic(branch=branches[family],
                    requirement_index=keys.index(key) if key in keys else None))
            raise failure

    return validate
