"""Exact JSON value comparison shared by request approval and response checks."""

from typing import Any


def json_equal(actual: Any, expected: Any) -> bool:
    """Keep exact types at every depth, including bool versus integer."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            json_equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            json_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected
