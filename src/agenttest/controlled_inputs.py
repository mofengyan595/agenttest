"""Operator-selected JSON credential fields; values never become planner bindings."""

import re

from agenttest.request_validation import request_schemas, validate_value


def credential_alias(value):
    if not re.fullmatch(r"AGENTTEST_TARGET_[A-Z0-9_]{1,63}", value):
        raise ValueError("TARGET_CREDENTIAL_ALIAS_REQUIRED")
    return value


def controlled_payload(operation, aliases, environ=None):
    """Validate a closed string-field mapping, optionally resolving its private values."""
    if not aliases:
        return {}
    if operation.method not in {"POST", "PUT", "PATCH"}:
        raise ValueError("CONTROLLED_INPUT_REQUIRES_JSON_WRITE")
    _, schema = request_schemas(operation, "application/json")
    properties = (schema or {}).get("properties", {})
    payload = {}
    for name, alias in aliases.items():
        credential_alias(alias)
        field = properties.get(name, {})
        if field.get("type") != "string" or field.get("readOnly"):
            raise ValueError("CONTROLLED_INPUT_REQUIRES_STRING_BODY_FIELD")
        if environ is not None:
            value = environ.get(alias)
            if not value or any(c in value for c in "\r\n\x00"):
                raise ValueError("CONTROLLED_INPUT_CREDENTIAL_MISSING")
            validate_value(value, field)
            payload[name] = value
    return payload
