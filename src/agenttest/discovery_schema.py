"""Conservative witnesses and variations using the existing request validator."""

import math
from dataclasses import dataclass
from typing import Any

from agenttest.contracts import Operation
from agenttest.discovery_models import InputVariation
from agenttest.policies import NeedsReview
from agenttest.request_validation import request_schemas, supported_schema, validate_value

SCALARS = {"string", "integer", "number", "boolean"}
MAX_SAMPLE_LENGTH = 256


def versioned_schema(schema: dict, version: str) -> None:
    """Reject ambiguous dialect use instead of changing Executor interpretation."""
    supported_schema(schema)
    for key in ("exclusiveMinimum", "exclusiveMaximum"):
        if key in schema:
            correct_type = (
                type(schema[key]) is bool
                if version.startswith("3.0.")
                else type(schema[key]) in {int, float}
            )
            if not correct_type:
                raise NeedsReview("UNSUPPORTED_SCHEMA_DIALECT")
    if version.startswith("3.1.") and "nullable" in schema:
        raise NeedsReview("UNSUPPORTED_SCHEMA_DIALECT")
    for child in schema.get("properties", {}).values():
        versioned_schema(child, version)
    if "items" in schema:
        versioned_schema(schema["items"], version)


def edge(schema: dict, lower: bool) -> int | float | None:
    key = "minimum" if lower else "maximum"
    exclusive = schema.get("exclusive" + key.capitalize(), False)
    bound = schema.get(key)
    if type(exclusive) in {int, float}:
        bound = (
            max(bound, exclusive)
            if lower and bound is not None
            else (min(bound, exclusive) if not lower and bound is not None else exclusive)
        )
        strict = bound == exclusive
    else:
        strict = exclusive is True
    if bound is None:
        return None
    if schema["type"] == "integer":
        return (
            (math.floor(bound) + 1 if strict else math.ceil(bound))
            if lower
            else (math.ceil(bound) - 1 if strict else math.floor(bound))
        )
    return math.nextafter(bound, math.inf if lower else -math.inf) if strict else bound


def witness(schema: dict, extra: dict | None = None) -> Any:
    """Bounded search for a value satisfying both schemas; never claims completeness."""
    schemas = [schema] + ([extra] if extra is not None else [])
    for item in schemas:
        supported_schema(item)
    choices: list[Any] = []
    for item in schemas:
        choices.extend(item.get("enum", []))
    kind = schema["type"]
    if kind in {"integer", "number"}:
        choices.extend([0, 1, -1])
        for item in schemas:
            if item.get("type") not in {"integer", "number"}:
                continue
            for lower in (True, False):
                value = edge(item, lower)
                if value is not None:
                    choices.append(value)
    elif kind == "string":
        lengths = {1, 0}
        for item in schemas:
            lengths.update([item.get("minLength", 0), item.get("maxLength", 1)])
        choices.extend("a" * n for n in sorted(lengths) if 0 <= n <= MAX_SAMPLE_LENGTH)
    elif kind == "boolean":
        choices.extend([False, True])
    else:
        raise NeedsReview("UNSUPPORTED_DISCOVERY_VALUE_TYPE")
    for value in choices:
        if isinstance(value, str) and len(value) > MAX_SAMPLE_LENGTH:
            continue
        try:
            for item in schemas:
                validate_value(value, item)
        except ValueError:
            continue
        return value
    raise NeedsReview("DISCOVERY_NO_BOUNDED_WITNESS")


@dataclass
class FieldSpec:
    location: str
    name: str
    schema: dict
    required: bool
    basis: str
    required_basis: str


def fields(operation: Operation, version: str, basis: str) -> tuple[list[FieldSpec], str | None]:
    content = (operation.request_body or {}).get("content", {})
    if content and set(content) != {"application/json"}:
        raise NeedsReview("UNSUPPORTED_DISCOVERY_BODY_MEDIA")
    media = "application/json" if content else None
    parameters, body = request_schemas(operation, media)
    result = []
    for location, entries in parameters.items():
        for name, schema in sorted(entries.items()):
            versioned_schema(schema, version)
            index = next(
                i
                for i, p in enumerate(operation.parameters)
                if p["name"] == name and p["in"] == location
            )
            result.append(
                FieldSpec(
                    "path_parameters" if location == "path" else "query",
                    name,
                    schema,
                    operation.parameters[index].get("required", False),
                    f"{basis}/parameters/{index}/schema",
                    f"{basis}/parameters/{index}/required",
                )
            )
    if body is not None:
        versioned_schema(body, version)
        if body.get("enum"):
            raise NeedsReview("UNSUPPORTED_DISCOVERY_OBJECT_ENUM")
        if set(body.get("required", [])) - set(body.get("properties", {})):
            raise NeedsReview("UNSUPPORTED_DISCOVERY_REQUIRED_PROPERTY")
        for name, schema in sorted(body.get("properties", {}).items()):
            if schema.get("readOnly"):
                if name in body.get("required", []):
                    raise NeedsReview("UNSUPPORTED_DISCOVERY_REQUIRED_READ_ONLY")
                continue
            if schema.get("type") not in SCALARS:
                raise NeedsReview("UNSUPPORTED_DISCOVERY_NESTED_VALUE")
            escaped = name.replace("~", "~0").replace("/", "~1")
            result.append(
                FieldSpec(
                    "body",
                    name,
                    schema,
                    name in body.get("required", []),
                    f"{basis}/request_body/content/application~1json/schema/properties/{escaped}",
                    f"{basis}/request_body/content/application~1json/schema/required",
                )
            )
    if len(result) > 100:
        raise NeedsReview("DISCOVERY_FIELD_BUDGET")
    return result, media


def describe(field: FieldSpec, value: Any, action: str = "synthetic") -> InputVariation:
    enum = field.schema.get("enum", [])
    index = next(
        (i for i, item in enumerate(enum) if type(value) is type(item) and value == item), None
    )
    return InputVariation(
        location=field.location,
        name=field.name,
        action=action,
        value_type=field.schema["type"],
        enum_index=index,
        numeric_value=value if type(value) in {int, float} and index is None else None,
        boolean_value=value if type(value) is bool and index is None else None,
        string_length=len(value) if type(value) is str and index is None else None,
    )


def variations(field: FieldSpec) -> list[tuple[str, str, InputVariation, Any]]:
    """Negative entries are descriptions only; the value stays local for validation."""
    schema, result = field.schema, []

    def add(kind: str, polarity: str, action: str, value: Any = None) -> None:
        result.append((kind, polarity, describe(field, value, action), value))

    if field.required:
        add("required_missing", "negative", "omit_required")
    add("type_mismatch", "negative", "replace_with_wrong_scalar_type")
    if "enum" in schema:
        for i in sorted({0, len(schema["enum"]) - 1}):
            add("enum_boundary", "normal", "select_enum_member", schema["enum"][i])
        # An outside-enum description does not promise a same-type witness for exhaustive bool.
        if schema["type"] != "boolean" or not all(
            any(type(v) is bool and v is b for v in schema["enum"]) for b in (True, False)
        ):
            add("enum_boundary", "negative", "select_outside_enum")
    if schema["type"] in {"integer", "number"}:
        for lower in (True, False):
            value = edge(schema, lower)
            if value is None or not math.isfinite(value):
                continue
            kind = "numeric_lower" if lower else "numeric_upper"
            add(kind, "normal", "select_valid_edge", value)
            if schema["type"] == "integer":
                invalid = value - 1 if lower else value + 1
            else:
                invalid = math.nextafter(value, -math.inf if lower else math.inf)
            if math.isfinite(invalid):
                add(kind, "negative", "select_outside_bound", invalid)
    if schema["type"] == "string":
        for key, kind in (("minLength", "string_min_length"), ("maxLength", "string_max_length")):
            if key not in schema:
                continue
            n = schema[key]
            if n <= MAX_SAMPLE_LENGTH:
                add(kind, "normal", "select_valid_length", "a" * n)
            if key == "maxLength" or n > 0:
                variation = InputVariation(
                    location=field.location,
                    name=field.name,
                    action="select_outside_length",
                    value_type="string",
                    string_length=n + 1 if key == "maxLength" else n - 1,
                )
                result.append((kind, "negative", variation, None))
    return result
