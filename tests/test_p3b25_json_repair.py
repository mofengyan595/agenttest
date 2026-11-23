"""P3-B2.5: the JSON repair feedback must say *what* is missing, not only where.

The fixtures are verbatim raw outputs from the P3-B2.4 stability batch (release-v1/r3 and
release-v2/r4). Both failed at the same structural point: inside a step-argument object the
``name`` member lost its ``:`` and value, so ``json`` stopped at the comma that followed.
"""

import json
from pathlib import Path

import pytest

from agenttest.plan_json import bounded_json
from agenttest.product_agent import AgentSession

FIXTURES = Path(__file__).parent / "fixtures" / "p3b25"
ARCHIVED = ("p3b24-v1-negative-batch-3.json", "p3b24-v2-negative-batch-3.json")


def detail_of(raw: str) -> dict:
    with pytest.raises(Exception) as caught:
        bounded_json(raw, 200_000)
    diagnostic = caught.value.diagnostic
    assert diagnostic.code == "JSON_SYNTAX"
    return diagnostic.detail


@pytest.mark.parametrize("name", ARCHIVED)
def test_an_archived_batch_failure_names_the_missing_colon_and_the_key(name):
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    assert json.loads is not None
    with pytest.raises(json.JSONDecodeError) as caught:  # the archive really is malformed
        json.loads(raw)
    detail = detail_of(raw)
    assert detail["position"] == caught.value.pos
    assert detail["reason"] == "MISSING_COLON"
    assert detail["parser_error_type"] == "JSON_DECODE_ERROR"
    structure = detail["expected_structure"]
    assert structure["kind"] == "OBJECT_MEMBER"
    assert structure["expected"] == "COLON_THEN_VALUE"
    assert structure["after_key"] == "name"
    assert structure["at_end"] is False
    assert "冒号" in detail["repair"]


@pytest.mark.parametrize("name", ARCHIVED)
def test_the_repair_never_repeats_the_model_text_or_a_value(name):
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    serialized = json.dumps(detail_of(raw), ensure_ascii=False)
    assert "测试包裹" not in serialized  # no literal from the rejected output
    assert "value" not in serialized  # no token that follows the broken member
    assert "name" in serialized  # only the schema-style key name is echoed
    assert len(json.dumps(detail_of(raw))) < 700


def test_a_valid_document_still_parses_and_lenient_shapes_are_still_refused():
    assert bounded_json('{"location":"body","name":"测试包裹B","value":null}', 5000) == {
        "location": "body", "name": "测试包裹B", "value": None}
    for raw in ('{"name" "value"}', '{"a":1,}', "{'a':1}", '{"a":1,,"b":2}', '{"a":01}'):
        with pytest.raises(Exception) as caught:
            bounded_json(raw, 5000)
        assert caught.value.diagnostic.code == "JSON_SYNTAX"
    with pytest.raises(Exception) as caught:
        bounded_json('{"x":1,"x":2}', 5000)
    assert caught.value.diagnostic.code == "JSON_DUPLICATE_KEY"


@pytest.mark.parametrize("raw,kind,expected,at_end", [
    ('{"a":1,}', "MEMBER_LIST", "NO_TRAILING_COMMA", False),
    ('{"a" 1}', "OBJECT_MEMBER", "COLON_THEN_VALUE", False),
    ('{"a":}', "MEMBER_VALUE", "JSON_VALUE", False),
    ('{"a":"x}', "STRING_LITERAL", "CLOSING_QUOTE", False),
    ('{"a":1} {"b":2}', "DOCUMENT", "SINGLE_ROOT_OBJECT", False),
    ('{"a":1', "MEMBER_SEPARATOR", "COMMA", True),
])
def test_each_closed_reason_maps_to_one_structural_fact(raw, kind, expected, at_end):
    detail = detail_of(raw)
    assert detail["expected_structure"]["kind"] == kind
    assert detail["expected_structure"]["expected"] == expected
    assert detail["expected_structure"]["at_end"] is at_end
    assert detail["repair"]


def test_the_key_is_only_named_when_the_offset_proves_a_key_position():
    # A quoted string that is not a schema-style identifier is never echoed.
    assert detail_of('{"a b c" 1}')["expected_structure"].get("after_key") is None
    assert detail_of('{"' + "k" * 41 + '" 1}')["expected_structure"].get("after_key") is None
    # At a real member boundary the key is named, which is what makes the repair actionable.
    assert detail_of('{"meta":{"name" 1}}')["expected_structure"]["after_key"] == "name"


def test_the_design_hint_points_at_the_new_fields():
    hint = AgentSession._repair_hint("JSON_SYNTAX")
    assert "expected_structure" in hint and "repair" in hint
    assert "position" in hint and "line" in hint and "column" in hint
