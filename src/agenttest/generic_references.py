"""Bound reference syntax and declared producer/selector choices before Scenario creation."""

from typing import TYPE_CHECKING

from agenttest.diagnostics import Layer, ReferenceDiagnostic, ValidationFailure
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


def root_response_bindings(context: dict) -> list[dict]:
    """Explain declared whole-response bindings; never infer one from response examples."""
    policies = {p["id"]: p for p in context["policies"]}
    return [{"consumer_policy_id": policy["id"], "location": rule["location"],
             "name": rule["name"], "source_policy_id": source["id"],
             "source_operation_id": source["operation_id"], "select": "$"}
            for policy in policies.values() for rule in policy.get("binding_rules", [])
            if rule.get("literal_schema") is None and rule.get("source_selector") == "$"
            and (source := policies.get(rule.get("source_policy_id"))) is not None]


def validate_generic_references(draft: "GenericPlannerDraft", context: dict) -> None:
    """Check each declared binding against an earlier producer; never repair a reference."""
    policies = {p["id"]: p for p in context["policies"]}
    previous = {}
    step_ids = {step.id for step in draft.steps}
    for step_index, step in enumerate(draft.steps):
        for argument_index, argument in enumerate(step.arguments):
            if argument.from_step is None:
                continue

            def reject(code, branch, *, model=False):
                failure = (ValidationFailure(Layer.MODEL, code) if model else NeedsReview(code))
                failure.diagnostic.reference = ReferenceDiagnostic(
                    branch=branch, step_index=step_index, argument_index=argument_index)
                raise failure

            try:
                validate_selector(argument.select)
            except (TypeError, ValueError):
                reject("MODEL_SCHEMA_GENERIC_REFERENCE", "SELECTOR_SYNTAX", model=True)
            producer = previous.get(argument.from_step)
            policy = policies.get(step.policy_id)
            if producer is None:
                reject("MODEL_SCHEMA_GENERIC_REFERENCE", "SOURCE_NOT_PREVIOUS"
                       if argument.from_step in step_ids else "SOURCE_UNKNOWN", model=True)
            if policy is None:
                reject("POLICY_NOT_APPROVED", "CONSUMER_POLICY")
            if policy.get("operation_id", step.operation_id) != step.operation_id:
                reject("POLICY_SCOPE_MISMATCH", "CONSUMER_OPERATION")
            else:
                rules = [r for r in policy.get("binding_rules", [])
                         if (r["location"], r["name"]) == (argument.location, argument.name)]
                if not rules:
                    if policy.get("closed_arguments", False):
                        reject("POLICY_ARGUMENT_MISMATCH", "ARGUMENT_SLOT")
                else:
                    rule = rules[0]
                    source_policy = policies.get(rule.get("source_policy_id"))
                    if rule.get("literal_schema") is not None:
                        reject("POLICY_LITERAL_REQUIRED", "LITERAL_SLOT")
                    elif rule.get("source_policy_id") is not None:
                        if not source_policy or producer.policy_id != rule["source_policy_id"]:
                            reject("POLICY_DEPENDENCY_MISMATCH", "SOURCE_POLICY")
                        if producer.operation_id != source_policy["operation_id"]:
                            reject("POLICY_DEPENDENCY_MISMATCH", "SOURCE_OPERATION")
                        if argument.select != rule["source_selector"]:
                            reject("POLICY_DEPENDENCY_MISMATCH", "SELECTOR_POLICY")
        previous[step.id] = step
