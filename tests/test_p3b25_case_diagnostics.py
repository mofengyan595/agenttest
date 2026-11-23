"""P3-B2.5: the case-level refusal must say which field was wrong and which are allowed.

P3-B2.4 rejected 31 cases whose design parsed fine; 27 of them invented a body field. The
precheck recorded the field path, the approved field names and the owning step/obligation, but
``error`` carried only the code, so the model resubmitted the same shape. These tests pin both
halves: the facts now travel, and nothing that is not on the whitelist does.
"""

import json
from pathlib import Path

import pytest
from test_product_agent import session

from agenttest.case_diagnostics import (
    CODE_HINTS,
    HINTS,
    case_failure,
    facts,
    project_cases,
    public_diagnostic,
    rejection_error,
    repair_kind,
)

ARCHIVE = json.loads((Path(__file__).parent / "fixtures" / "p3b25" / "cases-archive.json")
                     .read_text(encoding="utf-8"))


def archived(round_name, index=0):
    return json.loads(json.dumps(ARCHIVE[round_name][index]))  # a fresh copy per test


def refusal(cases, retryable=True):
    return rejection_error(cases, retryable=retryable,
                           message="方案未通过执行前校验，未发送业务请求。")


# ------------------------------------------------------------------ the facts a repair needs


def test_an_archived_unknown_body_field_names_the_allowed_fields_and_the_position():
    case = archived("release-v5", 0)
    error = refusal([case])
    assert error["code"] == "OPENAPI_UNKNOWN_BODY_FIELD" == case["reason"]
    row, = error["cases"]
    assert row["case_id"] == "negative_batch_6_case_1"
    assert row["repair_kind"] == "UNKNOWN_FIELD"
    assert row["fields"] == ["plan.steps.0.arguments.1.name"]
    assert row["step_id"] == "create_for_name_too_long"
    assert row["obligation_id"] == "web_rule_3"
    assert row["allowed_body_fields"] == ["meta", "name", "quantity"]
    assert row["unknown_body_positions"] == [0]
    assert row["policy_ids"] == ["web_rule_3", "web_rule_7_negative_4"]
    assert "hint" not in row  # stated once at the top instead of repeated per case
    assert "allowed_body_fields" in error["hint"] and "unknown_body_positions" in error["hint"]


def test_every_rejected_case_keeps_its_identity_and_its_own_obligation():
    cases = ARCHIVE["release-v5"]
    error = refusal(cases)
    assert [row["case_id"] for row in error["cases"]] == [case["case_id"] for case in cases]
    assert [row["step_id"] for row in error["cases"]] == ["create_for_name_too_long",
                                                          "create_for_qty_missing"]
    assert [row["obligation_id"] for row in error["cases"]] == ["web_rule_3", "web_rule_3"]
    # Two identical codes: the shared hint is stated once, so nothing is repeated per case.
    assert "hint" not in error["cases"][0] and "hint" not in error["cases"][1]


def test_mixed_codes_keep_both_codes_and_the_more_specific_hint():
    v4 = ARCHIVE["release-v4"]
    error = refusal(v4)
    assert error["code"] == "POLICY_ARGUMENT_MISMATCH"
    first, second = error["cases"]
    assert first["code"] == "POLICY_ARGUMENT_MISMATCH"
    assert first["repair_kind"] == "ARGUMENT_LOCATION"
    assert "fields" not in first  # the precheck recorded no path, so none is invented
    assert second["code"] == "OPENAPI_VALUE_LENGTH"
    assert second["fields"] == ["steps.0.body.name"]
    assert second["hint"] == CODE_HINTS["OPENAPI_VALUE_LENGTH"]


def test_the_policy_argument_hint_describes_the_rule_the_checker_enforces():
    assert "body/path_parameters/query/headers" in HINTS["ARGUMENT_LOCATION"]
    assert "不要改名" in HINTS["ARGUMENT_LOCATION"]


@pytest.mark.parametrize("code,kind", [
    ("OPENAPI_UNKNOWN_BODY_FIELD", "UNKNOWN_FIELD"), ("OPENAPI_OBJECT_FIELDS", "UNKNOWN_FIELD"),
    ("OPENAPI_SCHEMA_EXTRA_FIELD", "UNKNOWN_FIELD"), ("DSL_SCHEMA_MISSING_FIELD", "MISSING_FIELD"),
    ("MODEL_SCHEMA_TYPE_OR_CONSTRAINT", "TYPE_MISMATCH"),
    ("POLICY_ARGUMENT_MISMATCH", "ARGUMENT_LOCATION"),
])
def test_the_six_repair_categories_come_from_a_closed_mapping(code, kind):
    assert repair_kind(code) == kind


def test_a_recorded_reference_outranks_the_code_family():
    assert repair_kind("OPENAPI_UNKNOWN_BODY_FIELD",
                       {"reference": {"branch": "ARGUMENT_SLOT", "step_index": 0}}) \
        == "REFERENCE_ERROR"
    assert repair_kind("SOMETHING_NEW") == "OTHER"


# ------------------------------------------------------------------ sanitisation


def test_a_value_shaped_fact_never_travels_even_under_a_whitelisted_key():
    case = archived("release-v5", 0)
    case["preflight_diagnostic"]["detail"].update(
        step_id="AKIA5SECRETVALUE",                 # identifier slot, credential-shaped
        request_value="测试包裹B",                   # not on the whitelist at all
        allowed_body_fields=["name", "secret value!", "AKIA5SECRETVALUE"])  # one safe name only
    case["preflight_diagnostic"]["fields"] = ["steps.0.body.测试包裹B", "steps.0.body.name"]
    case["title"] = "替换包裹时名称超长应被拒绝"
    text = json.dumps(refusal([case]), ensure_ascii=False)
    for poison in ("AKIA5SECRETVALUE", "测试包裹B", "secret value!", "请求值"):
        assert poison not in text
    row = refusal([case])["cases"][0]
    assert row["allowed_body_fields"] == ["name"]  # the schema-owned name survives
    assert row["fields"] == ["steps.0.body.name"]  # the poisoned path is dropped, not trimmed
    assert "step_id" not in row  # the only recorded id was credential-shaped, so none is named
    assert row["obligation_id"] == "web_rule_3"  # a real obligation still travels
    assert row["unknown_body_positions"] == [0]


def test_free_text_and_unlisted_keys_are_dropped_by_the_whitelist():
    detail = dict(hint="上一版把 value=测试包裹A 放进了正文", window='"name","value":"测试包裹A"',
                  allowed_body_fields=["name"], step_id="create_1")
    kept, omitted = facts(detail)
    assert kept == dict(step_id="create_1", allowed_body_fields=["name"])
    assert omitted == {}


def test_public_diagnostic_keeps_closed_codes_and_drops_unknown_shapes():
    public = public_diagnostic(dict(layer="openapi", code="OPENAPI_UNKNOWN_BODY_FIELD",
                                   fields=["steps.0.body.name"], detail=dict(limit="64")))
    assert public["code"] == "OPENAPI_UNKNOWN_BODY_FIELD"
    assert public["repair_kind"] == "UNKNOWN_FIELD"
    assert public["layer"] == "OPENAPI"
    assert public["fields"] == ["steps.0.body.name"]
    assert "detail" not in public  # limit="64" is a string, not the int the schema expects
    assert public_diagnostic(None) == {} and case_failure(None) is None


def test_two_failing_cases_never_swap_their_facts():
    first = archived("release-v5", 0)
    second = archived("release-v5", 1)
    second["preflight_diagnostic"]["detail"].update(
        step_id="create_for_quantity", allowed_body_fields=["name", "quantity"],
        unknown_body_positions=[2])
    error = refusal([first, second])
    left, right = error["cases"]
    assert (left["case_id"], left["step_id"], left["allowed_body_fields"],
            left["unknown_body_positions"]) == (
        "negative_batch_6_case_1", "create_for_name_too_long", ["meta", "name", "quantity"], [0])
    assert (right["case_id"], right["step_id"], right["allowed_body_fields"],
            right["unknown_body_positions"]) == (
        "negative_batch_6_case_2", "create_for_quantity", ["name", "quantity"], [2])
    assert left["obligation_id"] == right["obligation_id"] == "web_rule_3"


# ------------------------------------------------------------------ bounds and determinism


def test_the_feedback_lists_every_case_and_states_what_it_left_out():
    case = archived("release-v5", 0)
    many = [dict(case, case_id=f"case_{index}") for index in range(7)]
    many[0]["preflight_diagnostic"]["detail"]["allowed_body_fields"] = [
        f"field_{index}" for index in range(10)]
    error = refusal(many)
    # Every rejected case stays locatable: four keep their facts, three are compact rows.
    assert len(error["cases"]) == 7 and error["cases_total"] == 7
    assert error["compact_cases"] == 3 and "omitted_cases" not in error
    assert [(row["case_id"], row["code"]) for row in error["cases"]] == [
        (f"case_{index}", "OPENAPI_UNKNOWN_BODY_FIELD") for index in range(7)]
    assert [bool(row.get("compact")) for row in error["cases"]] == [False] * 4 + [True] * 3
    first = error["cases"][0]
    # A truncated candidate list says so and keeps the total: it must not read as the whole set.
    assert first["allowed_body_fields"] == [f"field_{index}" for index in range(6)]
    assert first["allowed_body_fields_total"] == 10 and first["allowed_body_fields_truncated"]
    assert first["omitted"] == {"allowed_body_fields": 4}
    assert "truncated=true" in error["hint"] and "不代表不合法" in error["hint"]
    assert len(json.dumps(error, ensure_ascii=False).encode("utf-8")) < 4000


def test_a_case_beyond_the_hard_bound_is_counted_not_silently_dropped():
    from agenttest.case_diagnostics import MAX_CASES_TOTAL

    case = archived("release-v5", 0)
    many = [dict(case, case_id=f"case_{index}") for index in range(MAX_CASES_TOTAL + 3)]
    error = refusal(many)
    assert len(error["cases"]) == MAX_CASES_TOTAL
    assert error["cases_total"] == MAX_CASES_TOTAL + 3
    assert error["omitted_cases"] == 3


def test_the_candidate_list_keeps_the_culprit_first_when_it_must_truncate():
    case = archived("release-v5", 0)
    case["preflight_diagnostic"]["detail"].update(
        fields=["plan.steps.0.arguments.1.quantity"],
        allowed_body_fields=["meta", "name", "quantity", "note", "price", "tags", "weight"])
    row = case_failure(case)
    assert row["allowed_body_fields_total"] == 7
    assert row["allowed_body_fields"][0] == "quantity"
    assert row["allowed_body_fields"] == ["quantity", "meta", "name", "note", "price", "tags"]


def test_the_same_rejection_serialises_identically_twice():
    cases = ARCHIVE["release-v5"]
    assert json.dumps(refusal(cases), ensure_ascii=False) \
        == json.dumps(refusal(cases), ensure_ascii=False)


def test_an_empty_refusal_still_names_a_code():
    error = rejection_error([], retryable=True, message="方案未通过执行前校验，未发送业务请求。")
    assert error["code"] == "PLAN_REJECTED" and error["hint"] and "cases" not in error
    assert project_cases([]) == ([], 0, 0)


# ------------------------------------------------------------------ the payload the model reads


def rejected_effect(cases):
    """A group whose design succeeded (``PROPOSED``/``DESIGNED``) but whose cases failed precheck.

    This is the archived shape: ``negative_batch_6`` stayed ``DESIGNED`` while every case was
    rejected, which is exactly the branch that used to answer with a bare code.
    """

    def effect(summary, group, supervision):
        summary["design_records"].append(dict(group_id=group["id"], status="PROPOSED"))
        summary["cases"].extend(json.loads(json.dumps(cases)))
        group["status"] = "DESIGNED"
    return effect


@pytest.mark.asyncio
async def test_the_case_facts_reach_the_model_refusal_with_zero_business_requests(tmp_path):
    cases = [dict(archived("release-v5", 0), group_id="group_1"),
             dict(archived("release-v5", 1), group_id="group_1")]
    session_, tasks, _, _, _ = session(tmp_path, effect=rejected_effect(cases))
    before = session_.ledger.requests
    payload = await session_.run_group("group_1")
    assert payload["ok"] is False and payload["status"] == "REJECTED"
    assert payload["requests"] == 0 and session_.ledger.requests == before
    error = payload["error"]
    assert error["code"] == "OPENAPI_UNKNOWN_BODY_FIELD"
    assert error["retryable"] is True and error["fields"] == ["plan.steps.0.arguments.1.name"]
    assert [row["case_id"] for row in error["cases"]] == ["negative_batch_6_case_1",
                                                          "negative_batch_6_case_2"]
    assert error["cases"][0]["allowed_body_fields"] == ["meta", "name", "quantity"]
    assert error["cases"][0]["unknown_body_positions"] == [0]
    # The stored evidence keeps the untouched precheck record; only the payload is projected.
    stored = session_.summary["cases"][0]["preflight_diagnostic"]
    assert stored["detail"]["step_id"] == "create_for_name_too_long"
    assert len(tasks.calls) == 1


@pytest.mark.asyncio
async def test_a_group_that_passes_precheck_is_untouched_by_the_new_feedback(tmp_path):
    session_, _, _, _, _ = session(tmp_path)
    payload = await session_.run_group("group_1")
    assert payload["ok"] is True
    assert "error" not in payload
    assert payload["cases"][0]["status"] == "FINISHED"


@pytest.mark.asyncio
async def test_a_single_rejected_case_still_reports_one_case(tmp_path):
    case = dict(archived("release-v4", 1), group_id="group_1")
    session_, _, _, _, _ = session(tmp_path, effect=rejected_effect([case]))
    payload = await session_.run_group("group_1")
    error = payload["error"]
    assert error["code"] == "OPENAPI_VALUE_LENGTH"
    assert error["cases"][0]["repair_kind"] == "OTHER"
    assert "omitted_cases" not in error
    assert payload["requests"] == 0
