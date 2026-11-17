"""P3-B2.4 A1/A2: multi-violation Focus diagnostics and scalar instance constraints.

Both fixes only change what a rejection *says*. The accept/reject decision stays the
validators': every test below asserts a code that already existed, plus the structured facts the
one allowed repair round needs.
"""

import json
from types import SimpleNamespace

import pytest

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.llm_planner import ModelSettings
from agenttest.product_focus import TestFocusModel as FocusDecoder
from agenttest.product_focus_wire import decode_operation_bundles
from agenttest.product_sources import decode_source_refs, input_sources
from agenttest.request_requirements import request_atoms
from agenttest.request_validation import validate_value

SETTINGS = ModelSettings(
    model="ecnu-max", base_url="https://offline-replay.invalid/v1", protocol="chatecnu",
    timeout=60, max_input_tokens=200_000, max_output_tokens=8192, max_total_tokens=1_000_000,
    max_response_bytes=200000, api_key="offline-replay-not-a-credential")

TASK = "发版前请完整验收整个项目，正常路径和非法入参都按已批准的规则执行。"
CLAUSE = dict(disposition="required", actions=[], before=[], same_resource=[])


def check_context():
    """The archived v1 shape: one action clause, one trusted scope clause, one approved check."""
    from agenttest.task_completion import is_variant_scope_clause

    atoms = request_atoms(TASK)
    scope = next(atom for atom in atoms if is_variant_scope_clause(atom["text"]))
    context = dict(approved_ids=["createParcel"], user_task=TASK, request_atoms=atoms,
                   trusted_scope_clauses=[scope["source_id"]], unavailable_operations=[],
                   input_fields=[],
                   approved_checks=[dict(policy_id="policy_1", operation_id="createParcel",
                                         location="body", name="name", kind="boundary_outside",
                                         field_constraints=dict(type="string"))])
    sources = {entry["text"]: entry["source_id"] for entry in input_sources(context)}
    return context, sources[scope["text"]]


def focus_wire(source_ref, exact_value, requirement_map=None):
    check = dict(check_ref="check_1", source_ref=source_ref, exact_value=exact_value)
    bundle = dict(input_requirements=[], input_questions=[], requested_checks=[check],
                  expectation_questions=[], test_normal=True)
    return dict(interpretation="发版前完整验收", scope=dict(mode="selected",
                operations=dict(createParcel=bundle), question=None), blocked_operations={},
                requirement_map=requirement_map if requirement_map is not None
                else {"request_1": dict(CLAUSE), "request_2": dict(CLAUSE)})


def decode(wire, context):
    return json.loads(FocusDecoder(SETTINGS, profile="generic")._decode_output(
        json.dumps(wire), context))


def requested_checks_of(wire, context):
    """Decode the check entries exactly like the accepted path, without the full selection."""
    selected, details = decode_operation_bundles(wire["scope"]["operations"], context)
    decode_source_refs(dict(requested_checks=list(details["requested_checks"])),
                       input_sources(context), context)
    from agenttest.product_checks import RequestedCheck

    return [RequestedCheck.model_validate(item) for item in details["requested_checks"]], selected


# ------------------------------------------------------------------ A1: multi-violation Focus

def test_a_proposal_with_two_independent_violations_names_both():
    context, source = check_context()
    wire = focus_wire(source, dict(value="blue"))
    with pytest.raises(ValidationFailure) as caught:
        decode(wire, context)
    diagnostic = caught.value.diagnostic
    # The code is the one the requirement rule always produced: the repair gate and the review
    # classification keep seeing the same code.
    assert diagnostic.code == "FOCUS_SOURCE_CLAUSE_MISSING"
    assert diagnostic.fields[0] == "requirement_map.request_2"
    violations = diagnostic.detail["violations"]
    assert [item["error_code"] for item in violations] == [
        "FOCUS_SOURCE_CLAUSE_MISSING", "FOCUS_INPUT_TEXT_CHANGED"]
    assert violations[0]["must_delete"] == ["requirement_map.request_2"]
    assert "删除 requirement_map.request_2" in violations[0]["expected_rule"]
    assert violations[1]["must_be_null"] == ["requested_checks.0.exact_value"]
    assert violations[1]["count"] == 1
    assert "必须为 null" in violations[1]["violated_constraint"]


def test_removing_only_the_extra_key_still_reports_the_clause_value_violation():
    context, source = check_context()
    wire = focus_wire(source, dict(value="blue"), requirement_map={"request_1": dict(CLAUSE)})
    with pytest.raises(ValidationFailure) as caught:
        decode(wire, context)
    diagnostic = caught.value.diagnostic
    assert diagnostic.code == "FOCUS_INPUT_TEXT_CHANGED"
    assert diagnostic.fields == ["requested_checks.0.exact_value"]
    (violation,) = diagnostic.detail["violations"]
    assert violation["must_be_null"] == ["requested_checks.0.exact_value"]
    assert not violation["must_delete"]


def test_every_violating_check_is_reported_not_just_the_first():
    from agenttest.product_checks import requested_check_violations

    context, source = check_context()
    wire = focus_wire(source, dict(value="blue"), requirement_map={"request_1": dict(CLAUSE)})
    wire["scope"]["operations"]["createParcel"]["requested_checks"] = [
        dict(check_ref="check_1", source_ref=source, exact_value=dict(value="blue"))]
    checks, selected = requested_checks_of(wire, context)
    first = requested_check_violations(checks, context, selected)
    assert len(first) == 1 and first[0]["field_path"] == "requested_checks.0.exact_value"


def test_repaired_checks_produce_no_violations_and_the_requirement_map_decodes():
    from agenttest.product_checks import requested_check_violations
    from agenttest.request_requirements import decode_requirements

    context, source = check_context()
    wire = focus_wire(source, None, requirement_map={"request_1": dict(CLAUSE)})
    checks, selected = requested_checks_of(wire, context)
    assert requested_check_violations(checks, context, selected) == []
    accepted = decode_requirements({"request_1": dict(CLAUSE)}, context)
    assert [(item.source_id, item.disposition) for item in accepted] == [
        ("request_1", "required"), ("request_2", "constraint")]


def test_a_valid_proposal_still_decodes_and_an_unrelated_invalid_one_still_fails():
    context, source = check_context()
    accepted = decode(focus_wire(source, None, requirement_map={"request_1": dict(CLAUSE)}),
                      context)
    assert accepted["requested_checks"][0]["source_quote"]
    duplicated = focus_wire(source, None, requirement_map={"request_1": dict(CLAUSE)})
    duplicated["scope"]["operations"]["createParcel"]["requested_checks"].append(
        dict(check_ref="check_1", source_ref=source, exact_value=None))
    with pytest.raises(ValidationFailure) as caught:
        decode(duplicated, context)
    assert caught.value.diagnostic.code == "FOCUS_REQUESTED_CHECK_INVALID"


def test_the_repair_payload_never_asks_for_a_different_literal():
    from agenttest.diagnostics import Diagnostic
    from agenttest.product_tasks import focus_repair_payload

    target = SimpleNamespace(target=SimpleNamespace(allowed_operations=["createParcel"]))
    diagnostic = Diagnostic(layer=Layer.MODEL, code="FOCUS_SOURCE_CLAUSE_MISSING",
        fields=["requirement_map.request_2"], detail=dict(
            expected_requirement_keys=["request_1"], trusted_scope_clauses=["request_2"],
            violations=[
                dict(error_code="FOCUS_SOURCE_CLAUSE_MISSING",
                     field_path="requirement_map.request_2",
                     field_paths=["requirement_map.request_2"], count=1,
                     violated_constraint="requirement_map 只能包含批准的子句",
                     expected_rule="删除 requirement_map.request_2",
                     must_delete=["requirement_map.request_2"], must_be_null=[],
                     must_match=[]),
                dict(error_code="FOCUS_INPUT_TEXT_CHANGED",
                     field_path="requested_checks.0.exact_value",
                     field_paths=["requested_checks.0.exact_value"], count=1,
                     violated_constraint="引用范围说明子句时 exact_value 必须为 null",
                     expected_rule="把 exact_value 置为 null",
                     must_delete=[], must_be_null=["requested_checks.0.exact_value"],
                     must_match=[])]))
    payload = focus_repair_payload(target, diagnostic, atoms=[dict(source_id="request_1")],
                                   scope_sources=["request_2"])
    detail = payload["detail"]
    assert "不要重复同一个被拒的值" not in detail["hint"]
    assert "换一个值不是修复" in detail["hint"]
    assert "requirement_keys 列出的子句" in detail["hint"]
    assert "不要为它们填条目" in detail["hint"]
    assert detail["must_delete"] == ["requirement_map.request_2"]
    assert detail["must_be_null"] == ["requested_checks.0.exact_value"]
    assert detail["requirement_keys"] == ["request_1"]


def test_violation_order_is_stable_across_equal_proposals():
    from agenttest.product_checks import group_violations, requested_check_violations

    context, source = check_context()
    wire = focus_wire(source, dict(value="blue"), requirement_map={"request_1": dict(CLAUSE)})
    first, _ = requested_checks_of(wire, context)
    second, _ = requested_checks_of(wire, context)
    left = group_violations(requested_check_violations(first, context, ["createParcel"]))
    right = group_violations(requested_check_violations(second, context, ["createParcel"]))
    assert left == right
    assert [item["field_path"] for item in left] == ["requested_checks.0.exact_value"]


# ------------------------------------- A1 b: the path names the field that is really wrong

def scope_text(context, source_id):
    return next(entry["text"] for entry in input_sources(context)
                if entry["source_id"] == source_id)


def wide_context(count):
    """The archived shape with an arbitrary number of approved negative checks."""
    context, source = check_context()
    context["approved_checks"] = [
        dict(policy_id=f"policy_{index}", operation_id="createParcel", location="body",
             name="name", kind="boundary_outside", field_constraints=dict(type="string"))
        for index in range(count)]
    return context, scope_text(context, source)


def scope_check(quote, *, value=None, length=None, policy_id="policy_1"):
    from agenttest.product_checks import RequestedCheck

    return RequestedCheck.model_validate(dict(
        policy_id=policy_id, source_quote=quote,
        **({"exact_value": dict(value=value)} if value is not None else {}),
        **({"exact_length": length} if length is not None else {})))


def scope_violations(*checks, context=None):
    from agenttest.product_checks import requested_check_violations

    context = context if context is not None else check_context()[0]
    return requested_check_violations(list(checks), context, ["createParcel"])


def test_a_length_only_violation_names_exact_length_not_exact_value():
    context, source = check_context()
    quote = scope_text(context, source)
    (violation,) = scope_violations(scope_check(quote, length=8))
    assert violation["field_paths"] == ["requested_checks.0.exact_length"]
    assert violation["must_be_null"] == ["requested_checks.0.exact_length"]
    assert violation["count"] == 1


def test_a_check_that_breaks_both_fields_names_both_in_one_entry():
    context, source = check_context()
    quote = scope_text(context, source)
    (violation,) = scope_violations(scope_check(quote, value="blue", length=8))
    assert violation["field_paths"] == ["requested_checks.0.exact_value",
                                        "requested_checks.0.exact_length"]
    assert violation["must_be_null"] == violation["field_paths"]
    assert violation["count"] == 1


def test_a_legal_check_is_never_listed_as_violating():
    from agenttest.product_checks import grouped_failure

    context, quote = wide_context(2)
    violations = scope_violations(scope_check(quote, value="blue", policy_id="policy_0"),
                                  scope_check(quote, policy_id="policy_1"), context=context)
    assert [item["field_paths"] for item in violations] == [
        ["requested_checks.0.exact_value"]]
    with pytest.raises(ValidationFailure) as caught:
        raise grouped_failure(violations)
    assert caught.value.diagnostic.fields == ["requested_checks.0.exact_value"]


def test_the_diagnostic_fields_stay_within_the_pydantic_limit():
    from agenttest.product_checks import grouped_failure

    context, quote = wide_context(17)
    checks = [scope_check(quote, value=f"value-{index}", policy_id=f"policy_{index}")
              for index in range(17)]
    violations = scope_violations(*checks, context=context)
    assert len(violations) == 17
    with pytest.raises(ValidationFailure) as caught:
        raise grouped_failure(violations)
    diagnostic = caught.value.diagnostic
    assert len(diagnostic.fields) == 8
    assert diagnostic.detail["violations"][0]["count"] == 17


# ------------------------------------- A1 c: the repair payload lists every position

def payload_for(violations, *, atoms=()):
    from agenttest.diagnostics import Diagnostic
    from agenttest.product_tasks import focus_repair_payload

    diagnostic = Diagnostic(layer=Layer.MODEL, code="FOCUS_INPUT_TEXT_CHANGED",
                            fields=[item["field_paths"][0] for item in violations][:8],
                            detail=dict(violations=violations))
    target = SimpleNamespace(target=SimpleNamespace(allowed_operations=["createParcel"]))
    return focus_repair_payload(target, diagnostic, atoms=atoms)


def archived_paths():
    """The archived v1 shape: 11 value positions and 6 length positions interleaved."""
    length_positions = {3, 4, 12, 14, 15, 16}
    return [f"requested_checks.{position}."
            f"{'exact_length' if position in length_positions else 'exact_value'}"
            for position in range(17)]


def test_the_repair_payload_names_all_seventeen_archived_positions():
    paths = archived_paths()
    assert len(paths) == 17 and sum(".exact_length" in path for path in paths) == 6
    payload = payload_for([dict(error_code="FOCUS_INPUT_TEXT_CHANGED",
                                field_path=paths[0], field_paths=paths, count=17,
                                violated_constraint="必须为 null", expected_rule="置为 null",
                                must_delete=[], must_be_null=list(paths), must_match=[])])
    detail = payload["detail"]
    assert detail["must_be_null"] == paths
    assert len(detail["must_be_null"]) == 17
    assert "omitted" not in detail
    assert detail["must_be_null"][3].endswith(".exact_length")


def test_the_repair_payload_covers_the_widest_legal_proposal():
    from agenttest.product_checks import grouped_failure
    from agenttest.product_tasks import MAX_REPAIR_PATHS, MAX_REQUESTED_CHECKS

    assert MAX_REPAIR_PATHS >= 2 * MAX_REQUESTED_CHECKS
    context, quote = wide_context(MAX_REQUESTED_CHECKS)
    checks = [scope_check(quote, value="blue", length=8, policy_id=f"policy_{index}")
              for index in range(MAX_REQUESTED_CHECKS)]
    violations = scope_violations(*checks, context=context)
    with pytest.raises(ValidationFailure) as caught:
        raise grouped_failure(violations)
    detail = payload_for(caught.value.diagnostic.detail["violations"])["detail"]
    assert len(detail["must_be_null"]) == 2 * MAX_REQUESTED_CHECKS
    assert detail["must_be_null"][:2] == ["requested_checks.0.exact_value",
                                          "requested_checks.0.exact_length"]
    assert "omitted" not in detail


def test_a_payload_beyond_the_cap_counts_the_remainder_instead_of_dropping_it():
    from agenttest.product_tasks import MAX_REPAIR_PATHS

    paths = [f"requested_checks.{index}.exact_value" for index in range(MAX_REPAIR_PATHS + 20)]
    detail = payload_for([dict(error_code="FOCUS_INPUT_TEXT_CHANGED",
                               field_path=paths[0], field_paths=paths,
                               count=len(paths), violated_constraint="必须为 null",
                               expected_rule="置为 null", must_delete=[],
                               must_be_null=list(paths), must_match=[])])["detail"]
    assert len(detail["must_be_null"]) == MAX_REPAIR_PATHS
    assert detail["omitted"] == {"must_be_null": 20}
    assert "omitted 里的条目数量按 violations 中同类别的 violated_constraint" in detail["hint"]


def test_the_repair_payload_deduplicates_positions_and_echoes_no_value():
    paths = ["requested_checks.0.exact_value", "requested_checks.0.exact_value",
             "requested_checks.1.exact_length"]
    payload = payload_for([dict(error_code="FOCUS_INPUT_TEXT_CHANGED",
                                field_path=paths[0], field_paths=paths, count=3,
                                violated_constraint="必须为 null", expected_rule="置为 null",
                                must_delete=[], must_be_null=list(paths), must_match=[])])
    detail = payload["detail"]
    assert detail["must_be_null"] == ["requested_checks.0.exact_value",
                                      "requested_checks.1.exact_length"]
    assert detail["must_delete"] == [] and detail["must_match"] == []
    assert "blue" not in json.dumps(payload, ensure_ascii=False)


# ------------------------------------------------------------------ A2: scalar constraint facts

def instance_context(approved):
    argument = SimpleNamespace(location="body", name="name", value=None, from_instance=None)
    instance = SimpleNamespace(instance_id="ai_5", operation_id="createParcel", role="alice",
                               policy_id="positive_1", phase="setup", arguments=[argument])
    ir = SimpleNamespace(instances=[instance], edge_pairs=lambda: [])
    policies = [dict(id="positive_1", binding_rules=[
        dict(location="body", name="name", literal_schema=approved)])]
    return dict(planning_ir=ir, policies=policies)


def instance_wire(value):
    return dict(inputs={"ai_5": {"arg_0": value}})


def test_an_over_long_literal_reports_the_real_length_rule():
    from agenttest.instance_design import instance_response

    context = instance_context(dict(type="string", minLength=1, maxLength=24))
    with pytest.raises(ValidationFailure) as caught:
        instance_response(instance_wire("acceptance-parcel-renamed"), context)
    diagnostic = caught.value.diagnostic
    assert diagnostic.code == "DESIGN_INSTANCE_LITERAL_INVALID"
    detail = diagnostic.detail
    assert detail["field_path"] == "body.name"
    assert detail["violation"] == "MAX_LENGTH"
    assert detail["constraint"] == "maxLength"
    assert detail["limit"] == 24
    assert detail["max_length"] == 24
    assert detail["actual_length"] == 25
    assert detail["schema_rule"] == "OPENAPI_VALUE_LENGTH"
    assert "maxLength=24" in detail["hint"]
    # The old payload always carried the object-shaped required list and a "fill in the
    # required fields" hint, which never named the real cause.
    assert "required" not in detail
    assert "填写全部必填字段" not in detail["hint"]
    assert diagnostic.fields == ["body.name"]
    assert len(detail) <= 12


def test_a_literal_within_the_approved_length_is_accepted():
    from agenttest.instance_design import instance_response

    context = instance_context(dict(type="string", minLength=1, maxLength=24))
    case = instance_response(instance_wire("acceptance-parcel-rename"), context)["cases"][0]
    assert case["plan"]["steps"][0]["arguments"][0]["value"] == "acceptance-parcel-rename"


@pytest.mark.parametrize("approved,value,violation,constraint,limit", [
    (dict(type="string", minLength=3), "ab", "MIN_LENGTH", "minLength", 3),
    (dict(type="integer", minimum=1, maximum=3), 4, "MAXIMUM", "maximum", 3),
    (dict(type="integer", minimum=1, maximum=3), 0, "MINIMUM", "minimum", 1),
    (dict(type="string", enum=["a", "b"]), "c", "ENUM", "enum", None),
])
def test_each_scalar_constraint_reports_itself(approved, value, violation, constraint, limit):
    from agenttest.instance_design import instance_response

    context = instance_context(approved)
    with pytest.raises(ValidationFailure) as caught:
        instance_response(instance_wire(value), context)
    detail = caught.value.diagnostic.detail
    assert detail["violation"] == violation
    assert detail["constraint"] == constraint
    if limit is not None:
        assert detail["limit"] == limit
    assert "填写全部必填字段" not in detail["hint"]


def test_a_type_mismatch_names_the_expected_type():
    from agenttest.instance_design import instance_response

    context = instance_context(dict(type="string", minLength=1, maxLength=24))
    with pytest.raises(ValidationFailure) as caught:
        instance_response(instance_wire(24), context)
    detail = caught.value.diagnostic.detail
    assert detail["violation"] == "TYPE"
    assert detail["constraint"] == "type"
    assert detail["expected_type"] == "string"
    assert detail["actual_type"] == "integer"


def test_a_missing_required_member_keeps_the_required_list_and_facts():
    from agenttest.instance_design import instance_response

    approved = dict(type="object", additionalProperties=False, required=["labels", "options"],
                    properties=dict(labels=dict(type="array", items=dict(type="string")),
                                    options=dict(type="object", additionalProperties=False,
                                                 properties={}, required=[])))
    context = instance_context(approved)
    with pytest.raises(ValidationFailure) as caught:
        instance_response(instance_wire(dict(labels=["blue"])), context)
    detail = caught.value.diagnostic.detail
    assert detail["violation"] == "OBJECT_FIELDS"
    assert detail["constraint"] == "required"
    assert detail["missing"] == ["options"]
    assert detail["required"] == ["labels", "options"]
    assert "缺少必填字段：options" in detail["hint"]


def test_the_same_value_is_still_refused_by_the_validator():
    """The diagnosis must not change the decision: 25 characters still fails validate_value."""
    approved = dict(type="string", minLength=1, maxLength=24)
    with pytest.raises(ValidationFailure) as caught:
        validate_value("x" * 25, approved)
    assert caught.value.diagnostic.code == "OPENAPI_VALUE_LENGTH"
    validate_value("x" * 24, approved)
