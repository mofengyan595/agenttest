"""Shared limits for untrusted JSON, independent of the optional graph runtime."""

import json
import math
import re

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

# P3-B2.5: a position alone tells a model where a rejection is, not what is wrong there. These
# closed facts name the missing structure and give one actionable edit, without repeating any
# model-supplied text (the parser's offset is enough to know the shape, the value never travels).
STRUCTURE_FACTS = {
    "MISSING_COLON": dict(kind="OBJECT_MEMBER", expected="COLON_THEN_VALUE",
                          repair="对象的键后面必须有冒号和值；在该位置补上“:”与该成员的值。"),
    "MISSING_COMMA": dict(kind="MEMBER_SEPARATOR", expected="COMMA",
                          repair="两个成员之间缺少逗号；在该位置补上“,”，其余成员保持不变。"),
    "UNQUOTED_KEY": dict(kind="OBJECT_KEY", expected="DOUBLE_QUOTED_KEY",
                         repair="对象的键必须用双引号包住；只给该键补上双引号。"),
    "MISSING_VALUE": dict(kind="MEMBER_VALUE", expected="JSON_VALUE",
                          repair="冒号后面缺少值；补一个合法值，未知值写 null，字符串要带双引号。"),
    "UNTERMINATED_STRING": dict(kind="STRING_LITERAL", expected="CLOSING_QUOTE",
                                repair="字符串缺少结束双引号；在该字符串末尾补上。"),
    "CONTROL_CHARACTER": dict(kind="STRING_LITERAL", expected="ESCAPED_CONTROL",
                              repair="字符串里出现控制字符；改写为 \\n、\\t 等转义或删除。"),
    "INVALID_ESCAPE": dict(kind="STRING_LITERAL", expected="VALID_ESCAPE",
                           repair="字符串里有非法转义；改成合法转义，或把反斜杠写成双反斜杠。"),
    "TRAILING_DATA": dict(kind="DOCUMENT", expected="SINGLE_ROOT_OBJECT",
                          repair="根对象结束后不能再有内容；只保留一个 JSON 对象。"),
    "INVALID_TOKEN": dict(kind="TOKEN", expected="VALID_JSON_TOKEN",
                          repair="该位置不是合法 JSON 记号；按提示位置改写这一处。"),
}
# A missing colon is only locatable when the offset proves a key position and the key itself is a
# plain identifier; anything else stays unnamed rather than echoing model text.
KEY_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"$')
KEY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")


def _syntax_reason(message: str) -> str:
    for prefix, code in SYNTAX_REASONS:
        if str(message or "").startswith(prefix):
            return code
    return "INVALID_TOKEN"


def _member_key_before(text: str, position: int) -> str | None:
    """The key whose ``:`` is missing, when the offset and its shape make that certain."""
    head = str(text or "")[:max(0, int(position or 0))]
    match = KEY_TOKEN.search(head.rstrip())
    if match is None or not KEY_NAME.match(match.group(1)):
        return None
    before = head[:match.start()].rstrip()
    if not before or before[-1] not in "{,":
        return None
    return match.group(1)


def _syntax_detail(text: str, error: json.JSONDecodeError) -> dict:
    """Everything a repair can act on: where, which closed reason, and what shape is missing."""
    position = int(error.pos or 0)
    reason = _syntax_reason(error.msg)
    facts = STRUCTURE_FACTS.get(reason) or STRUCTURE_FACTS["INVALID_TOKEN"]
    structure = dict(kind=facts["kind"], expected=facts["expected"],
                     at_end=position >= len(text))
    repair = facts["repair"]
    if reason == "UNQUOTED_KEY" and str(text or "")[position:].lstrip()[:1] in ("}", "]"):
        # The parser asks for a key only because the members ended with a comma.
        structure = dict(kind="MEMBER_LIST", expected="NO_TRAILING_COMMA", at_end=False)
        repair = "成员列表末尾多了逗号；删掉最后一个成员后面那个逗号。"
    if reason == "MISSING_COLON":
        key = _member_key_before(text, position)
        if key:
            structure["after_key"] = key
    return dict(position=position,
                line=int(error.lineno or 0),
                column=int(error.colno or 0),
                reason=reason,
                parser_error_type="JSON_DECODE_ERROR",
                expected_structure=structure,
                repair=repair)


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
        failure.diagnostic.detail = _syntax_detail(raw, error)
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
