import copy

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.product_sources import compact_sources, decode_source_refs, input_sources


def test_shared_design_sources_preserve_values_authority_and_original_validation_context():
    quote = "创建后读取和删除，并检查缺少数量。"
    item = dict(operation_id="create", value=dict(object=[dict(key="source_quote", value="数据")]),
                source_quote=quote)
    context = dict(input_requirements=[item], requested_checks=[dict(policy_id="approved",
        source_quote=quote)], previous_result=dict(input_requirements=[item]),
        policies=[dict(id="approved", source_quote="实际业务属性不能改写")])
    original = copy.deepcopy(context)
    public = compact_sources(context)
    assert context == original
    assert public["source_texts"] == dict(source_1=quote)
    assert public["input_requirements"][0]["source_ref"] == "source_1"
    assert public["requested_checks"][0] == dict(policy_id="approved", source_ref="source_1")
    assert public["previous_result"]["input_requirements"][0]["value"] == item["value"]
    assert public["policies"] == context["policies"]


def test_references_resolve_exact_operator_text_including_punctuation_and_prior_proof():
    current = '名称为“A。B”，数量2；标签依次a、b。'
    sources = input_sources(dict(user_task=current, previous_input_requirements=[
        dict(source_quote="周二")]))
    assert sources[0]["text"] == current and sources[1]["text"] == "周二"
    assert sources[1]["origin"] == "previous_validated"
    wire = dict(input_requirements=[dict(slot_id="approved_slot", value="A。B",
        source_ref="source_1")], input_questions=[], requested_checks=[])
    decoded = copy.deepcopy(wire)
    decode_source_refs(decoded, sources)
    assert decoded["input_requirements"][0]["source_quote"] == current
    for changes in (dict(source_ref="unknown"), dict(source_quote="模型自行改写的文字"),
                    dict(source_ref=1)):
        invalid = copy.deepcopy(wire)
        invalid["input_requirements"][0].update(changes)
        with pytest.raises(ValidationFailure, match="FOCUS_SOURCE_REFERENCE_INVALID"):
            decode_source_refs(invalid, sources)


def test_long_operator_input_keeps_every_character_in_source_windows_without_silent_truncation():
    current = "甲" * 315 + "乙" * 320 + "。末尾仍需检查。"
    sources = input_sources(dict(user_task=current))
    assert any("末尾仍需检查" in source["text"] for source in sources)
    assert all(0 < len(source["text"]) <= 300 and source["text"] in current for source in sources)
    assert all(any(current[start:start + 10] in source["text"] for source in sources)
               for start in range(len(current) - 9))


def test_repeated_rule_sources_are_shared_without_dropping_rules_or_changing_authority():
    basis = "WEB_OPERATOR_CONFIRMED:unchanged-policy-source"
    context = dict(confirmed_rules=[dict(policy_id=f"rule{i}", source=basis,
        status=dict(codes=[422])) for i in range(50)])
    original = copy.deepcopy(context)
    public = compact_sources(context)
    assert context == original
    assert len(public["confirmed_rules"]) == 50
    assert public["rule_sources"] == dict(rule_1=basis)
    assert all(rule["source_ref"] == "rule_1" for rule in public["confirmed_rules"])
    assert [rule["policy_id"] for rule in public["confirmed_rules"]] == [
        rule["policy_id"] for rule in original["confirmed_rules"]]
    assert all(public["rule_sources"][rule["source_ref"]] ==
               original["confirmed_rules"][i]["source"]
               for i, rule in enumerate(public["confirmed_rules"]))
