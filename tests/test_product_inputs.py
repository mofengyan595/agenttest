"""User values constrain design; provenance does not authorize an operation or assertion."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_product_design import case, project, settings

from agenttest.product_design import TestDesign as DesignOutput
from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_design import design_context, design_groups
from agenttest.product_inputs import (
    InputRequirement,
    focus_input_groups,
    input_fields,
    input_slot,
    validate_design_inputs,
    validate_requirements,
)


def test_focus_wire_keeps_boolean_and_number_types_and_groups_repeated_fields():
    from agenttest.product_focus import TestFocusModel

    fields = [dict(operation_id=operation, location="query", name=name, path=[], schema=schema)
        for operation in ("readA", "readB") for name, schema in (
            ("active", dict(type="boolean", enum=[True, False])),
            ("limit", dict(type="integer", minimum=1, maximum=3)))]
    assert len(focus_input_groups(fields)) == 2
    context = dict(approved_ids=["readA", "readB"], unavailable_operations=[],
                   input_fields=fields, user_task="active=true，limit=2")
    model = TestFocusModel(settings(), profile="generic")
    request = json.loads(model.prepare_request(context))
    public = json.loads(request["messages"][1]["content"])
    assert len(public["input_fields"]) == 2
    validator = Draft202012Validator(request["response_format"]["json_schema"]["schema"])
    proposal = dict(interpretation="检查指定目录参数", scope=dict(mode="selected",
        operations=dict(readB=None, readA=dict(input_questions=[], requested_checks=[],
            test_normal=False, existing_resource=False, expectation_questions=[],
            input_requirements=[dict(field_ref="query/active", value=True,
                                     source_ref="source_1")])),
        question=None), blocked_operations={})
    assert validator.is_valid(proposal)
    whole = copy.deepcopy(proposal)
    whole["scope"] = dict(mode="all_ready", operations={}, question=None)
    decoded = json.loads(model._decode_output(json.dumps(whole), context))
    assert decoded["mode"] == "all_ready" and decoded["input_requirements"] == []
    for change in (dict(value="true"), dict(value=1), dict(location="body"),
                   dict(name="unapproved"), dict(path=["nested"]), dict(field_ref="private")):
        invalid = copy.deepcopy(proposal)
        invalid["scope"]["operations"]["readA"]["input_requirements"][0].update(change)
        assert not validator.is_valid(invalid), change
    numeric = copy.deepcopy(proposal)
    numeric["scope"]["operations"]["readA"]["input_requirements"][0].update(
        field_ref="query/limit", value=2)
    assert validator.is_valid(numeric)
    numeric["scope"]["operations"]["readA"]["input_requirements"][0]["value"] = 0
    assert not validator.is_valid(numeric)


def test_followup_source_refs_are_field_and_value_specific():
    from agenttest.product_focus import TestFocusModel

    context = dict(user_task="本次标题明确为“竹林排班”，其余沿用。", approved_ids=["create"],
        input_fields=[dict(operation_id="create", location="body", name=name,
                           path=[], schema=schema)
            for name, schema in [("title", dict(type="string", minLength=1)),
                                 ("day", dict(type="string", enum=["Monday", "Tuesday"]))]],
        previous_input_requirements=[dict(operation_id="create", location="body", name="day",
            path=[], value="Tuesday", source_quote="标题可竹林排班或松林排班，周二预约")])
    model = TestFocusModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    title = dict(field_ref="body/title", value="竹林排班", source_ref="source_1")
    day = dict(field_ref="body/day", value="Tuesday", source_ref="source_2")
    wire = dict(interpretation="保留周二，使用本次明确选择的标题", scope=dict(mode="selected",
        operations=dict(create=dict(input_requirements=[title, day], input_questions=[],
            requested_checks=[], expectation_questions=[],
                  test_normal=False, existing_resource=False)),
        question=None),
        blocked_operations={})
    validator = Draft202012Validator(schema)
    assert validator.is_valid(wire)
    model._decode_output(json.dumps(wire), context)
    title["source_ref"] = "source_2"
    assert not validator.is_valid(wire)
    with pytest.raises(ValueError, match="FOCUS_INPUT_SOURCE_INVALID"):
        model._decode_output(json.dumps(wire), context)
    title["source_ref"] = "source_1"
    day["value"] = "Monday"
    assert not validator.is_valid(wire)


def test_followup_recovers_unextracted_old_data_but_not_an_unanswered_choice():
    from agenttest.product_focus import TestFocusModel

    old = "标题可竹林或松林，周二预约，数量2。"
    context = dict(user_task="本次选竹林，其余沿用。", approved_ids=["create"],
        previous_requests=[old], pending_input_questions=[dict(operation_id="create",
            location="body", name="title", path=[], choices=["竹林", "松林"])],
        previous_input_requirements=[dict(operation_id="create", location="body", name="day",
            path=[], value="Tuesday", source_quote=old)],
        input_fields=[dict(operation_id="create", location="body", name=name, path=[],
            schema=schema) for name, schema in [
                ("title", dict(type="string")), ("day", dict(type="string", enum=["Tuesday"])),
                ("count", dict(type="integer", minimum=1, maximum=3))]])
    model = TestFocusModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    wire = dict(interpretation="使用新标题，并从原始交代中恢复周二", scope=dict(mode="selected",
        operations=dict(create=dict(test_normal=True, existing_resource=False,
            input_requirements=[dict(field_ref="body/title", value="竹林", source_ref="source_1"),
                dict(field_ref="body/day", value="Tuesday", source_ref="source_2"),
                dict(field_ref="body/count", value=2, source_ref="source_2")],
            input_questions=[], requested_checks=[], expectation_questions=[])), question=None),
        blocked_operations={})
    assert Draft202012Validator(schema).is_valid(wire)
    parsed = json.loads(model._decode_output(json.dumps(wire), context))
    assert parsed["input_requirements"][1]["source_quote"] == old
    assert parsed["input_requirements"][2]["value"] == 2
    wire["scope"]["operations"]["create"]["input_requirements"][0]["source_ref"] = "source_2"
    assert not Draft202012Validator(schema).is_valid(wire)
    with pytest.raises(ValueError, match="FOCUS_INPUT_SOURCE_INVALID"):
        model._decode_output(json.dumps(wire), context)


def test_unanswered_choices_cannot_turn_into_generated_defaults():
    from agenttest.product_inputs import InputQuestion, unresolved_input_questions

    raw = dict(operation_id="create", location="body", name="title", path=[],
        question="标题选竹林还是松林？", choices=["竹林", "松林"], source_quote="竹林还是松林")
    question = InputQuestion.model_validate(raw)
    assert unresolved_input_questions([raw], [], [], "继续", {"create"}) == [question]
    answer = InputRequirement(operation_id="create", location="body", name="title", path=[],
                              value="竹林", source_quote="本次用竹林")
    assert not unresolved_input_questions([raw], [], [answer], "本次用竹林", {"create"})
    assert unresolved_input_questions([raw], [], [answer], "继续", {"create"}) == [question]
    assert not unresolved_input_questions([raw], [], [], "只查健康", {"health"})


def test_input_question_rejects_invented_choices_and_conflicting_requirements():
    from agenttest.product_inputs import InputQuestion, validate_input_questions

    proj, _ = project()
    context = dict(user_task="建立春季资料任务", input_fields=input_fields(proj))
    question = InputQuestion(operation_id="insertJob", location="body", name="name", path=[],
        source_quote="春季资料任务", question="名称应是春季资料，还是春季资料任务？",
        choices=["春季资料", "春季资料任务"])
    validate_input_questions([question], [], context, ["insertJob"])
    for changes in (dict(choices=["春季资料", "未指定名称"]), dict(name="password"),
                    dict(choices=["春季资料", "春季资料"]), dict(source_quote="旧请求")):
        with pytest.raises(ValueError):
            validate_input_questions([question.model_copy(update=changes)], [], context,
                                     ["insertJob"])
    with pytest.raises(ValueError, match="QUESTION_CONFLICT"):
        validate_input_questions([question], [requirement()], context, ["insertJob"])


def test_followup_can_reuse_only_the_same_previously_validated_value_and_current_schema():
    from agenttest.diagnostics import ValidationFailure

    proj, _ = project()
    previous = requirement("quantity", value=2, quote="数量2")
    array = requirement("details", ["labels"], {"array": ["a", "b"]}, "a和b")
    context = dict(user_task="名称明确为春季资料，其余沿用", input_fields=input_fields(proj),
                   previous_input_requirements=[previous.model_dump(), array.model_dump()])
    validate_requirements([previous, array, requirement()], context, ["insertJob"])
    for changed in (previous.model_copy(update={"value": 3}),
                    previous.model_copy(update={"source_quote": "数量3"})):
        with pytest.raises(ValidationFailure) as exc:
            validate_requirements([changed], context, ["insertJob"])
        assert exc.value.diagnostic.code == "FOCUS_INPUT_SOURCE_INVALID"


def requirement(name="name", path=None, value="春季资料", quote="春季资料"):
    return InputRequirement(operation_id="insertJob", location="body", name=name,
                            path=path or [], value=value, source_quote=quote)


def test_readable_input_slot_escapes_segments_and_never_confuses_nested_fields():
    first = dict(operation_id="read/A", location="body", name="details", path=["a/b"])
    second = dict(operation_id="read/A", location="body", name="details/a", path=["b"])
    assert input_slot(first) == "read~1A/body/details/a~1b"
    assert input_slot(first) != input_slot(second)


def test_quoted_values_require_approved_slots_types_and_original_free_text():
    proj, _ = project()
    context = dict(user_task="创建春季资料，数量2，标签依次a和b", input_fields=input_fields(proj))
    nested = requirement("details", ["labels"], {"array": ["a", "b"]}, "标签依次a和b")
    valid = [requirement(), requirement("quantity", value=2, quote="数量2"), nested]
    validate_requirements(valid, context, ["insertJob"])
    written = dict(context, user_task="数量两个")
    validate_requirements([requirement("quantity", value=2, quote="两个")], written, ["insertJob"])
    for invalid in (requirement(value="EnglishName"), requirement(quote="历史资料"),
                    requirement(name="password"),
                    requirement("quantity", value=True, quote="数量2"),
                    requirement("quantity", value=3, quote="数量2")):
        with pytest.raises(ValueError):
            validate_requirements([invalid], context, ["insertJob"])
    with pytest.raises(ValueError, match="FOCUS_INPUT_OPERATION_NOT_SELECTED"):
        validate_requirements(valid, context, ["readJob"])


def test_provenance_diagnostics_retain_only_closed_codes_and_numeric_locations():
    from agenttest.diagnostics import ValidationFailure

    proj, _ = project()
    context = dict(user_task="春季资料，数量2", input_fields=input_fields(proj))
    for item, code in [(requirement(value="CHANGED_VALUE"), "FOCUS_INPUT_TEXT_CHANGED"),
        (requirement("quantity", value=3, quote="数量2"), "FOCUS_INPUT_NUMBER_CHANGED")]:
        with pytest.raises(ValidationFailure) as error:
            validate_requirements([item], context, ["insertJob"])
        diagnostic = error.value.diagnostic.model_dump()
        assert diagnostic["code"] == code
        assert diagnostic["fields"][0] == "input_requirements.0.value"
        assert diagnostic["fields"][1].startswith("approved_input_fields.")
        assert "CHANGED_VALUE" not in json.dumps(diagnostic)


def test_explicit_text_and_nested_order_are_wire_constrained_and_independently_checked():
    proj, _ = project()
    requirements = [requirement(), requirement("details", ["labels"],
        {"array": ["a", "b"]}, "a和b")]
    context = design_context(proj, "创建春季资料，标签a和b", design_groups(proj)[0], 10, 2)
    context["input_requirements"] = [r.model_dump() for r in requirements]
    wire = json.loads(DesignModel(settings(), profile="generic").prepare_request(context))
    validator = Draft202012Validator(wire["response_format"]["json_schema"]["schema"])
    draft = case()
    args = draft["plan"]["steps"][0]["arguments"]
    args[0]["value"] = "春季资料"
    args[2]["value"] = {"object": [{"key": "labels", "value": {"array": ["a", "b"]}}]}
    proposal = dict(interpretation="创建用户指定的资料", relevant=True, cases=[draft],
                    questions=[], not_covered=[])
    assert validator.is_valid(proposal), list(validator.iter_errors(proposal))
    design = DesignOutput.model_validate(proposal)
    validate_design_inputs(proj, design, requirements)
    for index in (0, 2):
        changed = copy.deepcopy(proposal)
        changed["cases"][0]["plan"]["steps"][0]["arguments"][index]["value"] = (
            "TranslatedTitle" if index == 0 else
            {"object": [{"key": "labels", "value": {"array": ["b", "a"]}}]})
        assert not validator.is_valid(changed)
        with pytest.raises(ValueError, match="EXPLICIT_INPUT_NOT_PRESERVED"):
            validate_design_inputs(proj, DesignOutput.model_validate(changed), requirements)
    # An empty extraction is not an independent certificate of semantic completeness.
    validate_design_inputs(proj, DesignOutput.model_validate(proposal), [])
    extra = copy.deepcopy(proposal)
    additional = copy.deepcopy(draft)
    additional["plan"]["steps"][0]["arguments"][0]["value"] = "ExtraChangedName"
    extra["cases"].append(additional)
    with pytest.raises(ValueError, match="EXPLICIT_INPUT_NOT_PRESERVED"):
        validate_design_inputs(proj, DesignOutput.model_validate(extra), requirements)


def test_omissions_require_current_source_and_selected_primary_operation():
    proj, _ = project()
    context = design_context(proj, "创建任务并核验通知期限，不检查服务状态",
        design_groups(proj, operation_ids=["insertJob"])[0], 10, 2)
    model = DesignModel(settings(), profile="generic")
    proposal = dict(interpretation="通知期限需要确认", relevant=True, cases=[case()],
        questions=[], not_covered=[dict(operation_id="insertJob", reason="通知期限未确认",
                                       source_quote="通知期限")])
    decoded = json.loads(model._decode_output(json.dumps(proposal), context))
    assert decoded["not_covered"] == ["通知期限未确认"]
    for change in (dict(operation_id="readJob"), dict(source_quote="已经过期的旧需求")):
        invalid = copy.deepcopy(proposal)
        invalid["not_covered"][0].update(change)
        with pytest.raises(ValueError, match="OMISSION_OUTSIDE_REQUEST"):
            model._decode_output(json.dumps(invalid), context)


def test_explicit_existing_resource_cannot_be_substituted_by_new_producer_data():
    proj, _ = project()
    fields = input_fields(proj)
    field = next(f for f in fields if f["operation_id"] == "readJob")
    assert field["location"] == "path_parameters" and field["source_dependency"]
    requested = InputRequirement(operation_id="readJob", location="path_parameters",
        name="jobId", path=[], value="existing-job", source_quote="existing-job")
    validate_requirements([requested], dict(user_task="读取existing-job", input_fields=fields),
                          ["readJob"])
    draft = case()
    draft["plan"]["steps"].append(dict(id="read", operation_id="readJob", role=None,
        policy_id="web_rule_2", arguments=[dict(location="path_parameters", name="jobId",
            value=None, from_step="insert", select="$.id")]))
    design = DesignOutput(interpretation="新建资源后读取", cases=[draft],
                          questions=[], not_covered=[])
    with pytest.raises(ValueError, match="EXPLICIT_INPUT_REQUIRES_SOURCE_REVIEW"):
        validate_design_inputs(proj, design, [requested])


def test_normal_variants_need_real_positive_steps_not_negative_baseline_arguments():
    from test_negative_values import case as read_case
    from test_negative_values import policy, read_project

    proj, _ = read_project()
    base = next(p for p in proj.registry.definitions() if not p.negative)
    first, negative = read_case(base.id), read_case(policy(proj).id)
    negative["kind"] = "negative"
    negative["plan"]["steps"][0]["arguments"][0]["value"] = 2
    reqs = [InputRequirement(operation_id="readReports", location="query", name="limit", path=[],
        value=value, source_quote="正常limit=1与limit=2") for value in [1, 2]]
    design = DesignOutput.model_validate(dict(interpretation="第二个值只是未执行的正常基线",
        cases=[first, negative], questions=[], not_covered=[]))
    with pytest.raises(ValueError, match="DESIGN_EXPLICIT_INPUT_NOT_PRESERVED"):
        validate_design_inputs(proj, design, reqs, normal_operation_ids=["readReports"])
    second = read_case(base.id)
    second["plan"]["steps"][0]["arguments"][0]["value"] = 2
    design.cases.insert(1, DesignOutput.model_validate(dict(interpretation="第二个真实正常请求",
        cases=[second], questions=[], not_covered=[])).cases[0])
    validate_design_inputs(proj, design, reqs, normal_operation_ids=["readReports"])
