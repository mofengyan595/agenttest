"""Bound reference syntax and declared producer/selector choices before Scenario creation."""

from typing import TYPE_CHECKING

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.policies import NeedsReview
from agenttest.selectors import SELECTOR, validate_selector

if TYPE_CHECKING:
    from agenttest.llm_planner import GenericPlannerDraft


def reference_schema(context: dict) -> dict | None:
    """Closed catalogs expose a finite union; open slots retain supported JSONPath syntax."""
    policies = {p["id"]: p for p in context["policies"]}
    closed = all(p.get("closed_arguments") and "binding_rules" in p for p in policies.values())
    selectors = set()
    for policy in policies.values():
        for rule in policy.get("binding_rules", []):
            if rule.get("literal_schema") is not None:
                continue
            producer = rule.get("source_policy_id")
            if producer is None:
                closed = False
            elif producer in policies:
                selectors.add(validate_selector(rule["source_selector"]))
    if closed:
        return {"type": "string", "enum": sorted(selectors)} if selectors else None
    return {"type": "string", "maxLength": 200, "pattern": "^" + SELECTOR.pattern + "$"}


def validate_generic_references(draft: "GenericPlannerDraft", context: dict) -> None:
    """Check each declared binding against an earlier producer; never repair a reference."""
    policies = {p["id"]: p for p in context["policies"]}
    previous = {}
    for step in draft.steps:
        for argument in step.arguments:
            if argument.from_step is None:
                continue
            try:
                validate_selector(argument.select)
            except (TypeError, ValueError):
                raise ValidationFailure(Layer.MODEL, "MODEL_SCHEMA_GENERIC_REFERENCE") from None
            producer = previous.get(argument.from_step)
            policy = policies.get(step.policy_id)
            if producer is None:
                raise ValidationFailure(Layer.MODEL, "MODEL_SCHEMA_GENERIC_REFERENCE")
            if policy is None:
                raise NeedsReview("POLICY_NOT_APPROVED")
            if policy.get("operation_id", step.operation_id) != step.operation_id:
                raise NeedsReview("POLICY_SCOPE_MISMATCH")
            else:
                rules = [r for r in policy.get("binding_rules", [])
                         if (r["location"], r["name"]) == (argument.location, argument.name)]
                if not rules:
                    if policy.get("closed_arguments", False):
                        raise NeedsReview("POLICY_ARGUMENT_MISMATCH")
                else:
                    rule = rules[0]
                    source_policy = policies.get(rule.get("source_policy_id"))
                    if rule.get("literal_schema") is not None:
                        raise NeedsReview("POLICY_LITERAL_REQUIRED")
                    elif rule.get("source_policy_id") is not None:
                        valid = bool(source_policy
                            and producer.policy_id == rule["source_policy_id"]
                            and producer.operation_id == source_policy["operation_id"]
                            and argument.select == rule["source_selector"])
                        if not valid:
                            raise NeedsReview("POLICY_DEPENDENCY_MISMATCH")
        previous[step.id] = step
