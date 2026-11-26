"""Compile one witnessed missing-field check using an already validated Focus instance."""

from agenttest.credentials import CredentialVault
from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.llm_planner import literal_data
from agenttest.negative import validate_approval
from agenttest.plan_construction import construct_plan
from agenttest.product_design import TestDesign, design_context
from agenttest.product_inputs import InputRequirement, validate_design_inputs
from agenttest.product_schema import encoded_literal
from agenttest.project_inventory import identity_ready
from agenttest.report import fingerprint
from agenttest.request_validation import validate_value

SWITCH = "AGENTTEST_C2_REQUIRED_MISSING_PROJECT"
PROTOCOL = "required_missing_observed_post_v1"
SOURCE = "DETERMINISTIC_REQUIRED_MISSING"


def compile_missing_check(scope, group, checks, supervision):
    """Return an applicable design or the reason to retain the original design path."""
    project, ir = scope.project, scope.planning_ir
    if (len(checks) != 1 or tuple(group.get("policy_ids") or ()) != (checks[0]["policy_id"],)
            or checks[0] not in scope.executable_checks):
        return None, "NO_SINGLE_REQUESTED_CHECK"
    check = checks[0]
    if (check.get("exact_value") is not None or check.get("exact_length") is not None
            or any(supervision.get(key) for key in
                   ("design_note", "reference_repair", "negative_repair"))):
        return None, "EXPLICIT_OR_REPAIR_CONSTRAINT"
    if not ir or ir.ambiguities:
        return None, "PLANNING_IR_UNRESOLVED"
    if any(scope.summary.get(key) for key in (
            "questions", "pending_input_questions", "permission_requirements",
            "http_status_requirements", "response_requirements", "data_references",
            "source_blocked_operations", "pending_negative_values")):
        return None, "TASK_CONSTRAINTS_REQUIRE_DESIGN"
    focus = scope.summary.get("focus", {})
    if focus.get("source_conflicts"):
        return None, "TASK_CONSTRAINTS_REQUIRE_DESIGN"

    policies = {item.id: item for item in project.registry.definitions()}
    policy = policies.get(check["policy_id"])
    if policy is None or policy.negative is None:
        return None, "NO_APPROVED_NEGATIVE"
    approval = policy.negative
    operation = next((op for op in project.contract.operations
                      if op.operation_id == policy.operation_id), None)
    observer = project.target.negative_post_observations.get(policy.operation_id)
    if (approval.kind != "required_missing" or approval.location != "body"
            or operation is None or operation.method != "POST" or observer is None
            or tuple(group["primary"]) != (operation.operation_id,)
            or not {operation.operation_id, observer.operation_id}
                   <= set(project.target.allowed_operations)
            or not project.target.allow_mutations or not policy.closed_arguments
            or policy.prerequisite_policy_ids or policy.required_bindings
            or policy.csv_fixture or policy.poll
            or any(rule.location == "path_parameters" or rule.source_policy_id is not None
                   or rule.literal_schema is None for rule in policy.binding_rules)):
        return None, "UNSUPPORTED_APPROVED_SHAPE"
    hitl = scope.durability.hitl if scope.durability is not None else None
    if scope.tool is None or hitl is None or (hitl and operation.operation_id not in hitl):
        return None, "DURABLE_APPROVAL_NOT_CONFIGURED"

    instances = [item for item in ir.instances if item.operation_id == operation.operation_id]
    if len(instances) != 1 or instances[0].policy_id != approval.base_policy_id:
        return None, "BASELINE_INSTANCE_NOT_UNIQUE"
    instance = instances[0]
    if (any(edge.target_instance == instance.instance_id for edge in ir.edges)
            or any(arg.from_instance for arg in instance.arguments)):
        return None, "BASELINE_DEPENDS_ON_RESOURCE"
    if instance.assertions or any(
            instance.instance_id in rule.instances for rule in ir.constraints):
        return None, "TASK_CONSTRAINTS_REQUIRE_DESIGN"
    if instance.role not in policy.roles:
        raise ValidationFailure(Layer.POLICY, "POLICY_SCOPE_MISMATCH")
    if instance.role is not None:
        identity = project.target.roles.get(instance.role)
        if identity is None or not identity_ready(identity, scope.svc.environ):
            return None, "IDENTITY_NOT_READY"
        if identity.login_operation:
            return None, "MANAGED_LOGIN_REQUIRES_DESIGN"

    # The only server-filled value is the approved correlation header. The normal preflight
    # replaces it with its existing per-execution observation key before dispatch.
    arguments = {(arg.location, arg.name): arg.value for arg in instance.arguments}
    source_inputs = [InputRequirement.model_validate(row)
                     for row in focus.get("input_requirements", [])
                     if row["operation_id"] == operation.operation_id]
    supplied = {(item.location, item.name): item for item in source_inputs}
    key_slot = ("headers", observer.request_header)
    if (key_slot in supplied or len(supplied) != len(source_inputs)
            or any(item.path for item in source_inputs)):
        return None, "BASELINE_INPUTS_REQUIRE_DESIGN"
    rules = {(item.location, item.name): item for item in policy.binding_rules}
    if (key_slot not in rules or set(arguments) - rules.keys() or supplied.keys() - rules.keys()
            or len(arguments) != len(instance.arguments)):
        return None, "BASELINE_INPUT_BINDINGS_MISMATCH"

    values = []
    for (location, name), rule in rules.items():
        slot = location, name
        literal = arguments.get(slot)
        choices = rule.literal_schema.get("enum", [])
        if slot == key_slot:
            if literal is not None:
                return None, "BASELINE_INPUTS_REQUIRE_DESIGN"
            value = fingerprint((scope.task_id, group["id"], policy.id))[:32]
        elif slot in supplied:
            item = supplied[slot]
            if slot not in arguments or not json_equal(literal, item.model_dump()["value"]):
                return None, "BASELINE_INPUT_BINDINGS_MISMATCH"
            value = literal_data(item.value)
        elif len(choices) == 1 and literal is not None and json_equal(literal, choices[0]):
            value = choices[0]
        else:
            return None, "OPEN_BASELINE_INPUT_SLOT"
        try:
            validate_value(value, rule.literal_schema)
        except ValueError:
            return None, "BASELINE_LITERAL_NOT_VALID"
        values.append(dict(location=location, name=name, value=encoded_literal(value),
                           from_step=None, select=None))

    vault = CredentialVault(project.target, scope.svc.environ)
    try:
        if vault.contains_secret(values):
            raise ValidationFailure(Layer.POLICY, "DESIGN_CONTEXT_CONTAINS_CREDENTIAL")
    finally:
        vault.close()
    validate_approval(approval, operation)
    draft = dict(name="requested_check", steps=[dict(id="negative",
        operation_id=operation.operation_id, role=instance.role, policy_id=policy.id,
        arguments=values)])
    plan, _ = construct_plan(draft, design_context(
        project, scope.text, group, group["minimum_requests"], scope.summary["inventory"]["total"]))
    design = TestDesign.model_validate(dict(
        interpretation="使用已确认的基线验证批准的必填字段检查。", relevant=True,
        cases=[dict(title="已批准的必填字段检查", kind="negative", plan=plan)],
        questions=[], not_covered=[]))
    validate_design_inputs(project, design, source_inputs, source_requirements=scope.requirements)
    return design, "COMPILED_FROM_APPROVED_BASELINE"
