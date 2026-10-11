"""A deliberately small JSONPath subset: $.field, [index], and root $."""

import re
from typing import Any

SELECTOR = re.compile(r"\$(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[\d{1,6}\]))*")
TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def validate_selector(path: str) -> str:
    if len(path) > 200 or not SELECTOR.fullmatch(path):
        raise ValueError("Unsupported JSONPath; use $.field[index] only")
    return path


def select(data: Any, path: str) -> Any:
    validate_selector(path)
    for key, index in TOKEN.findall(path):
        if key:
            if not isinstance(data, dict):
                raise KeyError(path)
            data = data[key]
        else:
            if not isinstance(data, list):
                raise KeyError(path)
            data = data[int(index)]
    return data
