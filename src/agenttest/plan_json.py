"""Shared limits for untrusted JSON, independent of the optional graph runtime."""

import json
import math

from agenttest.diagnostics import Layer, ValidationFailure

# Closed reasons for a syntax rejection. ``json``'s own message is a fixed library string, and
# the position is an integer: the repair needs both, while the offending *text* is never
# serialised (it is model output and may contain values or credentials).
SYNTAX_REASONS = (
    ("Expecting ':' delimiter", "MISSING_COLON"),
    ("Expecting ',' delimiter", "MISSING_COMMA"),
    ("Expecting property name enclosed in double quotes", "UNQUOTED_KEY"),
    ("Expecting value", "MISSING_VALUE"),
    ("Unterminated string starting at", "UNTERMINATED_STRING"),
    ("Invalid control character at", "CONTROL_CHARACTER"),
    ("Invalid \\escape", "INVALID_ESCAPE"),
    ("Extra data", "TRAILING_DATA"),
)


def _syntax_reason(message: str) -> str:
    for prefix, code in SYNTAX_REASONS:
        if str(message or "").startswith(prefix):
            return code
    return "INVALID_TOKEN"


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
    except json.JSONDecodeError as error:
        failure = ValidationFailure(Layer.JSON, "JSON_SYNTAX")
        failure.diagnostic.detail = dict(position=int(error.pos or 0),
                                         line=int(error.lineno or 0),
                                         column=int(error.colno or 0),
                                         reason=_syntax_reason(error.msg))
        raise failure from None
    except RecursionError:
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
