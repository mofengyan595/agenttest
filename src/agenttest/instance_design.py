"""Positive IR design asks only for values not fixed by the approved plan."""

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.product_schema import encoded_literal, literal_schema
from agenttest.request_validation import validate_value

JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean",
              dict: "object", list: "array", type(None): "null"}


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


def violation_facts(value, approved):
    """Name the approved-schema rule a literal breaks, without echoing the literal itself.

    Diagnosis only: the accept/reject decision stays ``validate_value``'s. The checks follow the
    same order as that validator, so the reported rule is the one that actually failed. Only
    lengths, counts, type names and schema limits are reported -- never the supplied value, which
    may be an operator literal or a credential the model should not see repeated.
    """
    if "enum" in approved and not any(json_equal(value, item) for item in approved["enum"]):
        return dict(violation="ENUM", constraint="enum",
                    allowed_count=len(approved["enum"]))
    kind = approved.get("type")
    if JSON_TYPES.get(type(value)) != kind:
        return dict(violation="TYPE", constraint="type", expected_type=kind,
                    actual_type=JSON_TYPES.get(type(value), "unknown"))
    if kind == "object":
        required = [str(name) for name in approved.get("required", [])]
        missing = [name for name in required if name not in value]
        unknown = [name for name in value if name not in approved.get("properties", {})]
        if missing or unknown:
            return dict(violation="OBJECT_FIELDS",
                        constraint="required" if missing else "properties",
                        missing=missing[:8], unknown_count=len(unknown),
                        required=required[:8])
    if kind in {"array", "string"}:
        unit = "Items" if kind == "array" else "Length"
        low, high = approved.get("min" + unit), approved.get("max" + unit)
        if type(low) is int and len(value) < low:
            return dict(violation="MIN_" + unit.upper(), constraint="min" + unit,
                        limit=low, actual_length=len(value))
        if type(high) is int and len(value) > high:
            facts = dict(violation="MAX_" + unit.upper(), constraint="max" + unit,
                         limit=high, actual_length=len(value))
            if kind == "string":
                facts["max_length"] = high
            return facts
    if kind == "string" and "pattern" in approved:
        return dict(violation="PATTERN", constraint="pattern")
    if kind in {"number", "integer"}:
        for bound, exclusive in (("minimum", "exclusiveMinimum"),
                                 ("maximum", "exclusiveMaximum")):
            limit = approved.get(bound)
            if type(limit) in {int, float} and (
                    (bound == "minimum" and value < limit)
                    or (bound == "maximum" and value > limit)):
                return dict(violation=bound.upper(), constraint=bound, limit=limit)
            if limit is not None and approved.get(exclusive) is True and value == limit:
                return dict(violation="EXCLUSIVE_" + bound.upper(),
                            constraint=exclusive, limit=limit)
            if type(approved.get(exclusive)) in {int, float} and (
                    (bound == "minimum" and value <= approved[exclusive])
                    or (bound == "maximum" and value >= approved[exclusive])):
                return dict(violation="EXCLUSIVE_" + bound.upper(),
                            constraint=exclusive, limit=approved[exclusive])
    return dict(violation="INVALID", constraint=kind)


def violation_hint(facts):
    """The concrete edit a rejected literal needs; never 'fill in the required fields'."""
    violation, constraint = facts.get("violation"), facts.get("constraint")
    if violation == "MAX_LENGTH":
        return (f"长度超限：批准 Schema 要求 maxLength={facts['limit']}，本次 "
                f"{facts['actual_length']} 个字符。请缩短到 {facts['limit']} 个字符以内。")
    if violation == "MIN_LENGTH":
        return (f"长度不足：批准 Schema 要求 minLength={facts['limit']}，本次 "
                f"{facts['actual_length']} 个字符。")
    if violation == "MAX_ITEMS":
        return (f"元素过多：批准 Schema 要求 maxItems={facts['limit']}，本次 "
                f"{facts['actual_length']} 项。")
    if violation == "MIN_ITEMS":
        return (f"元素过少：批准 Schema 要求 minItems={facts['limit']}，本次 "
                f"{facts['actual_length']} 项。")
    if violation == "TYPE":
        return (f"类型不符：批准 Schema 要求 {facts['expected_type']}，本次是 "
                f"{facts['actual_type']}。请按该类型填写。")
    if violation == "ENUM":
        return f"取值不在批准枚举内（批准 {facts['allowed_count']} 个取值），请改用批准取值。"
    if violation == "OBJECT_FIELDS" and facts.get("missing"):
        return ("缺少必填字段：" + "、".join(facts["missing"])
                + "。请补齐这些字段，不要新增未批准字段。")
    if violation == "OBJECT_FIELDS":
        return f"含 {facts.get('unknown_count', 0)} 个未批准字段，请只用批准 Schema 声明的字段。"
    if violation == "PATTERN":
        return "取值不符合批准的 pattern，请按该格式填写。"
    if violation.startswith("EXCLUSIVE_") or violation in {"MINIMUM", "MAXIMUM"}:
        return f"超出批准数值范围：{constraint}={facts['limit']}。"
    return "取值不符合该槽位的批准 Schema。"


def literal_failure_detail(instance, slot, location, name, approved, value, exc=None):
    """Structured facts for DESIGN_INSTANCE_LITERAL_INVALID (P3-B2.4 A2).

    A scalar constraint (length, bounds, enum, type) used to be reported as ``required: []``
    plus "fill in every required field", which never named the real rule.
    """
    facts = violation_facts(value, approved)
    detail = dict(instance=instance, slot=slot,
                  field_path=f"{location}.{name}" if name else slot)
    detail.update({key: item for key, item in facts.items() if item not in (None, [], ())})
    code = getattr(getattr(exc, "diagnostic", None), "code", None)
    if code:
        detail["schema_rule"] = code
    detail["hint"] = violation_hint(facts)
    return detail


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
                error.diagnostic.detail = literal_failure_detail(
                    instance.instance_id, key, location, name, approved, value, exc)
                error.diagnostic.fields = ([f"{location}.{name}"]
                                           if location and name else [])
                raise error from exc
            arguments.append(dict(location=location, name=name, value=encoded_literal(value),
                                  from_step=None, select=None))
        steps.append(dict(id=instance.instance_id, operation_id=instance.operation_id,
                          policy_id=instance.policy_id, role=instance.role, arguments=arguments))
    return dict(interpretation="验证本轮已批准的实例及资源依赖。", relevant=True, questions=[],
                not_covered=[], cases=[dict(title="已批准的实例与资源依赖", kind="flow",
                                            plan=dict(name="approved_instances", steps=steps))])
