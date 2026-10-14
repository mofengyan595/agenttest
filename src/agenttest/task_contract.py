"""Project-neutral, operator-owned task requirements; never accepted from a planner."""

from typing import Literal

from pydantic import Field, model_validator

from agenttest.contracts import ContractSummary
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
        if not score_intent(task.requirements, scenario).passed:
            raise NeedsReview("INTENT_TASK_CONTRACT_PLAN_MISMATCH")

    return validate
