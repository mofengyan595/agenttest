"""Shared limits for untrusted JSON, independent of the optional graph runtime."""

import json
import math

from agenttest.diagnostics import Layer, ValidationFailure


def bounded_json(raw: str, limit: int):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise ValidationFailure(Layer.JSON, "JSON_SIZE", "Invalid JSON size")

    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValidationFailure(Layer.JSON, "JSON_DUPLICATE_KEY", "Duplicate JSON key")
            data[key] = value
        return data

    def invalid_constant(value):
        raise ValidationFailure(Layer.JSON, "JSON_NONFINITE_NUMBER", "Non-JSON number")

    try:
        data = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (json.JSONDecodeError, RecursionError):
        raise ValidationFailure(Layer.JSON, "JSON_SYNTAX") from None
    pending, nodes = [(data, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 40 or nodes > 20_000 or (type(value) is float and not math.isfinite(value)):
            raise ValidationFailure(Layer.JSON, "JSON_STRUCTURE_LIMIT",
                                    "JSON structure limit exceeded")
        if isinstance(value, (dict, list)):
            pending.extend((v, depth + 1) for v in (
                value.values() if isinstance(value, dict) else value))
    return data
