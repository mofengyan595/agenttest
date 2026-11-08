"""Strict, explicit OpenAPI request-schema subset; no silent partial validation."""

import math
import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from agenttest.contracts import Operation
from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.policies import NeedsReview
from agenttest.request_patterns import matches_pattern, supported_pattern

ANNOTATIONS = {"title", "description", "default", "example", "examples", "deprecated",
               "externalDocs", "writeOnly", "readOnly"}
CONSTRAINTS = {"type", "nullable", "enum", "properties", "required", "additionalProperties",
               "items", "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum",
               "exclusiveMinimum", "exclusiveMaximum", "format", "contentMediaType", "pattern"}


def supported_schema(schema: Any, depth: int = 0) -> None:
    if not isinstance(schema, dict) or depth > 30 or set(schema) - ANNOTATIONS - CONSTRAINTS:
        raise NeedsReview("UNSUPPORTED_REQUEST_SCHEMA")
    kind = schema.get("type")
    if not isinstance(kind, str) or kind not in {
        "object", "array", "string", "integer", "number", "boolean", "null",
    }:
        raise NeedsReview("UNSUPPORTED_REQUEST_TYPE")
    text_formats = {"binary", "password", "uuid", "email", "date", "date-time"}
    if schema.get("format") not in {None, *text_formats, "int32", "int64", "float", "double"}:
        raise NeedsReview("UNSUPPORTED_REQUEST_FORMAT")
    if ("contentMediaType" in schema
            and (kind != "string" or schema["contentMediaType"] != "application/octet-stream")):
        raise NeedsReview("UNSUPPORTED_CONTENT_MEDIA_TYPE")
    if ((schema.get("format") in {"int32", "int64"} and kind != "integer")
            or (schema.get("format") in text_formats and kind != "string")
            or (schema.get("format") in {"float", "double"} and kind != "number")):
        raise NeedsReview("UNSUPPORTED_REQUEST_FORMAT_TYPE")
    applicability = {
        "properties": {"object"}, "required": {"object"}, "additionalProperties": {"object"},
        "items": {"array"}, "minItems": {"array"}, "maxItems": {"array"},
        "minLength": {"string"}, "maxLength": {"string"},
        "pattern": {"string"},
        "minimum": {"number", "integer"}, "maximum": {"number", "integer"},
        "exclusiveMinimum": {"number", "integer"}, "exclusiveMaximum": {"number", "integer"},
    }
    if any(key in schema and kind not in kinds for key, kinds in applicability.items()):
        raise NeedsReview("UNSUPPORTED_REQUEST_CONSTRAINT")
    if "pattern" in schema:
        supported_pattern(schema["pattern"])
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise NeedsReview("UNSUPPORTED_REQUEST_CONSTRAINT_VALUE")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key not in schema:
            continue
        value = schema[key]
        if key.startswith("exclusive") and type(value) is bool:
            continue
        try:
            finite = type(value) in {int, float} and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise NeedsReview("UNSUPPORTED_REQUEST_CONSTRAINT_VALUE")
    if "nullable" in schema and type(schema["nullable"]) is not bool:
        raise NeedsReview("UNSUPPORTED_REQUEST_CONSTRAINT_VALUE")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise NeedsReview("UNSUPPORTED_REQUEST_ENUM")
    if kind == "object":
        properties, required = schema.get("properties", {}), schema.get("required", [])
        if (not isinstance(properties, dict) or not isinstance(required, list)
                or any(not isinstance(name, str) for name in [*properties, *required])):
            raise NeedsReview("UNSUPPORTED_REQUEST_OBJECT")
        if schema.get("additionalProperties", False) is not False:
            raise NeedsReview("OPEN_OBJECT_SCHEMA_NOT_SUPPORTED")
        for child in schema.get("properties", {}).values():
            supported_schema(child, depth + 1)
    if kind == "array":
        supported_schema(schema.get("items"), depth + 1)


def validate_value(value: Any, schema: dict) -> None:
    supported_schema(schema)
    if "enum" in schema and not any(json_equal(value, v) for v in schema["enum"]):
        raise ValidationFailure(Layer.OPENAPI, "OPENAPI_VALUE_ENUM", "Request value outside enum")
    if value is None and schema.get("nullable") is True:
        return
    kind = schema["type"]
    matches = {
        "object": type(value) is dict, "array": type(value) is list,
        "string": type(value) is str, "integer": type(value) is int,
        "number": type(value) in {int, float}, "boolean": type(value) is bool,
        "null": value is None,
    }
    if not matches[kind] or (type(value) is float and not math.isfinite(value)):
        raise ValidationFailure(Layer.OPENAPI, "OPENAPI_VALUE_TYPE", "Request value has wrong type")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(value) - set(properties) or set(schema.get("required", [])) - set(value):
            raise ValidationFailure(
                Layer.OPENAPI, "OPENAPI_OBJECT_FIELDS",
                "Unknown or missing request field")
        for key, item in value.items():
            if properties[key].get("readOnly"):
                raise ValidationFailure(
                    Layer.OPENAPI, "OPENAPI_READ_ONLY_FIELD_IN_REQUEST",
                    "Read-only field in request")
            validate_value(item, properties[key])
    if kind == "array":
        for item in value:
            validate_value(item, schema["items"])
    if kind in {"array", "string"}:
        unit = "Items" if kind == "array" else "Length"
        if (len(value) < schema.get("min" + unit, 0)
                or len(value) > schema.get("max" + unit, math.inf)):
            raise ValidationFailure(
                Layer.OPENAPI, "OPENAPI_VALUE_LENGTH",
                "Request length outside bounds")
    if kind == "string":
        validate_string_format(value, schema.get("format"))
        if "pattern" in schema and not matches_pattern(schema["pattern"], value):
            raise ValidationFailure(Layer.OPENAPI, "OPENAPI_VALUE_PATTERN",
                                    "Request value does not match the approved pattern")
    if kind in {"number", "integer"}:
        for bound, direction in (("minimum", -1), ("maximum", 1)):
            limit = schema.get(bound)
            exclusive = schema.get("exclusive" + bound.capitalize(), False)
            if type(exclusive) in {int, float}:
                if (value - exclusive) * direction >= 0:
                    raise ValidationFailure(
                        Layer.OPENAPI, "OPENAPI_VALUE_EXCLUSIVE_BOUND",
                        "Request numeric value outside exclusive bounds")
            if limit is not None and (
                (value - limit) * direction > 0 or (exclusive is True and value == limit)
            ):
                raise ValidationFailure(
                    Layer.OPENAPI, "OPENAPI_VALUE_BOUND",
                    "Request numeric value outside bounds")
        if schema.get("format") in {"int32", "int64"}:
            bits = int(schema["format"][3:])
            if value < -(2 ** (bits - 1)) or value >= 2 ** (bits - 1):
                raise ValidationFailure(
                    Layer.OPENAPI, "OPENAPI_VALUE_FORMAT_BOUND",
                    "Request integer outside format bounds")


def validate_string_format(value: str, format_name: str | None) -> None:
    """Validate common scalar formats locally; never resolve a host or load content."""
    valid = True
    try:
        if format_name == "uuid":
            valid = str(UUID(value)) == value.lower()
        elif format_name == "email":
            valid = bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))
        elif format_name == "date":
            valid = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
            date.fromisoformat(value)
        elif format_name == "date-time":
            valid = bool(re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})", value))
            datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        valid = False
    if not valid:
        raise ValidationFailure(Layer.OPENAPI, "OPENAPI_VALUE_FORMAT", "Invalid request format")


RESERVED_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie",
                              "set-cookie", "host", "content-length"})
SENSITIVE_HEADER_NAMES = frozenset({"x-api-key", "api-key", "x-auth-token", "x-access-token",
                                    "x-secret", "x-token", "token", "secret"})
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}")


def header_name(name: str) -> str:
    """Reject a header name the executor can never own; return the canonical spelling."""
    if not isinstance(name, str) or not HEADER_NAME.fullmatch(name):
        raise ValidationFailure(Layer.OPENAPI, "OPENAPI_INVALID_HEADER_NAME",
                                "Invalid header parameter name")
    if name.lower() in RESERVED_HEADERS:
        raise ValidationFailure(Layer.OPENAPI, "OPENAPI_RESERVED_HEADER_PARAMETER",
                                "Credentials and framing headers stay executor-owned")
    return name


def sensitive_headers(operation: Operation) -> set[str]:
    """Header parameters whose value never reaches persisted evidence."""
    names = set()
    for param in operation.parameters:
        if param["in"] != "header":
            continue
        schema = param.get("schema") or {}
        if (param.get("x-sensitive") is True or schema.get("x-sensitive") is True
                or str(schema.get("format", "")).lower() in {"password", "token", "apikey"}
                or param["name"].lower() in SENSITIVE_HEADER_NAMES):
            names.add(param["name"].lower())
    return names


def public_headers(operation: Operation, headers: dict) -> dict[str, str]:
    """Report header names with sensitive values masked; never log raw credentials."""
    hidden = sensitive_headers(operation)
    return {name: ("[REDACTED]" if name.lower() in hidden else str(value))
            for name, value in headers.items()}


def request_schemas(operation: Operation, media: str | None) -> tuple[dict, dict | None]:
    parameters = {"path": {}, "query": {}, "header": {}}
    seen_headers: set[str] = set()
    for param in operation.parameters:
        location = param["in"]
        if location not in parameters:
            if param.get("required"):
                raise NeedsReview("UNSUPPORTED_REQUIRED_PARAMETER")
            continue
        if location == "header":
            header_name(param["name"])
            if param["name"].lower() in seen_headers:
                raise ValidationFailure(Layer.OPENAPI, "OPENAPI_DUPLICATE_HEADER_PARAMETER",
                                        "Duplicate header parameter")
            seen_headers.add(param["name"].lower())
        if (param.get("style", "form" if location == "query" else "simple")
                != ("form" if location == "query" else "simple")
                or "content" in param):
            raise NeedsReview("UNSUPPORTED_PARAMETER_ENCODING")
        schema = param.get("schema")
        supported_schema(schema)
        if schema["type"] not in {"string", "integer", "number", "boolean"}:
            raise NeedsReview("UNSUPPORTED_PARAMETER_TYPE")
        parameters[location][param["name"]] = schema
    body = None
    if media:
        content = (operation.request_body or {}).get("content", {})
        if media not in content:
            raise ValidationFailure(
                Layer.OPENAPI, "OPENAPI_UNDECLARED_BODY_MEDIA_TYPE",
                "Undeclared body media type")
        body = content[media].get("schema")
        supported_schema(body)
        if body["type"] != "object":
            raise NeedsReview("ONLY_OBJECT_REQUEST_BODIES_SUPPORTED")
    return parameters, body


def canonical_headers(declared: dict, values: dict) -> dict[str, str]:
    """Match request headers case-insensitively and keep the declared spelling."""
    names = {name.lower(): name for name in declared}
    result = {}
    for name, value in values.items():
        canonical = names.get(str(name).lower())
        if canonical is None:
            raise ValidationFailure(Layer.OPENAPI, "OPENAPI_UNKNOWN_PARAMETER",
                                    "Unknown operation parameter")
        result[canonical] = value
    return result


def validate_request(operation: Operation, path: dict, query: dict, payload: dict,
                     media: str | None, headers: dict | None = None) -> None:
    parameters, body = request_schemas(operation, media)
    headers = canonical_headers(parameters["header"], headers or {})
    for location, values in (("path", path), ("query", query), ("header", headers)):
        if set(values) - set(parameters[location]):
            raise ValidationFailure(
                Layer.OPENAPI, "OPENAPI_UNKNOWN_PARAMETER",
                "Unknown operation parameter")
        for name, value in values.items():
            validate_value(value, parameters[location][name])
    for param in operation.parameters:
        values = {"path": path, "query": query, "header": headers}.get(param["in"], {})
        if param.get("required") and param["name"] not in values:
            raise ValidationFailure(
                Layer.OPENAPI, "OPENAPI_PARAMETER_REQUIRED",
                "Missing required operation parameter")
    if body is not None:
        validate_value(payload, body)
