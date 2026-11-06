import copy

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.product_sources import (
    compact_sources,
    constrain_input_sources,
    decode_source_refs,
    input_sources,
    literal_texts,
)


def test_compaction_preserves_nonempty_execution_authority_and_original_defaults():
    original = dict(policies=[dict(id="read", required_bindings={}, poll_allowed=False),
        dict(id="wait", required_bindings=dict(poll=True), poll_allowed=True)])
    before = copy.deepcopy(original)
    public = compact_sources(original)
    assert public["policies"] == [dict(id="read"),
        dict(id="wait", required_bindings=dict(poll=True), poll_allowed=True)]
    assert original == before


def test_quoted_text_references_copy_exact_punctuation_and_reject_wrong_source():
    sources = input_sources(dict(user_task='创建“春季。资料 v2”',
        previous_input_requirements=[dict(source_quote='先前“另一资料”')]))
    assert literal_texts(sources) == dict(text_1="春季。资料 v2", text_2="另一资料")
    wire = dict(input_requirements=[dict(slot_id="field", value=dict(text_ref="text_1"),
        source_ref="source_1")])
    decode_source_refs(wire, sources)
    assert wire["input_requirements"][0]["value"] == "春季。资料 v2"
    for value in (dict(text_ref="text_2"), dict(text_ref="unknown"),
                  dict(text_ref="text_1", fallback="changed")):
        with pytest.raises(ValidationFailure, match="FOCUS_INPUT_SOURCE_INVALID"):
            decode_source_refs(dict(input_requirements=[dict(value=value,
                source_ref="source_1")]), sources)


def test_quoted_path_reference_schema_keeps_the_source_and_field_together():
    from jsonschema import Draft202012Validator

    from agenttest.request_requirements import request_atoms

    quote = '查 owner="team"、repo="register"；用另一个身份查同一仓库。'
    context = dict(user_task=quote, request_atoms=request_atoms(quote))
    fields = [dict(operation_id="read", location="path_parameters", name=name, path=[],
                   schema=dict(type="string")) for name in ("owner", "repo")]
    branches = [dict(type="object", additionalProperties=False,
        properties=dict(field_ref=dict(enum=["path_parameters/" + field["name"]]),
                        source_quote=dict(type="string"), value=dict(type="string")),
        required=["field_ref", "source_quote", "value"]) for field in fields]
    schema = dict(input_requirements=dict(type="array", items=dict(anyOf=branches)),
                  input_questions=dict(type="array"))
    constrain_input_sources(schema, context, "read", fields)
    validator = Draft202012Validator(schema["input_requirements"])
    entry = dict(field_ref="path_parameters/repo", source_ref="source_2",
                 value=dict(text_ref="text_2"))
    assert validator.is_valid([entry])
    assert not validator.is_valid([{**entry, "source_ref": "source_3"}])
    assert not validator.is_valid([{**entry, "value": dict(text_ref="text_1")}])


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


def test_numeric_default_without_matching_provenance_is_closed_before_generation():
    import json

    from jsonschema import Draft202012Validator
    from test_product_design import settings

    from agenttest.product_focus import TestFocusModel

    context = dict(user_task="创建本轮资源，验证删除返回409。", approved_ids=["create"],
        unavailable_operations=[], input_fields=[dict(operation_id="create", location="body",
            name="reviewer", path=[], schema=dict(type="integer", enum=[9]))])
    model = TestFocusModel(settings(), profile="generic")
    wire = dict(interpretation="按已确认配置准备资源并检查删除限制。", scope=dict(mode="selected",
        operations=dict(create=dict(input_requirements=[], input_questions=[], requested_checks=[],
            expectation_questions=[], test_normal=True)), question=None),
        blocked_operations={})
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    validator = Draft202012Validator(schema)
    assert validator.is_valid(wire)
    wire["scope"]["operations"]["create"]["input_requirements"] = [dict(
        field_ref="body/reviewer", value=9, source_ref="source_1")]
    assert not validator.is_valid(wire)
    with pytest.raises(ValidationFailure, match="FOCUS_INPUT_NUMBER_CHANGED"):
        model._decode_output(json.dumps(wire), context)
    context["user_task"] = "使用审核员9创建本轮资源，验证删除返回409。"
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    assert Draft202012Validator(schema).is_valid(wire)


def test_singleton_input_does_not_offer_an_impossible_two_choice_question():
    import json

    from jsonschema import Draft202012Validator
    from test_product_design import settings

    from agenttest.product_focus import TestFocusModel

    context = dict(user_task="提交已确认的本轮内容。", approved_ids=["submit"],
        unavailable_operations=[], input_fields=[dict(operation_id="submit", location="body",
            name="text", path=[], schema=dict(type="string", enum=["fixed synthetic text"]))])
    wire = dict(interpretation="使用已确认内容提交本轮资源。", scope=dict(mode="selected",
        operations=dict(submit=dict(input_requirements=[], input_questions=[], requested_checks=[],
            expectation_questions=[], test_normal=True)), question=None),
        blocked_operations={})
    model = TestFocusModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    validator = Draft202012Validator(schema)
    assert validator.is_valid(wire)
    wire["scope"]["operations"]["submit"]["input_questions"] = [dict(
        field_ref="body/text", source_ref="source_1", question="选择哪一项内容？",
        choices=["fixed synthetic text", "fixed synthetic text"])]
    assert not validator.is_valid(wire)


@pytest.mark.parametrize("kind,quote,valid,invalid", [
    ("integer", "核对返回一项、排名为1及健康状态。", 1, 20),
    ("number", "检查阈值2.5。", 2.5, 3),
])
def test_numeric_generation_is_bound_to_its_exact_source_before_provider_call(
        kind, quote, valid, invalid):
    import json

    from jsonschema import Draft202012Validator
    from test_product_design import settings

    from agenttest.product_focus import TestFocusModel

    context = dict(user_task=quote, approved_ids=["read"], unavailable_operations=[],
        input_fields=[dict(operation_id="read", location="query", name="amount", path=[],
                           schema=dict(type=kind, minimum=1, maximum=100))])
    model = TestFocusModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    validator = Draft202012Validator(schema)
    entry = dict(field_ref="query/amount", value=valid, source_ref="source_1")
    wire = dict(interpretation="核对明确请求的读取内容。", scope=dict(mode="selected",
        operations=dict(read=dict(input_requirements=[entry], input_questions=[],
            requested_checks=[], expectation_questions=[], test_normal=True)), question=None),
        blocked_operations={})
    assert validator.is_valid(wire)
    entry["value"] = invalid
    assert not validator.is_valid(wire)
    # A provider ignoring the schema still meets the unchanged provenance gate.
    with pytest.raises(ValidationFailure, match="FOCUS_INPUT_NUMBER_CHANGED"):
        model._decode_output(json.dumps(wire), context)
