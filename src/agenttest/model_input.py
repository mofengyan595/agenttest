"""Model-facing projection of diagnostics: sanitise values, keep the repair facts.

A stored diagnostic is audit evidence, and some producers legitimately record free text (an
error message, a model's output window, a validator's rule sentence). Copying such a dict into a
tool result put that text on the wire: the P3-B2.5 audit found two such paths
(``payload.cases[].preflight_diagnostic`` and a design rejection's ``detail``). This module
projects the *values*, not only the keys: identifiers, field paths, small integers and closed
enum words pass unchanged, credential shapes are redacted in place, and free text is capped.
Stored evidence is never modified — only the copy the model reads.
"""

import json
import re

from agenttest.selectors import SELECTOR

REDACTED = "[已脱敏]"
MAX_KEYS = 12        # matches the bound ``Diagnostic.detail`` already has
MAX_ITEMS = 10       # list/dict entries per fact
MAX_TEXT = 240       # free text per fact
MAX_CHARS = 2400     # serialised size of one projected detail

CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,63}$")
PATH_PART = re.compile(r"^\*$|^[A-Za-z_][A-Za-z0-9_]{0,63}$|^\d{1,2}$")
ENUM_WORD = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# Credential shapes. A match is replaced, so "Authorization: Bearer x" keeps its words and loses
# the secret. Deliberately narrow: ordinary identifiers, paths and rule sentences never match.
SECRET = re.compile(
    r"(?i:(?:authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9._\-+/=]{6,}"
    r"|(?:authorization|x-api-key|api[-_]?key|apikey|access[-_]?token|refresh[-_]?token"
    r"|password|passwd|secret|cookie)[\"']?\s*[:=]\s*[\"']?[^\s\"',;}]{4,}"
    r"|sk-[A-Za-z0-9_\-]{8,}"
    r"|(?:AKIA|ASIA)[A-Za-z0-9]{12,}"
    r"|[A-Fa-f0-9]{32,}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})")
# A value-shaped string that must never be echoed even though it is not credential-shaped: the
# name slot of a diagnostic is not a place for request values, so anything with spaces, quotes,
# brackets or punctuation beyond a path's own separators is treated as text and capped.
TEXTY = re.compile(r"[\s\"'<>{}()\[\],;|]")


# A value under any other key must be a fact; only these closed slots may carry prose. Every
# entry is written by the application (a repair hint, a validator rule sentence), by the operator
# (an approved scope quote) or by the model itself (the window around its own invalid JSON).
TEXT_KEYS = frozenset({"hint", "message", "window", "quote", "negative_literal_hint", "schema",
                       "violated_constraint", "expected_rule", "constraint", "violation",
                       "expected", "expected_type", "actual_type", "repair", "reason"})
DROP = object()


def redact(value: str) -> str:
    """Replace credential-shaped spans, keeping the surrounding message readable."""
    return SECRET.sub(REDACTED, value)


def safe_text(value: str, limit: int = MAX_TEXT) -> str:
    cleaned = redact(str(value))
    cleaned = "".join(" " if char in "\r\n\t" else char
                      for char in cleaned if char >= " " or char == "\n")
    return cleaned[:limit]


def safe_path(value) -> str | None:
    if not isinstance(value, str) or len(value) > 96:
        return None
    parts = value.split(".")
    if parts and all(PATH_PART.match(part) for part in parts) and not SECRET.search(value):
        return value
    return None


def safe_selector(value) -> str | None:
    if (isinstance(value, str) and len(value) <= 200 and SELECTOR.fullmatch(value)
            and not SECRET.search(value)):
        return value
    return None


def _value(value, key=None, depth=0, items=MAX_ITEMS, text=MAX_TEXT):
    """Project one stored value; return it plus how many items were left out.

    A string under a *fact* key must look like a fact (identifier, path, enum word or a small
    number) or it is dropped and counted: a value pasted into a fact slot is exactly the leak
    this projection exists to stop. Only the closed ``TEXT_KEYS`` slots may carry prose, and
    prose is redacted and capped.
    """
    text_slot = key in TEXT_KEYS
    if value is None or isinstance(value, bool):
        return value, 0
    if type(value) is int:
        return (value if -1_000_000_000 <= value <= 1_000_000_000 else 0), 0
    if isinstance(value, float):
        return (value if value == value and abs(value) < 1e12 else 0), 0
    if isinstance(value, str):
        if text_slot:
            return safe_text(value, text), 0
        if _fact(value):
            return value, 0
        return DROP, 1
    if isinstance(value, (list, tuple)):
        kept, dropped = [], 0
        for item in value:
            if len(kept) >= items:
                dropped += 1
                continue
            projected, lost = _value(item, key, depth + 1, items, text)
            dropped += lost
            if projected is DROP or projected in kept:
                continue
            kept.append(projected)
        return kept, dropped
    if isinstance(value, dict):
        kept, dropped = {}, 0
        for inner, item in value.items():
            if len(kept) >= items:
                dropped += 1
                continue
            name = inner if isinstance(inner, str) and IDENTIFIER.match(inner) else "*"
            if name in kept:
                dropped += 1
                continue
            projected, lost = _value(item, inner if isinstance(inner, str) else None,
                                     depth + 1, items, text)
            dropped += lost
            if projected is DROP:
                continue
            kept[name] = projected
        return kept, dropped
    return safe_text(str(value), text), 0


def _fact(value: str) -> bool:
    """Identifier, dotted path, enum word or a small number; never a credential."""
    if SECRET.search(value):
        return False
    if IDENTIFIER.match(value) or ENUM_WORD.match(value) or re.fullmatch(r"\d{1,6}", value):
        return True
    return safe_path(value) is not None


def safe_detail(detail, *, limit: int = MAX_KEYS, chars: int = MAX_CHARS,
                items: int = MAX_ITEMS, text: int = MAX_TEXT):
    """A projected copy of one ``detail`` dict, plus the counts of what was left out."""
    if not isinstance(detail, dict):
        return {}, {}
    kept, omitted = {}, {}
    for key, value in detail.items():
        if len(kept) >= limit:
            omitted["keys"] = len(detail) - len(kept)
            break
        name = key if isinstance(key, str) and IDENTIFIER.match(key) else "*"
        projected, dropped = _value(value, key if isinstance(key, str) else None,
                                    items=items, text=text)
        if projected is DROP:
            omitted[name] = 1
            continue
        if not projected and dropped:
            omitted[name] = dropped  # an empty container whose whole content was dropped
            continue
        if dropped:
            omitted[name] = dropped
        kept[name] = projected
    while kept and len(json.dumps(kept, ensure_ascii=False)) > chars:
        name = list(kept)[-1]
        kept.pop(name)
        omitted[name] = "DROPPED_FOR_SIZE"
    if omitted and "omitted" not in kept:
        kept["omitted"] = omitted
    return kept, omitted


def safe_fields(fields) -> list[str]:
    """Masked field paths only; anything else is dropped rather than echoed."""
    if not isinstance(fields, (list, tuple)):
        return []
    kept = []
    for item in fields:
        path = safe_path(item)
        if path is not None and path not in kept:
            kept.append(path)
    return kept[:8]


def safe_code(value) -> str:
    """A closed diagnostic code, or an empty string when the stored value is not one."""
    return value if isinstance(value, str) and CODE.match(value) else ""


def diagnostic_view(diagnostic, *, repair_kind=None) -> dict:
    """The model-facing form of one stored diagnostic: closed code, paths, projected detail."""
    if not isinstance(diagnostic, dict):
        return {}
    code = safe_code(diagnostic.get("code"))
    view = dict(code=code)
    layer = diagnostic.get("layer")
    if isinstance(layer, str) and ENUM_WORD.match(layer.upper()):
        view["layer"] = layer.upper()
    paths = safe_fields(diagnostic.get("fields"))
    if paths:
        view["fields"] = paths
    reference = diagnostic.get("reference")
    if isinstance(reference, dict):
        branch = reference.get("branch")
        if isinstance(branch, str) and ENUM_WORD.match(branch):
            step_index = reference.get("step_index")
            view["reference"] = dict(branch=branch, step_index=(
                step_index if type(step_index) is int and 0 <= step_index <= 99 else None))
    detail, omitted = safe_detail(diagnostic.get("detail"))
    if detail:
        view["detail"] = detail
    if repair_kind:
        view["repair_kind"] = repair_kind
    return view
