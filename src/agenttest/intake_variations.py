"""Operator-enabled, document-based negative capabilities; executor owns each mutation."""

from agenttest.diagnostics import ValidationFailure
from agenttest.negative import ApprovedNegative, validate_approval
from agenttest.request_validation import validate_value


def deviations(value, schema):
    basic = {k: schema[k] for k in ("type", "nullable", "properties", "items", "required",
                                  "additionalProperties") if k in schema}
    result = set()
    for family in [(), ("enum",), ("minLength", "maxLength", "minItems", "maxItems"),
                   ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"), ("format",)]:
        try:
            validate_value(value, dict(basic, **{k: schema[k] for k in family if k in schema}))
        except ValidationFailure as exc:
            result.add(exc.diagnostic.code)
    return sorted(result)


def negative_capabilities(base, op, specs, arguments):
    """Finite single-field cases only; paths, auth and resource references never mutate."""
    result = []
    by_slot = {(a.location, a.name): a for a in arguments}
    for spec in specs.values():
        argument = by_slot.get((spec.location, spec.name))
        if (spec.location == "path_parameters" or not argument or not argument.generated
                or argument.source_operation):
            continue
        schema = spec.schema
        candidates = []
        if spec.required:
            candidates.append(("required_missing", None))
        candidates.append(("type_mismatch", "wrong-type" if schema["type"] != "string" else 7))
        if schema.get("enum"):
            outside = ("__outside_enum__" if schema["type"] == "string" else
                       987654321 if schema["type"] in {"integer", "number"} else None)
            if outside is not None:
                candidates.append(("enum_outside", outside))
        for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
            if key not in schema:
                continue
            edge = schema[key]
            if key in {"minimum", "maximum"}:
                value = edge - 1 if key == "minimum" else edge + 1
            elif key.endswith("Length") and edge <= 256:
                value = "a" * (edge + 1 if key == "maxLength" else max(0, edge - 1))
            elif key.endswith("Items") and edge <= 8:
                from agenttest.project_inventory import sample

                value = [] if key == "minItems" else [sample(schema["items"])] * (edge + 1)
            else:
                continue
            candidates.append(("boundary_outside", value))
        for kind, value in candidates:
            codes = (["OPENAPI_BODY_FIELD_REQUIRED" if spec.location == "body" else
                      "OPENAPI_PARAMETER_REQUIRED"] if kind == "required_missing"
                     else deviations(value, schema))
            if not codes:
                continue
            approval = ApprovedNegative(base_policy_id=base.id, location=spec.location,
                name=spec.name, kind=kind, value=value, violation_codes=codes)
            try:
                validate_approval(approval, op)
            except ValueError:
                continue
            result.append(approval)
    return result
