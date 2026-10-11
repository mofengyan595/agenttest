"""Operator-owned approvals, outside the model's input/output authority."""

import json

from pydantic import Field

from agenttest.dsl import AssertionPolicy, PollPolicy, Step
from agenttest.safety import StrictModel


class NeedsReview(ValueError):
    """No trusted basis exists to execute or classify this expectation."""


class RegisteredPolicy(StrictModel):
    id: str
    operation_id: str
    roles: list[str | None]
    business_scenario: str
    assertions: AssertionPolicy
    poll: PollPolicy | None = None
    # Exact binding prerequisites make data-dependent approvals auditable.
    required_bindings: dict = Field(default_factory=dict)


class PolicyRegistry:
    """Snapshot trusted configuration; return copies so callers cannot change approvals.

    Load only from an operator-selected file, never from a planner response. File
    access and review are the trust root; a model-supplied `reviewed` flag is not.
    """

    def __init__(self, policies: list[RegisteredPolicy] = ()) -> None:
        self._entries: dict[str, str] = {}
        for policy in policies:
            if policy.id in self._entries:
                raise ValueError("Duplicate policy id")
            self._entries[policy.id] = policy.model_dump_json()

    @classmethod
    def from_json(cls, raw: bytes | str) -> "PolicyRegistry":
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("Policy registry must be a list")
        return cls([RegisteredPolicy.model_validate(item) for item in data])

    def resolve(self, step: Step, business_scenario: str) -> RegisteredPolicy:
        raw = self._entries.get(step.policy_id or "")
        if raw is None:
            raise NeedsReview("POLICY_NOT_APPROVED")
        policy = RegisteredPolicy.model_validate_json(raw)
        if (policy.operation_id != step.operation_id or step.role not in policy.roles
                or policy.business_scenario != business_scenario
                or not policy.assertions.reviewed or not policy.assertions.source.strip()
                or not (policy.assertions.status or policy.assertions.fields)):
            raise NeedsReview("POLICY_SCOPE_MISMATCH")
        for field, expected in policy.required_bindings.items():
            if step.model_dump().get(field) != expected:
                raise NeedsReview("POLICY_BINDING_MISMATCH")
        if step.poll and not policy.poll:
            raise NeedsReview("POLL_POLICY_NOT_APPROVED")
        if step.poll and not policy.assertions.status:
            raise NeedsReview("POLL_HTTP_CONTRACT_NOT_APPROVED")
        return policy

    def catalog(self, business_scenario: str) -> list[dict]:
        """Public references only; never expose mutable approval objects to planners."""
        entries = [RegisteredPolicy.model_validate_json(raw) for raw in self._entries.values()]
        return [{"id": item.id, "operation_id": item.operation_id, "roles": item.roles}
                for item in entries if item.business_scenario == business_scenario
                and item.assertions.reviewed]

    def snapshot(self) -> "PolicyRegistry":
        return PolicyRegistry([RegisteredPolicy.model_validate_json(raw)
                               for raw in self._entries.values()])
