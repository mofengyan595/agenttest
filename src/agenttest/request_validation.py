"""Strict, explicit OpenAPI request-schema subset; no silent partial validation."""

import math
from typing import Any

from agenttest.contracts import Operation
from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.policies import NeedsReview

ANNOTATIONS = {"title", "description", "default", "example", "examples", "deprecated",
               "externalDocs", "writeOnly", "readOnly"}
CONSTRAINTS = {"type", "nullable", "enum", "properties", "required", "additionalProperties",
               "items", "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum",
               "exclusiveMinimum", "exclusiveMaximum", "format", "contentMediaType"}


def supported_schema(schema: Any, depth: int = 0) -> None:
    if not isinstance(schema, dict) or depth > 30 or set(schema) - ANNOTATIONS - CONSTRAINTS:
        raise NeedsReview("UNSUPPORTED_REQUEST_SCHEMA")
    kind = schema.get("type")
    if not isinstance(kind, str) or kind not in {
        "object", "array", "string", "integer", "number", "boolean", "null",
    }:
        raise NeedsReview("UNSUPPORTED_REQUEST_TYPE")
    if schema.get("format") not in {None, "binary", "password", "int32", "int64"}:
        raise NeedsReview("UNSUPPORTED_REQUEST_FORMAT")
    if ("contentMediaType" in schema
            and (kind != "string" or schema["contentMediaType"] != "application/octet-stream")):
        raise NeedsReview("UNSUPPORTED_CONTENT_MEDIA_TYPE")
    if ((schema.get("format") in {"int32", "int64"} and kind != "integer")
            or (schema.get("format") in {"binary", "password"} and kind != "string")):
        raise NeedsReview("UNSUPPORTED_REQUEST_FORMAT_TYPE")
    applicability = {
        "properties": {"object"}, "required": {"object"}, "additionalProperties": {"object"},
        "items": {"array"}, "minItems": {"array"}, "maxItems": {"array"},
        "minLength": {"string"}, "maxLength": {"string"},
        "minimum": {"number", "integer"}, "maximum": {"number", "integer"},
        "exclusiveMinimum": {"number", "integer"}, "exclusiveMaximum": {"number", "integer"},
    }
    if any(key in schema and kind not in kinds for key, kinds in applicability.items()):
        raise NeedsReview("UNSUPPORTED_REQUEST_CONSTRAINT")
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


def request_schemas(operation: Operation, media: str | None) -> tuple[dict, dict | None]:
    parameters = {"path": {}, "query": {}}
    for param in operation.parameters:
        location = param["in"]
        if location not in parameters:
            if param.get("required"):
                raise NeedsReview("UNSUPPORTED_REQUIRED_PARAMETER")
            continue
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


def validate_request(operation: Operation, path: dict, query: dict, payload: dict,
                     media: str | None) -> None:
    parameters, body = request_schemas(operation, media)
    for location, values in (("path", path), ("query", query)):
        if set(values) - set(parameters[location]):
            raise ValidationFailure(
                Layer.OPENAPI, "OPENAPI_UNKNOWN_PARAMETER",
                "Unknown operation parameter")
        for name, value in values.items():
            validate_value(value, parameters[location][name])
    for param in operation.parameters:
        values = {"path": path, "query": query}.get(param["in"], {})
        if param.get("required") and param["name"] not in values:
            raise ValidationFailure(
                Layer.OPENAPI, "OPENAPI_PARAMETER_REQUIRED",
                "Missing required operation parameter")
    if body is not None:
        validate_value(payload, body)
