"""Operator-owned approvals, outside the model's input/output authority."""

import json
from typing import Literal

from pydantic import Field

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.dsl import AssertionPolicy, FieldAssertion, PollPolicy, Step
from agenttest.safety import StrictModel


class NeedsReview(ValidationFailure):
    """No trusted basis exists to execute or classify this expectation."""

    def __init__(self, code: str):
        layer = (Layer.INTENT if code.startswith("INTENT_") else
                 Layer.OPENAPI if code.startswith(("UNSUPPORTED_", "OPEN_OBJECT_", "ONLY_OBJECT_"))
                 else Layer.DEPENDENCY if "DEPENDENCY" in code
                 else Layer.POLICY)
        super().__init__(layer, code)


class BindingRule(StrictModel):
    location: Literal["body", "path_parameters", "query"]
    name: str
    literal_schema: dict | None = None
    source_policy_id: str | None = None
    source_selector: str = "$"
    role_relation: Literal["same", "different"] | None = None
    # Approved equality relations, never model-authored expected values.
    response_selector: str | None = None
    source_body_equalities: dict[str, str] = Field(default_factory=dict)


class RegisteredPolicy(StrictModel):
    id: str
    operation_id: str
    roles: list[str | None]
    business_scenario: str
    assertions: AssertionPolicy
    poll: PollPolicy | None = None
    # Exact binding prerequisites make data-dependent approvals auditable.
    required_bindings: dict = Field(default_factory=dict)
    binding_rules: list[BindingRule] = Field(default_factory=list)
    closed_arguments: bool = False


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

    def resolve(self, step: Step, business_scenario: str,
                previous: dict[str, Step] | None = None) -> RegisteredPolicy:
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
        if policy.closed_arguments:
            for location in ("body", "path_parameters", "query"):
                names = {r.name for r in policy.binding_rules if r.location == location}
                if set(getattr(step, location)) != names or step.upload is not None:
                    raise NeedsReview("POLICY_ARGUMENT_MISMATCH")
        for rule in policy.binding_rules:
            binding = getattr(step, rule.location).get(rule.name)
            if binding is None:
                raise NeedsReview("POLICY_BINDING_REQUIRED")
            if rule.literal_schema is not None:
                from agenttest.request_validation import validate_value

                if binding.from_step is not None or rule.source_policy_id is not None:
                    raise NeedsReview("POLICY_LITERAL_REQUIRED")
                validate_value(binding.value, rule.literal_schema)
            elif rule.source_policy_id is not None:
                source = (previous or {}).get(binding.from_step)
                if (source is None or source.policy_id != rule.source_policy_id
                        or binding.select != rule.source_selector
                        or source.role is None or step.role is None
                        or (rule.role_relation == "same" and source.role != step.role)
                        or (rule.role_relation == "different" and source.role == step.role)):
                    raise NeedsReview("POLICY_DEPENDENCY_MISMATCH")
                for selector, field in rule.source_body_equalities.items():
                    original = source.body.get(field)
                    if original is None or original.from_step is not None:
                        raise NeedsReview("POLICY_SOURCE_LITERAL_REQUIRED")
                    policy.assertions.fields.append(FieldAssertion(
                        select=selector, expected=original.value))
            else:
                raise NeedsReview("POLICY_PARAMETER_NOT_DEFINED")
        if step.poll and not policy.poll:
            raise NeedsReview("POLL_POLICY_NOT_APPROVED")
        if step.poll and not policy.assertions.status:
            raise NeedsReview("POLL_HTTP_CONTRACT_NOT_APPROVED")
        return policy

    @staticmethod
    def request_assertions(policy: RegisteredPolicy, resolved: dict) -> AssertionPolicy:
        """Instantiate approved equality relations using already validated request data."""
        assertions = policy.assertions.model_copy(deep=True)
        for rule in policy.binding_rules:
            if rule.response_selector:
                assertions.fields.append(FieldAssertion(
                    select=rule.response_selector, expected=resolved[rule.location][rule.name]))
        return assertions

    def catalog(self, business_scenario: str) -> list[dict]:
        """Public references only; never expose mutable approval objects to planners."""
        entries = [RegisteredPolicy.model_validate_json(raw) for raw in self._entries.values()]
        return [{"id": item.id, "operation_id": item.operation_id, "roles": item.roles,
                 "required_bindings": item.required_bindings,
                 "binding_rules": [r.model_dump() for r in item.binding_rules],
                 "closed_arguments": item.closed_arguments,
                 "poll_allowed": item.poll is not None}
                for item in entries if item.business_scenario == business_scenario
                and item.assertions.reviewed]

    def snapshot(self) -> "PolicyRegistry":
        return PolicyRegistry([RegisteredPolicy.model_validate_json(raw)
                               for raw in self._entries.values()])

    def definitions(self) -> list[RegisteredPolicy]:
        """Independent copies for offline auditing; visibility grants no approval."""
        return [RegisteredPolicy.model_validate_json(self._entries[key])
                for key in sorted(self._entries)]

    def digest(self) -> str:
        from agenttest.report import fingerprint

        return fingerprint(self._entries)
