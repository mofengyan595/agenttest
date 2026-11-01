"""Operator-approved, single-field mutations of fully validated normal requests."""

from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, model_serializer, model_validator

from agenttest.diagnostics import ValidationFailure
from agenttest.json_values import json_equal
from agenttest.safety import StrictModel


class ApprovedNegative(StrictModel):
    """One approved field and deviation set, optionally with checked literal variants."""

    base_policy_id: str
    location: Literal["body", "query"]
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["required_missing", "type_mismatch", "enum_outside", "boundary_outside"]
    value: Any = None
    violation_codes: list[str] = Field(min_length=1, max_length=5)
    allow_value_variants: bool = False

    @model_serializer(mode="wrap")
    def preserve_fixed_approval(self, handler):
        data = handler(self)
        if not self.allow_value_variants:
            data.pop("allow_value_variants", None)
        return data

    @model_validator(mode="after")
    def bounded(self):
        if self.kind != "required_missing" and "value" not in self.model_fields_set:
            raise ValueError("NEGATIVE_FIXED_VALUE_REQUIRED")
        if len(self.model_dump_json().encode()) > 2048:
            raise ValueError("NEGATIVE_APPROVAL_TOO_LARGE")
        if len(set(self.violation_codes)) != len(self.violation_codes):
            raise ValueError("NEGATIVE_DUPLICATE_VIOLATION")
        return self


def approval_schema(approval, operation):
    """Only ordinary JSON body/query data; paths, auth, files and resources stay intact."""
    from agenttest.policies import NeedsReview
    from agenttest.request_validation import request_schemas, supported_schema

    params, body = request_schemas(operation,
        "application/json" if operation.request_body else None)
    schema = ((body or {}).get("properties", {}).get(approval.name)
              if approval.location == "body" else params["query"].get(approval.name))
    if schema is None or schema.get("readOnly"):
        raise NeedsReview("NEGATIVE_FIELD_NOT_APPROVED")
    supported_schema(schema)
    return schema, (approval.name in (body or {}).get("required", [])
        if approval.location == "body" else any(p["name"] == approval.name
            and p["in"] == "query" and p.get("required") for p in operation.parameters))


def validate_approval(approval, operation):
    """Check the exact set of schema deviations, including type plus enum collisions."""
    from agenttest.policies import NeedsReview
    from agenttest.request_validation import validate_value

    schema, required = approval_schema(approval, operation)
    if approval.kind == "required_missing":
        codes = {"OPENAPI_BODY_FIELD_REQUIRED" if approval.location == "body"
                 else "OPENAPI_PARAMETER_REQUIRED"} if required else set()
    else:
        codes = set()
        # Validate each constraint family separately, retaining full type interpretation.
        basic = {k: schema[k] for k in ("type", "nullable", "properties", "items",
                                      "required", "additionalProperties") if k in schema}
        families = [(), ("enum",), ("minLength", "maxLength", "minItems", "maxItems"),
                    ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"), ("format",)]
        for family in families:
            try:
                validate_value(approval.value, dict(basic, **{
                    k: schema[k] for k in family if k in schema}))
            except ValidationFailure as exc:
                codes.add(exc.diagnostic.code)
        required_code = {"type_mismatch": {"OPENAPI_VALUE_TYPE"},
            "enum_outside": {"OPENAPI_VALUE_ENUM"},
            "boundary_outside": {"OPENAPI_VALUE_LENGTH", "OPENAPI_VALUE_BOUND",
                "OPENAPI_VALUE_EXCLUSIVE_BOUND", "OPENAPI_VALUE_FORMAT_BOUND"}}[approval.kind]
        if not codes & required_code:
            raise NeedsReview("NEGATIVE_MUTATION_DOES_NOT_MATCH_KIND")
    if not codes or codes != set(approval.violation_codes):
        raise NeedsReview("NEGATIVE_UNAPPROVED_SCHEMA_DEVIATION")
    return codes


def mutate_request(approval, operation, path, query, payload, media):
    """Validate everything first, then change exactly the approved non-resource field."""
    from agenttest.policies import NeedsReview
    from agenttest.request_validation import validate_request

    validate_request(operation, path, query, payload, media)
    validate_approval(approval, operation)
    values = deepcopy(payload if approval.location == "body" else query)
    if approval.name not in values:
        raise NeedsReview("NEGATIVE_BASE_FIELD_REQUIRED")
    if approval.kind == "required_missing":
        del values[approval.name]
    else:
        if json_equal(values[approval.name], approval.value):
            raise NeedsReview("NEGATIVE_BASE_MUST_BE_VALID")
        values[approval.name] = deepcopy(approval.value)
    return ((query, values) if approval.location == "body" else (values, payload))


def boundary_side(value, schema):
    """A lower-bound capability cannot be relabelled as an upper-bound check."""
    sides = []
    if type(value) in {int, float}:
        for key, lower in (("minimum", True), ("maximum", False),
                           ("exclusiveMinimum", True), ("exclusiveMaximum", False)):
            boundary = schema.get(key)
            if type(boundary) not in {int, float}:
                continue
            exclusive = key.startswith("exclusive") or schema.get(
                "exclusiveMinimum" if lower else "exclusiveMaximum") is True
            outside = (value < boundary or exclusive and value == boundary) if lower else (
                value > boundary or exclusive and value == boundary)
            if outside:
                sides.append("lower" if lower else "upper")
    if isinstance(value, (str, list)):
        minimum, maximum = (("minLength", "maxLength") if isinstance(value, str)
                            else ("minItems", "maxItems"))
        if minimum in schema and len(value) < schema[minimum]:
            sides.append("shorter")
        if maximum in schema and len(value) > schema[maximum]:
            sides.append("longer")
    return tuple(sides)


def select_negative_approval(approval, operation, value=None, *, override=False):
    """Use exact data only within an explicitly approved, unchanged deviation capability."""
    import json

    from agenttest.policies import NeedsReview

    if not override:
        return approval
    if approval is None:
        raise NeedsReview("NEGATIVE_VALUE_VARIANT_NOT_AUTHORIZED")
    if approval.kind == "required_missing":
        raise NeedsReview("NEGATIVE_MISSING_FIELD_HAS_NO_REPLACEMENT_VALUE")
    if json_equal(value, approval.value):
        return approval
    if not approval.allow_value_variants:
        raise NeedsReview("NEGATIVE_VALUE_VARIANT_NOT_AUTHORIZED")
    if approval.location == "query" and type(value) not in {str, int, float, bool}:
        raise NeedsReview("NEGATIVE_QUERY_VARIANT_UNREPRESENTABLE")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError):
        raise NeedsReview("NEGATIVE_VALUE_NOT_JSON_DATA") from None
    if len(encoded) > 1800:
        raise NeedsReview("NEGATIVE_VALUE_TOO_LARGE")
    changed = ApprovedNegative.model_validate(dict(approval.model_dump(), value=value))
    validate_approval(changed, operation)
    schema, _ = approval_schema(approval, operation)
    if (approval.kind == "boundary_outside" and
            boundary_side(approval.value, schema) != boundary_side(value, schema)):
        raise NeedsReview("NEGATIVE_VALUE_BOUNDARY_DIRECTION_CHANGED")
    return changed
