"""Fixed public-evidence decision control, routed through the existing feedback executor."""

import json


def observed_conclusion(context):
    """Apply the two published symptom rules, without task/defect/evaluator access."""
    failure = context["failure"]
    denied_success = any(c["kind"] == "http_status" and not c["passed"] and
        c.get("expected") and all(code in {403, 404} for code in c["expected"])
        and type(c.get("actual")) is int and 200 <= c["actual"] < 300
        for c in failure["checks"])
    evidence = {e["ref"]: e for e in context["evidence"]}
    for relation in context["relations"]:
        read = evidence[relation["ref"]]
        if not (relation["same_resource_as_original"] and relation["same_operation_as_original"]):
            continue
        if (denied_success and relation["role_is_resource_producer"]
                and not relation["same_role_as_original"] and read["outcome"] == "PASS"):
            return "ACCESS_BOUNDARY_VIOLATION", ["original", read["ref"]]
        if (relation["same_role_as_original"] and relation["repeated_failed_checks"]
                and relation["http_checks_passed"] and read["outcome"] == "ASSERTION_FAILED"):
            return "PERSISTENT_READBACK_MISMATCH", ["original", read["ref"]]
    return None, []


def fixed_decision(context):
    finding, refs = observed_conclusion(context)
    if finding:
        return dict(action="finish", selection=None, finding=finding, evidence_refs=refs)
    failed = context["failure"]["checks"]
    access = any(c["kind"] == "http_status" and not c["passed"] and c.get("expected")
        and all(code in {403, 404} for code in c["expected"])
        and type(c.get("actual")) is int and 200 <= c["actual"] < 300 for c in failed)
    fields = any(c["kind"] != "http_status" and not c["passed"] for c in failed)
    if not context["finish_required"]:
        for tool in context["tools"]:
            if (tool["same_resource_as_original"] and tool["same_operation_as_original"] and
                (access and tool["role_is_resource_producer"] or
                 fields and tool["same_role_as_original"])):
                return dict(action="investigate", selection=tool["choice"],
                            finding=None, evidence_refs=[])
    return dict(action="finish", selection=None, finding="EVIDENCE_INSUFFICIENT", evidence_refs=[])


class FixedEvidenceControl:
    """No model, no separate HTTP engine, identical allowlist and per-run budget."""

    evidence_relations = True

    async def generate(self, context):
        return json.dumps(fixed_decision(context))
