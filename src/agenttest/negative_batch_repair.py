"""A narrowly witnessed repair for an undispatched required-body omission check."""

import re

from agenttest.diagnostics import Layer
from agenttest.model_input import diagnostic_view, safe_path
from agenttest.report import fingerprint
from agenttest.request_validation import request_schemas

CODE = "OPENAPI_UNKNOWN_BODY_FIELD"


def witness(project, case, error, ready_cases, validate, call_id):
    """Prove a legal baseline using a sibling's valid draft, without returning a plan to run."""
    diagnostic = getattr(error, "diagnostic", None)
    if (not call_id or not diagnostic or diagnostic.code != CODE
            or diagnostic.layer != Layer.OPENAPI or len(case.plan.steps) != 1):
        return None
    step, = case.plan.steps
    policies = {policy.id: policy for policy in project.registry.definitions()}
    policy = policies.get(step.policy_id)
    approval = policy.negative if policy else None
    detail = diagnostic.detail
    if (approval is None or approval.location != "body" or approval.kind != "required_missing"
            or step.operation_id != policy.operation_id or step.role not in policy.roles
            or detail.get("obligation_id") != policy.id or detail.get("step_id") != step.id
            or detail.get("step_index") != 0 or safe_path(step.id) != step.id):
        return None
    operation = next(op for op in project.contract.operations
                     if op.operation_id == policy.operation_id)
    _, body = request_schemas(operation, "application/json")
    required = sorted((body or {}).get("required", []))
    allowed = detail.get("allowed_body_fields") or []
    if approval.name not in required or not set(required) <= set(allowed):
        return None
    # Raw model-chosen names can encode a request value. Only the known DSL slot `value`
    # can be reported by name; other untrusted names are located by index and field path.
    paths = []
    unknown = []
    for path in diagnostic.fields:
        match = re.fullmatch(r"plan\.steps\.0\.arguments\.(\d{1,2})\.name", path)
        index = int(match[1]) if match else -1
        if not 0 <= index < len(step.arguments):
            return None
        argument = step.arguments[index]
        if argument.location != "body" or argument.name in body["properties"]:
            return None
        paths.append(path)
        unknown.append(dict(path=path, name="value" if argument.name == "value" else None))
    if not paths or len(paths) != len(detail.get("unknown_body_positions") or ()):
        return None
    for sibling in ready_cases:
        if len(sibling.plan.steps) != 1:
            continue
        source, = sibling.plan.steps
        source_policy = policies.get(source.policy_id)
        if (not source_policy or not source_policy.negative or source_policy.id == policy.id
                or source_policy.negative.base_policy_id != approval.base_policy_id
                or (source.operation_id, source.role) != (step.operation_id, step.role)):
            continue
        draft = source.model_copy(update={"policy_id": policy.id})
        candidate = sibling.model_copy(update={"plan": sibling.plan.model_copy(
            update={"steps": [draft]})})
        try:
            validate(candidate)
        except ValueError:
            continue
        projected = diagnostic_view(diagnostic.model_dump(mode="json"))
        return dict(call_id=call_id, sibling_policy_id=source_policy.id,
                    policy_fingerprint=fingerprint(policy.model_dump(mode="json")),
                    feedback=dict(code=CODE, fields=projected["fields"], step_id=step.id,
                        operation_id=policy.operation_id, policy_id=policy.id,
                        obligation_id=policy.id, role=step.role, unknown_body_fields=unknown,
                        allowed_body_fields=sorted(allowed), required_baseline_fields=required,
                        mutation=dict(kind="required_missing", location="body", name=approval.name),
                        hint="只重设计此批准策略，修正多余或错放的字段名。"
                             "提交符合 Schema 的正常基线。执行器会按原策略从实际请求移除指定字段。"
                             "保持原身份及已批准断言。"
                             "不得在发出的负向请求中保留缺失目标，也不要添加前后步骤。"))
    return None


def permits_case(design, repair):
    """Keep the repaired draft to the one independently witnessed case and identity."""
    if len(design.cases) != 1 or len(design.cases[0].plan.steps) != 1:
        return False
    step, = design.cases[0].plan.steps
    return (step.operation_id, step.policy_id, step.role) == (
        repair["operation_id"], repair["policy_id"], repair["role"])
