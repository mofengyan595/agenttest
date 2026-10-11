"""Shared limits for untrusted JSON, independent of the optional graph runtime."""

import json
import math


def bounded_json(raw: str, limit: int):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise ValueError("Invalid JSON size")

    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("Duplicate JSON key")
            data[key] = value
        return data

    def invalid_constant(value):
        raise ValueError("Non-JSON number")

    data = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    pending, nodes = [(data, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 40 or nodes > 20_000 or (type(value) is float and not math.isfinite(value)):
            raise ValueError("JSON structure limit exceeded")
        if isinstance(value, (dict, list)):
            pending.extend((v, depth + 1) for v in (
                value.values() if isinstance(value, dict) else value))
    return data
