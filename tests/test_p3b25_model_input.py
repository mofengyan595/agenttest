"""P3-B2.5 audit: what the model endpoint really receives, measured on the encoded request.

The audit found two paths where a *stored* diagnostic dict was copied verbatim into a tool
result (``payload.cases[].preflight_diagnostic`` and a design rejection's ``detail``), so an
operator value, a header or a free-text message could travel to the gateway. These tests drive
the real encoder (``EcnuxChatModel`` over a stub transport) and assert on the bytes of the POST,
not on the projection helper: a helper that is correct but not wired in would still pass a unit
test. Sentinels are obviously synthetic; no real credential is used anywhere.
"""

import json
import re
from pathlib import Path

import httpx
import pytest
from product_agent_support import session

from agenttest.llm_planner import ModelSettings
from agenttest.model_input import (
    MAX_TEXT,
    diagnostic_view,
    redact,
    safe_detail,
    safe_fields,
    safe_path,
)
from agenttest.product_agent import SYSTEM_PROMPT, _json

ARCHIVE = json.loads((Path(__file__).parent / "fixtures" / "p3b25" / "cases-archive.json")
                     .read_text(encoding="utf-8"))

# Every sentinel is fabricated. ``sk-audit…`` is not a provider key shape the adapter accepts;
# it only needs to be a string that must never appear on the wire.
SENTINELS = {
    "api_key": "sk-auditSYNTHETIC0123456789abcdef",
    "authorization": "Authorization: Bearer auditSYNTHETICtoken",
    "bearer": "Bearer auditSYNTHETICtoken",
    "body_value": "审计测试包裹B",
    "field_slot_value": "AKIAauditSYNTHETIC0001",
    "error_text": "SENTINEL-error-message-含敏感内容",
}
RECORD_HINT = "补上删除步骤或去掉该断言。"   # a real, app-authored hint that must survive
# ``seconds_left`` is a wall-clock read. Inside the wire body it is escaped, so both the plain
# and the escaped quoting are matched; only the number is deleted.
CLOCK = re.compile(r'(\\?"seconds_left\\?"\s*:\s*)-?\d+(?:\.\d+)?(?:e-?\d+)?(?=[,}])')


def archived(round_name, index=0):
    return json.loads(json.dumps(ARCHIVE[round_name][index]))


def settings():
    return ModelSettings(model="audit-model", base_url="https://audit.invalid/v1",
                         api_key="sk-synthetic-audit-placeholder", max_input_tokens=200_000,
                         max_total_tokens=300_000, max_output_tokens=4096)


async def wire(payload) -> tuple[str, str]:
    """Run the tool result through the real encoder and return (tool text, wire body)."""
    pytest.importorskip(
        "langchain_core",
        reason="Model wire audit requires the optional Agent dependencies",
    )
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    from agenttest.deep_harness.chat_model import build_chat_model

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={
            "id": "audit-1", "choices": [
                {"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    model = build_chat_model(settings(), transport=httpx.MockTransport(handler))
    text = _json(payload)
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="审计目标"),
                ToolMessage(content=text, tool_call_id="call_1")]
    await model._agenerate(messages)
    body = captured["body"].decode("utf-8")
    # The body is the real POST payload: one JSON object with the tool message inside it.
    assert json.loads(body)["messages"][-1]["content"] == text
    return text, body


def poisoned_case(group_id="group_1"):
    case = archived("release-v5", 0)
    case["group_id"] = group_id
    case["title"] = SENTINELS["body_value"]
    case["preflight_diagnostic"]["detail"].update(
        hint=SENTINELS["error_text"], window=SENTINELS["authorization"],
        request_value=SENTINELS["body_value"], step_id=SENTINELS["field_slot_value"],
        allowed_body_fields=["name", SENTINELS["field_slot_value"]])
    case["preflight_diagnostic"]["fields"].append(SENTINELS["body_value"])
    return case


def rejected(cases):
    def effect(summary, group, supervision):
        summary["design_records"].append(dict(group_id=group["id"], status="PROPOSED"))
        for case in cases:
            summary["cases"].append(json.loads(json.dumps(case)))
        group["status"] = "DESIGNED"
    return effect


def designed(record_extra=None):
    def effect(summary, group, supervision):
        record = dict(group_id=group["id"], status="REJECTED", reason="MODEL_INVALID_RESPONSE",
                      diagnostic=dict(layer="model", code="PLANNING_INSTANCE_COVERAGE",
                                      fields=["instance.ai_2"],
                                      detail=dict(missing=["ai_2"])))
        if record_extra:
            record["diagnostic"]["detail"].update(record_extra)
        summary["design_records"].append(record)
        group["status"] = "NOT_EXECUTED"
        group["reason"] = "MODEL_INVALID_RESPONSE"
    return effect


# ------------------------------------------------------- 1. the wire, not the helper


@pytest.mark.asyncio
async def test_no_stored_value_reaches_the_wire_for_a_case_level_refusal(tmp_path):
    case = poisoned_case()
    session_, _, _, _, _ = session(tmp_path, effect=rejected([case]))
    payload = await session_.run_group("group_1")
    text, body = await wire(payload)
    for name, value in SENTINELS.items():
        assert value not in text, f"{name} reached the tool result"
        assert value not in body, f"{name} reached the request body"
    # The audit evidence is untouched: the stored record still holds what the producer wrote.
    stored = json.dumps(session_.summary, ensure_ascii=False)
    for name in ("authorization", "bearer", "body_value", "field_slot_value", "error_text"):
        assert SENTINELS[name] in stored, f"{name} was removed from the stored evidence"
    assert payload["ok"] is False and payload["requests"] == 0
    assert session_.ledger.requests == 0


@pytest.mark.asyncio
async def test_a_design_refusal_redacts_credentials_and_drops_values_from_fact_slots(tmp_path):
    extra = dict(window=SENTINELS["authorization"], message=SENTINELS["bearer"],
                 hint=RECORD_HINT, operations={"ai_2": SENTINELS["body_value"]},
                 reference=SENTINELS["field_slot_value"])
    session_, _, _, _, _ = session(tmp_path, effect=designed(extra))
    payload = await session_.run_group("group_1")
    text, body = await wire(payload)
    for name in ("api_key", "authorization", "bearer", "body_value", "field_slot_value"):
        assert SENTINELS[name] not in text
        assert SENTINELS[name] not in body
    # The repair facts that matter survive, and the prose slot keeps its sentence.
    assert payload["error"]["code"] == "PLANNING_INSTANCE_COVERAGE"
    assert payload["error"]["detail"]["missing"] == ["ai_2"]
    assert payload["error"]["detail"]["hint"] == RECORD_HINT
    assert "omitted" in payload["error"]["detail"]
    assert payload["requests"] == 0 and session_.ledger.requests == 0


# ------------------------------------------------------- 2. every rejected case stays locatable


@pytest.mark.asyncio
async def test_seven_rejected_cases_are_all_locatable_in_the_model_input(tmp_path):
    cases = [dict(archived("release-v5", 0), case_id=f"case_{index}", group_id="group_1")
             for index in range(7)]
    session_, _, _, _, _ = session(tmp_path, effect=rejected(cases))
    payload = await session_.run_group("group_1")
    text, body = await wire(payload)
    error = payload["error"]
    assert error["cases_total"] == 7 and error["compact_cases"] == 3
    assert "omitted_cases" not in error
    listed = [(row["case_id"], row["code"]) for row in error["cases"]]
    assert listed == [(f"case_{index}", "OPENAPI_UNKNOWN_BODY_FIELD") for index in range(7)]
    # Each identity and code is in the bytes the gateway receives, not only in the helper output.
    for case_id, code in listed:
        assert case_id in text and case_id in body
        assert code in body
    assert sum(1 for row in error["cases"] if row.get("compact")) == 3


@pytest.mark.asyncio
async def test_an_archived_case_without_a_field_path_keeps_its_code_and_hint(tmp_path):
    case = archived("release-v4", 0)
    case["group_id"] = "group_1"
    assert case["preflight_diagnostic"].get("fields") in (None, [])
    session_, _, _, _, _ = session(tmp_path, effect=rejected([case]))
    payload = await session_.run_group("group_1")
    row, = payload["error"]["cases"]
    assert row["code"] == "POLICY_ARGUMENT_MISMATCH" and row["repair_kind"] == "ARGUMENT_LOCATION"
    assert "fields" not in row and row["case_id"] == case["case_id"]
    assert payload["error"]["hint"]  # the generic repair hint still reaches the model


@pytest.mark.asyncio
async def test_cases_with_different_obligations_keep_their_own_policy_ids(tmp_path):
    first = archived("release-v5", 0)
    second = archived("release-v4", 1)
    first["group_id"] = second["group_id"] = "group_1"
    first["planned_policy_ids"] = ["web_rule_3", "web_rule_7_negative_4"]
    second["planned_policy_ids"] = ["web_rule_5"]
    session_, _, _, _, _ = session(tmp_path, effect=rejected([first, second]))
    payload = await session_.run_group("group_1")
    left, right = payload["error"]["cases"]
    assert left["policy_ids"] == ["web_rule_3", "web_rule_7_negative_4"]
    assert right["policy_ids"] == ["web_rule_5"]


# ------------------------------------------------------- 3. a truncated candidate list is honest


@pytest.mark.asyncio
async def test_twelve_allowed_fields_are_marked_partial_instead_of_claimed_complete(tmp_path):
    case = archived("release-v5", 0)
    case["group_id"] = "group_1"
    names = ["meta", "name", "quantity", "note", "price", "tags", "weight",
             "colour", "size", "owner", "photo", "label"]
    case["preflight_diagnostic"]["detail"]["allowed_body_fields"] = names
    session_, _, _, _, _ = session(tmp_path, effect=rejected([case]))
    payload = await session_.run_group("group_1")
    text, body = await wire(payload)
    row, = payload["error"]["cases"]
    assert row["allowed_body_fields"] == names[:6]
    assert row["allowed_body_fields_total"] == 12 and row["allowed_body_fields_truncated"]
    assert row["omitted"] == {"allowed_body_fields": 6}
    hint = payload["error"]["hint"]
    assert "truncated=true" in hint and "不代表不合法" in hint
    # The warning is in the request body, so the model cannot read the six names as the whole set.
    assert "truncated=true" in body and "不代表不合法" in body
    assert all(name in body for name in names[:6])


@pytest.mark.asyncio
async def test_a_value_pasted_into_a_name_slot_is_dropped_not_echoed(tmp_path):
    case = archived("release-v5", 0)
    case["group_id"] = "group_1"
    case["preflight_diagnostic"]["detail"]["allowed_body_fields"] = [
        "name", SENTINELS["field_slot_value"], SENTINELS["body_value"], "token"]
    session_, _, _, _, _ = session(tmp_path, effect=rejected([case]))
    payload = await session_.run_group("group_1")
    text, body = await wire(payload)
    row, = payload["error"]["cases"]
    assert row["allowed_body_fields"] == ["name"]
    assert row["omitted"] == {"allowed_body_fields": 3}
    for name in ("field_slot_value", "body_value"):
        assert SENTINELS[name] not in body


# ------------------------------------------------------- 4. deterministic and bounded


@pytest.mark.asyncio
async def test_the_same_refusal_produces_the_same_request_body(tmp_path):
    cases = [dict(archived("release-v5", index % 2), group_id="group_1")
             for index in range(6)]
    first, _, _, _, _ = session(tmp_path / "a", effect=rejected(cases))
    second, _, _, _, _ = session(tmp_path / "b", effect=rejected(cases))
    left = await first.run_group("group_1")
    right = await second.run_group("group_1")
    # The remaining budget carries a wall-clock read (``seconds_left``), and inside the wire it
    # sits in an escaped JSON string; deleting only that number must leave every other byte
    # identical, so a refusal can never vary with anything but the clock.
    left_body, right_body = (await wire(left))[1], (await wire(right))[1]
    assert CLOCK.sub("seconds_left=0", left_body) == CLOCK.sub("seconds_left=0", right_body)
    assert len(CLOCK.findall(left_body)) == len(CLOCK.findall(right_body))
    # And the refusal itself is identical field by field.
    assert {key: value for key, value in left.items() if key != "budget"} == {
        key: value for key, value in right.items() if key != "budget"}


@pytest.mark.asyncio
async def test_the_model_input_keeps_a_bound_on_the_refusal_size(tmp_path):
    cases = [dict(archived("release-v5", 0), case_id=f"case_{index}", group_id="group_1")
             for index in range(16)]
    cases[0]["preflight_diagnostic"]["detail"]["allowed_body_fields"] = [
        f"field_{index}" for index in range(40)]
    session_, _, _, _, _ = session(tmp_path, effect=rejected(cases))
    payload = await session_.run_group("group_1")
    text, _ = await wire(payload)
    # The 6 KB tool-result budget keeps ``error`` whole (the P2 rule), so a 16-case refusal is
    # bounded by the error itself plus that budget's preview, not by the preview alone.
    assert len(text.encode("utf-8")) <= 2 * 6_000
    assert payload["error"]["cases_total"] == 16
    for row in payload["error"]["cases"]:
        assert row["case_id"] in text


# ------------------------------------------------------- 5. projection units


def test_safe_detail_redacts_credentials_and_keeps_repair_facts():
    detail = dict(missing=["ai_2"], allowed_from_steps=["create"],
                  hint="补上产生者步骤。", window="Authorization: Bearer abc123def456",
                  reference="SOURCE_UNKNOWN", count=3, field_path="requested_checks.0.exact_value")
    kept, omitted = safe_detail(detail)
    assert kept["missing"] == ["ai_2"] and kept["allowed_from_steps"] == ["create"]
    assert kept["hint"] == "补上产生者步骤。" and kept["reference"] == "SOURCE_UNKNOWN"
    assert "[已脱敏]" in kept["window"] and "abc123def456" not in kept["window"]
    assert kept["count"] == 3 and kept["field_path"] == "requested_checks.0.exact_value"
    assert omitted == {}


def test_safe_detail_drops_a_value_from_a_fact_slot_and_counts_it():
    kept, omitted = safe_detail(dict(operations={"ai_2": "审计测试包裹B"}, step_id="insert"))
    assert kept["step_id"] == "insert" and "operations" not in kept
    assert omitted == {"operations": 1}


def test_safe_detail_caps_items_keys_and_text():
    kept, omitted = safe_detail(dict(missing=[f"ai_{index}" for index in range(14)]))
    assert len(kept["missing"]) == 10 and omitted == {"missing": 4}
    kept, omitted = safe_detail({f"key_{index}": index for index in range(20)})
    assert len([key for key in kept if key != "omitted"]) == 12 and omitted["keys"] == 8
    kept, _ = safe_detail(dict(hint="修" * 5))
    assert len(kept["hint"]) == 5
    kept, _ = safe_detail(dict(message="sk-auditSYNTHETIC0123456789abcdef rejected"))
    assert "sk-auditSYNTHETIC" not in kept["message"]


def test_redaction_is_a_no_op_for_every_real_diagnostic_word():
    for value in ("MISSING_COMMA", "plan.steps.0.arguments.1.name", "web_rule_7_negative_4",
                  "补上产生者步骤。", "SOURCE_UNKNOWN", "expectation_5d9ea0609816b372b92f"):
        assert redact(value) == value


def test_diagnostic_view_is_closed_and_deterministic():
    view = diagnostic_view(dict(layer="openapi", code="OPENAPI_UNKNOWN_BODY_FIELD",
                                fields=["plan.steps.0.arguments.1.name", "审计测试包裹B"],
                                detail=dict(step_id="create", unknown_body_positions=[0],
                                            request_value="审计测试包裹B"),
                                reference=dict(branch="PRODUCER", step_index=0)))
    assert view == dict(code="OPENAPI_UNKNOWN_BODY_FIELD", layer="OPENAPI",
                        fields=["plan.steps.0.arguments.1.name"],
                        reference=dict(branch="PRODUCER", step_index=0),
                        detail=dict(step_id="create", unknown_body_positions=[0],
                                    omitted={"request_value": 1}))
    assert diagnostic_view(dict(code="not-a-code"))["code"] == ""
    assert safe_fields(["a.b", "审计", "a b"]) == ["a.b"]
    assert safe_path("a.b.0") == "a.b.0" and safe_path("a b") is None
    assert MAX_TEXT >= 60
