"""Bounded, linear regular-expression subset shared by validation and input suggestions."""

import re
from functools import lru_cache

from agenttest.policies import NeedsReview


@lru_cache(maxsize=128)
def _branches(pattern: str):
    if not pattern or len(pattern) > 160 or len(pattern.split("|")) > 8:
        raise NeedsReview("UNSUPPORTED_REQUEST_PATTERN")
    prefix, suffix = ("^" if pattern.startswith("^") else ""), (
        "$" if pattern.endswith("$") else "")
    core = pattern.removeprefix("^").removesuffix("$")
    if core.startswith("(") and core.endswith(")"):
        inner = core[1:-1].removeprefix("?:")
        if all(c.isalnum() or c in "_- @|" for c in inner):
            pattern = "|".join(prefix + branch + suffix for branch in inner.split("|"))
    result = []
    for branch in pattern.split("|"):
        body = branch.removeprefix("^").removesuffix("$")
        if body and all(c.isalnum() or c in "_- @" for c in body):
            result.append((body, None, None))
            continue
        match = re.fullmatch(r"\[([A-Za-z0-9_-]+)\](\+|\*|\?|\{\d{1,4}(?:,\d{0,4})?\})?", body)
        if not match:
            raise NeedsReview("UNSUPPORTED_REQUEST_PATTERN")
        members, quantifier = match.groups()
        characters, index = [], 0
        while index < len(members):
            start = members[index]
            if index + 2 < len(members) and members[index + 1] == "-":
                end = members[index + 2]
                if not any(start in group and end in group for group in (
                        "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789")):
                    raise NeedsReview("UNSUPPORTED_REQUEST_PATTERN")
                if start > end:
                    raise NeedsReview("UNSUPPORTED_REQUEST_PATTERN")
                characters.extend(chr(c) for c in range(ord(start), ord(end) + 1))
                index += 3
            else:
                if start == "-" and index not in {0, len(members) - 1}:
                    raise NeedsReview("UNSUPPORTED_REQUEST_PATTERN")
                characters.append(start)
                index += 1
        lower, upper = {None: (1, 1), "+": (1, None), "*": (0, None), "?": (0, 1)}.get(
            quantifier, (None, None))
        if lower is None:
            limits = quantifier[1:-1].split(",")
            lower = int(limits[0])
            upper = lower if len(limits) == 1 else int(limits[1]) if limits[1] else None
            if lower > 1024 or upper is not None and (upper > 1024 or upper < lower):
                raise NeedsReview("UNSUPPORTED_REQUEST_PATTERN")
        result.append((characters[0], lower, upper))
    return tuple(result)


def supported_pattern(pattern):
    if not isinstance(pattern, str):
        raise NeedsReview("UNSUPPORTED_REQUEST_PATTERN")
    _branches(pattern)


def matches_pattern(pattern: str, value: str) -> bool:
    """JSON Schema uses substring matching; ECMAScript $ does not ignore a final newline."""
    supported_pattern(pattern)
    return re.search(pattern.replace("$", r"\Z"), value) is not None


def pattern_examples(pattern: str, minimum: int, maximum: int):
    """Suggest bounded witnesses only; the complete schema still validates every candidate."""
    supported_pattern(pattern)
    for seed, lower, upper in _branches(pattern):
        if lower is not None:
            size = max(lower, minimum)
            if upper is not None:
                size = min(size, upper)
            if size > maximum:
                continue
            seed *= size
        if len(seed) > maximum:
            continue
        yield seed
        padding = "a" * max(0, min(minimum, maximum) - len(seed))
        if padding:
            yield padding + seed
            yield seed + padding
