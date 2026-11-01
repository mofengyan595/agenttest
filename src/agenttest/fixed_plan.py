"""An explicitly saved execution snapshot, never a certificate of natural-language intent."""

from typing import Literal

from pydantic import Field

from agenttest.report import fingerprint
from agenttest.safety import StrictModel
from agenttest.task_contract import TaskContract


class FixedPlanSnapshot(StrictModel):
    schema_version: Literal["agenttest.fixed-plan.v1"] = "agenttest.fixed-plan.v1"
    user_task: str
    source_run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scenario_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def validate_plan(self, contract, target, registry, scenario):
        if (self.scenario_sha256 != fingerprint(scenario.model_dump())
                or self.contract_sha256 != fingerprint(contract.model_dump())
                or self.target_config_sha256 != fingerprint(target.model_dump())
                or self.policy_sha256 != registry.digest()):
            raise ValueError("FIXED_PLAN_SNAPSHOT_CHANGED")


def load_saved_contract(raw):
    from agenttest.plan_json import bounded_json

    data = bounded_json(raw, 200_000)
    if not isinstance(data, dict):
        raise ValueError("SAVED_CONTRACT_INVALID")
    model = (FixedPlanSnapshot if data.get("schema_version") == "agenttest.fixed-plan.v1"
             else TaskContract)
    # JSON arrays encode ordered tuple edges; preserve the original strict JSON semantics.
    return model.model_validate_json(raw)


def replay_scope(contract, target, scenario):
    """Authentication is explicit; business mutations need a separately verified data lifecycle."""
    methods = {op.operation_id: op.method for op in contract.operations}
    authenticated = False
    for step in scenario.steps:
        method = methods.get(step.operation_id)
        role = target.roles.get(step.role)
        login = bool(role and method == "POST" and role.login_operation == step.operation_id)
        if method not in {"GET", "HEAD", "OPTIONS"} and not login:
            return "MUTATION_LIFECYCLE_REQUIRED"
        authenticated = authenticated or login
    return "AUTHENTICATED_READONLY" if authenticated else "READONLY"
