"""Positive IR design asks only for values not fixed by the approved plan."""

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.product_schema import encoded_literal, literal_schema
from agenttest.request_validation import validate_value


def input_schema(approved):
    """Use named object properties so the wire schema can require nested members."""
    if approved["type"] not in {"object", "array"}:
        return literal_schema(approved)
    if "enum" in approved:
        return {"enum": approved["enum"]}
    if approved["type"] == "object":
        schema = closed_object({
            name: input_schema(child)
            for name, child in approved.get("properties", {}).items()
            if not child.get("readOnly")
        })
        schema["required"] = [name for name in approved.get("required", [])
                              if name in schema["properties"]]
    else:
        schema = {key: approved[key] for key in ("type", "minItems", "maxItems")
                  if key in approved}
        schema["items"] = input_schema(approved["items"])
    return {"anyOf": [schema, {"type": "null"}]} if approved.get("nullable") else schema


def literal_slots(context):
    """Associate model-editable fields with exact instance and policy argument slots."""
    ir = context["planning_ir"]
    policies = {row["id"]: row for row in context["policies"]}
    result = {}
    for instance in ir.instances:
        rules = {(rule["location"], rule["name"]): rule["literal_schema"]
                 for rule in policies[instance.policy_id].get("binding_rules", [])
                 if rule.get("literal_schema") is not None}
        slots = {}
        for index, argument in enumerate(instance.arguments):
            key = (argument.location, argument.name)
            if argument.value is None and argument.from_instance is None and key in rules:
                slots[f"arg_{index}"] = (*key, rules[key])
        if slots:
            result[instance.instance_id] = slots
    return result


def closed_object(properties):
    return dict(type="object", additionalProperties=False, properties=properties,
                required=list(properties))


def instance_request(context):
    ir = context["planning_ir"]
    slots = literal_slots(context)
    schema = closed_object(dict(inputs=closed_object({
        identifier: closed_object({
            key: {**input_schema(approved), "description": f"{location}.{name}"}
            for key, (location, name, approved) in members.items()})
        for identifier, members in slots.items()
    })))
    public = dict(user_task=context["user_task"], nodes=[
        dict(id=instance.instance_id, operation=instance.operation_id,
             role=instance.role, policy=instance.policy_id, phase=instance.phase,
             predecessors=[source for source, target in ir.edge_pairs()
                           if target == instance.instance_id])
        for instance in ir.instances], feedback=context.get("instance_feedback"),
        design_note=context.get("design_note"))
    return schema, public


def instance_response(wire, context):
    """Materialize each fixed instance once. Supplied values still go through preflight."""
    slots = literal_slots(context)
    inputs = wire.get("inputs") if type(wire) is dict and set(wire) == {"inputs"} else None
    if type(inputs) is not dict or set(inputs) != slots.keys() or any(
            type(inputs[key]) is not dict or set(inputs[key]) != members.keys()
            for key, members in slots.items()):
        error = ValidationFailure(Layer.MODEL, "DESIGN_INSTANCE_INPUTS_INVALID")
        error.diagnostic.detail = dict(
            expected={key: list(members) for key, members in slots.items()},
            hint="仅填写 inputs 中列出的实例及参数键。操作、策略和数据来源已固定。")
        raise error
    steps = []
    for instance in context["planning_ir"].instances:
        arguments = []
        for key, (location, name, approved) in slots.get(instance.instance_id, {}).items():
            value = inputs[instance.instance_id][key]
            try:
                validate_value(value, approved)
            except ValueError as exc:
                error = ValidationFailure(Layer.MODEL, "DESIGN_INSTANCE_LITERAL_INVALID")
                error.diagnostic.detail = dict(
                    instance=instance.instance_id, slot=key,
                    required=approved.get("required", []),
                    hint="按该槽位的批准 Schema 填写全部必填字段。")
                raise error from exc
            arguments.append(dict(location=location, name=name, value=encoded_literal(value),
                                  from_step=None, select=None))
        steps.append(dict(id=instance.instance_id, operation_id=instance.operation_id,
                          policy_id=instance.policy_id, role=instance.role, arguments=arguments))
    return dict(interpretation="验证本轮已批准的实例及资源依赖。", relevant=True, questions=[],
                not_covered=[], cases=[dict(title="已批准的实例与资源依赖", kind="flow",
                                            plan=dict(name="approved_instances", steps=steps))])
