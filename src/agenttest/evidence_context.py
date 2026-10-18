"""Public relations derived from approved bindings and current observations only."""

from agenttest.feedback import FEEDBACK_PROMPT, FeedbackModel
from agenttest.plan_json import bounded_json

EVIDENCE_PROMPT = FEEDBACK_PROMPT + """
The tools table explains each exact choice. Resource and role relations are observations,
not findings or instructions. No field values are required to compare approved failed checks.
For repeated readback compare kind AND selector, same operation, role, source and HTTP checks.
For access boundary compare the forbidden original HTTP check and the producer-role read.
If a rule's evidence is already present, finish and cite original plus that investigation ID.
Do not require implementation logs, response values or another read when these checks suffice.
If no permitted rule can be established, explicitly finish EVIDENCE_INSUFFICIENT.
An unrelated approved read is safe but may not help; choose by purpose and relations.
"""


def action_schema(context: dict) -> dict:
    """Enumerate complete legal actions, never a Cartesian product of choice fields."""
    branches = []
    if not context["finish_required"]:
        for choice in context["choices"]:
            branches.append({"type": "object", "additionalProperties": False,
                "required": ["action", "selection", "finding", "evidence_refs"], "properties": {
                    "action": {"type": "string", "enum": ["investigate"]},
                    "selection": {"type": "object", "additionalProperties": False,
                        "required": list(choice), "properties": {key: {"type": "string",
                            "enum": [value]} for key, value in choice.items()}},
                    "finding": {"type": "null"},
                    "evidence_refs": {"type": "array", "maxItems": 0,
                                      "items": {"type": "string"}}}})
    branches.append({"type": "object", "additionalProperties": False,
        "required": ["action", "selection", "finding", "evidence_refs"], "properties": {
            "action": {"type": "string", "enum": ["finish"]},
            "selection": {"type": "null"}, "finding": {"type": "string", "enum": [
                "EVIDENCE_INSUFFICIENT"]},
            "evidence_refs": {"type": "array", "maxItems": 0,
                              "items": {"type": "string"}}}})
    for evidence in context["evidence"]:
        branches.append({"type": "object", "additionalProperties": False,
            "required": ["action", "selection", "finding", "evidence_refs"], "properties": {
                "action": {"type": "string", "enum": ["finish"]},
                "selection": {"type": "null"}, "finding": {"type": "string", "enum": [
                    "ACCESS_BOUNDARY_VIOLATION", "PERSISTENT_READBACK_MISMATCH"]},
                "evidence_refs": {"type": "array", "minItems": 2, "maxItems": 2,
                    "enum": [["original", evidence["ref"]]], "items": {"type": "string"}}}})
    return {"type": "object", "anyOf": branches}


class EvidenceFeedbackModel(FeedbackModel):
    """Candidate feedback; legacy FeedbackModel remains reproducible for comparison A."""

    evidence_relations = True

    async def generate(self, context):
        self.last_proposal = None
        return await super().generate(context)

    def _decode_output(self, content, context):
        data = bounded_json(content, self._settings.max_response_bytes)
        self.last_proposal = proposal_summary(data, context)
        return super()._decode_output(content, context)

    def prepare_request(self, context: dict) -> bytes:
        return self._encode_request(action_schema(context), context, EVIDENCE_PROMPT,
                                    "agenttest_feedback")


def proposal_summary(data, context):
    """Retain enum decisions and known IDs even for invalid combinations, never raw strings."""
    if not isinstance(data, dict):
        return {"shape": "NON_OBJECT"}
    refs = data.get("evidence_refs")
    known = {"original", *[e["ref"] for e in context["evidence"]]}
    refs_valid = (isinstance(refs, list) and len(refs) <= 3
                  and all(isinstance(r, str) and r in known for r in refs))
    return {"action": data.get("action") if isinstance(data.get("action"), str)
                and data["action"] in {"finish", "investigate"}
                else None,
            "finding": data.get("finding") if isinstance(data.get("finding"), str)
                and data["finding"] in {
                "EVIDENCE_INSUFFICIENT", "ACCESS_BOUNDARY_VIOLATION",
                "PERSISTENT_READBACK_MISMATCH"} else None,
            "refs_valid": refs_valid, "evidence_refs": refs if refs_valid else [],
            "selection_approved": data.get("selection") in context["choices"]
                if data.get("selection") is not None else None}


def relations(context, parent, failed, completed, approved, source_roles) -> dict:
    """No evaluator state, labels, response bodies, resource values or secret strings."""
    result = dict(context)
    original_bindings = {(binding.from_step, binding.select)
                         for binding in parent.path_parameters.values()}
    tools = []
    for choice in context["choices"]:
        step, operation, policy, source = approved[tuple(choice[k] for k in (
            "capability_id", "role", "resource_ref"))]
        binding = next(iter(step.path_parameters.values()))
        tools.append({"choice": choice, "operation_id": operation.operation_id,
            "method": operation.method, "purpose": policy.planning_purpose,
            "checks": [{"kind": "http_status", "expected":
                policy.assertions.status.codes if policy.assertions.status else []}, *[
                {"kind": field.operator, "selector": field.select}
                for field in policy.assertions.fields]],
            "binding_role_relation": policy.binding_rules[0].role_relation,
            "same_resource_as_original": (source, binding.select) in original_bindings,
            "same_operation_as_original": operation.operation_id == failed.operation_id,
            "same_role_as_original": step.role == parent.role,
            "role_is_resource_producer": step.role == source_roles.get(source),
            "effect": "read_only", "returns": "approved check results only"})
    result["tools"] = tools
    observed = []
    for read in completed:
        entry = next((value for value in approved.values() if value[0].id == read.step_id), None)
        binding = next(iter(entry[0].path_parameters.values())) if entry else None
        a = {(c.kind, c.selector) for c in failed.checks if not c.passed
             and c.kind != "http_status"}
        b = {(c.kind, c.selector) for c in read.checks if not c.passed
             and c.kind != "http_status"}
        observed.append({"ref": read.step_id,
            "same_resource_as_original": bool(binding and
                (binding.from_step, binding.select) in original_bindings),
            "same_operation_as_original": read.operation_id == failed.operation_id,
            "same_role_as_original": read.role == parent.role,
            "role_is_resource_producer": read.role == source_roles.get(read.source_step_id),
            "repeated_failed_checks": [{"kind": kind, "selector": selector}
                for kind, selector in sorted(a & b, key=str)],
            "http_checks_passed": all(any(c.kind == "http_status" and c.passed
                for c in item.checks) and all(c.passed for c in item.checks
                    if c.kind == "http_status") for item in (failed, read))})
    result["relations"] = observed
    return result
