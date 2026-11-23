"""A whole-project release's requested checks come only from verified Focus proposals."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from test_p3b24_diagnostics import CLAUSE, check_context, decode, focus_wire
from test_product_design import project, settings
from test_product_tasks import finished, response, service

from agenttest.diagnostics import ValidationFailure
from agenttest.product_checks import MISSING_REQUESTED_CHECKS, check_index
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput
from agenttest.request_requirements import request_atoms
from agenttest.task_completion import project_variant_scope_sources


def release_context():
    context, source_ref = check_context()
    first = context["approved_checks"][0]
    first["roles"] = [None]
    context["approved_checks"].append(dict(first, policy_id="policy_2", name="other"))
    context.update(managed_authentication=[], pending_checks=[],
                   focus_ready_operations=["createParcel"])
    return context, source_ref


def wire_with_checks(source_ref, *check_refs):
    wire = focus_wire(source_ref, None, requirement_map={"request_1": dict(CLAUSE,
        actions=[dict(operation_id="createParcel", role=None)])})
    wire["scope"]["operations"]["createParcel"]["requested_checks"] = [
        dict(check_ref=check_ref, source_ref=source_ref, exact_value=None)
        for check_ref in check_refs]
    return wire


def missing(wire, context):
    with pytest.raises(ValidationFailure, match=MISSING_REQUESTED_CHECKS) as caught:
        decode(wire, context)
    (violation,) = caught.value.diagnostic.detail["violations"]
    assert violation["count"] == len(violation["missing_checks"])
    return violation["missing_checks"]


def test_empty_partial_and_complete_mappings_use_operation_local_catalog_refs():
    context, source = release_context()
    rows = [dict(operation_id="createParcel", check_ref=f"check_{n}", source_ref=source)
            for n in (1, 2)]
    empty = wire_with_checks(source)
    assert missing(empty, context) == rows
    empty["scope"].update(mode="all_ready", operations={})
    assert missing(empty, context) == rows
    assert missing(wire_with_checks(source, "check_2"), context) == rows[:1]
    wire = wire_with_checks(source, "check_2", "check_1")
    # A valid reference to the main request has historically been allowed, as in archived v1.
    wire["scope"]["operations"]["createParcel"]["requested_checks"][0]["source_ref"] = "source_2"
    assert decode(wire, context)["requested_checks"] == [
        dict(policy_id="policy_2", source_quote=context["request_atoms"][0]["text"]),
        dict(policy_id="policy_1", source_quote=context["request_atoms"][1]["text"])]


@pytest.mark.parametrize("scope", ["zero", "not_ready", "unknown_inventory", "ambiguous"])
def test_unreliable_or_empty_catalog_keeps_existing_behavior(scope):
    context, source = release_context()
    if scope == "zero":
        context["approved_checks"] = []
    elif scope == "not_ready":
        context["focus_ready_operations"] = []
    elif scope == "unknown_inventory":
        context.pop("pending_checks")
    else:
        context["approved_checks"].append(context["approved_checks"][0])
    assert decode(wire_with_checks(source), context)["requested_checks"] == []


def test_missing_checks_exclude_unapproved_operations_pending_identities_and_held_operations():
    context, source = release_context()
    context["approved_ids"].append("heldOperation")
    context["focus_ready_operations"].append("unapprovedOperation")
    context["unavailable_operations"] = [dict(operation_id="heldOperation")]
    context["managed_authentication"] = [dict(identity="held", credential_status="MISSING"),
                                          dict(identity="ready", credential_status="READY")]
    context["pending_checks"] = [dict(operation_id="createParcel", role="ready")]
    first, second = context["approved_checks"]
    first["roles"] = ["held"]
    context["approved_checks"].extend([
        dict(second, policy_id="policy_3", operation_id="unapprovedOperation"),
        dict(second, policy_id="policy_4", operation_id="heldOperation"),
        dict(second, policy_id="policy_5", roles=["ready"]),
    ])
    wire = wire_with_checks(source)
    wire["scope"]["operations"]["heldOperation"] = None
    wire["blocked_operations"]["heldOperation"] = False
    assert missing(wire, context) == [dict(operation_id="createParcel", check_ref="check_2",
                                          source_ref=source)]
    repaired = wire_with_checks(source, "check_2")
    repaired["scope"]["operations"]["heldOperation"] = None
    repaired["blocked_operations"] = wire["blocked_operations"]
    assert len(decode(repaired, context)["requested_checks"]) == 1


@pytest.mark.parametrize("task", [
    "测试整个项目。正常路径和非法入参都按已批准的规则执行。",
    "发版前仅验收 insertJob。正常路径和非法入参都按已批准的规则执行。",
    "发版前完整验收整个项目，仅测试 insertJob。正常路径和非法入参都按已批准的规则执行。",
    "发版前完整验收整个项目但跳过写入。正常路径和非法入参都按已批准的规则执行。",
    "发版前完整验收整个项目。正常路径和非法入参都按已批准的规则执行。以后再加查询。",
])
def test_ordinary_limited_and_future_tasks_keep_existing_acceptance(task):
    proj, _ = project(negative=True)
    context, _ = release_context()
    atoms = request_atoms(task)
    trusted = project_variant_scope_sources(proj, task, atoms)
    context.update(user_task=task, request_atoms=atoms, trusted_scope_clauses=trusted)
    wire = wire_with_checks("source_3")
    wire["requirement_map"] = {atom["source_id"]: dict(CLAUSE,
        disposition="deferred" if atom["future"] else "required",
        actions=[] if atom["future"] else [dict(operation_id="createParcel", role=None)])
        for atom in atoms
        if atom["source_id"] not in trusted}
    assert decode(wire, context)["requested_checks"] == []


def test_a1_keeps_independent_requirement_literal_and_missing_mapping_diagnostics():
    context, source = release_context()
    wire = focus_wire(source, dict(value="sk-synthetic-not-for-model"))
    with pytest.raises(ValidationFailure, match="FOCUS_SOURCE_CLAUSE_MISSING") as caught:
        decode(wire, context)
    violations = caught.value.diagnostic.detail["violations"]
    assert [item["error_code"] for item in violations] == ["FOCUS_SOURCE_CLAUSE_MISSING",
        "FOCUS_INPUT_TEXT_CHANGED", MISSING_REQUESTED_CHECKS]
    assert violations[0]["must_delete"] == ["requirement_map.request_2"]
    assert violations[1]["must_be_null"] == ["requested_checks.0.exact_value"]
    assert violations[2]["missing_checks"] == [dict(operation_id="createParcel",
                                                   check_ref="check_2", source_ref=source)]
    assert "sk-synthetic-not-for-model" not in caught.value.diagnostic.model_dump_json()
    wire["scope"].update(mode="all_ready", operations={})
    with pytest.raises(ValidationFailure, match="FOCUS_SOURCE_CLAUSE_MISSING") as caught:
        decode(wire, context)
    requirement, gaps = caught.value.diagnostic.detail["violations"]
    assert requirement["must_delete"] == ["requirement_map.request_2"]
    assert gaps["error_code"] == MISSING_REQUESTED_CHECKS and gaps["count"] == 2


def test_approved_response_checks_do_not_fill_a_negative_mapping():
    context, source = release_context()
    context["response_expectations"] = {
        "parcel_name": dict(operation_id="createParcel", roles=[None], select="$.name",
                            operator="equals", expected="example")}
    wire = wire_with_checks(source)
    wire["requirement_map"]["request_1"]["response_checks"] = ["parcel_name"]
    assert len(missing(wire, context)) == 2
    wire["scope"]["operations"] = wire_with_checks(source, "check_1", "check_2")["scope"][
        "operations"]
    result = decode(wire, context)
    assert len(result["requested_checks"]) == 2
    assert result["requirements"][0]["response_checks"] == ["parcel_name"]


@pytest.mark.asyncio
async def test_task_service_sends_missing_checks_on_its_one_focus_retry(tmp_path, monkeypatch):
    proj, setup = project(negative=True)
    index = check_index(proj)
    requests, target_requests = [], []
    task = "发版前请完整验收整个项目，正常路径和非法入参都按已批准的规则执行。"
    unapproved = "unaudited-draft-value"
    credential = "sk-synthetic-credential-placeholder"

    def focus(request):
        requests.append(json.loads(request.content))
        context = json.loads(requests[-1]["messages"][1]["content"])
        bundle = dict(input_requirements=[], input_questions=[], requested_checks=[],
                      expectation_questions=[], test_normal=True, existing_resource=False)
        wire = dict(interpretation="整项目发版验收", scope=dict(mode="selected", question=None,
            operations=dict(insertJob=deepcopy(bundle), readJob=deepcopy(bundle))),
            blocked_operations={}, requirement_map={"request_1": dict(CLAUSE, actions=[
                dict(operation_id=op, role=None) for op in context["approved_ids"]])})
        checks = wire["scope"]["operations"]["insertJob"]["requested_checks"]
        for n, entry in enumerate(index, 1):
            check = dict(check_ref=f"check_{n}", source_ref="source_3", exact_value=None)
            if (entry["kind"] == "boundary_outside"
                    and entry["field_constraints"]["type"] == "string"):
                check["exact_length"] = None
            checks.append(check)
        if len(requests) == 1:
            check = checks[0]
            check["exact_value"] = dict(value=f"{unapproved} Authorization: Bearer {credential}")
            checks[:] = [check]
        return response(wire)

    def no_target(request):
        target_requests.append(request)
        raise AssertionError("This Focus test must not reach business HTTP")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(no_target))
    svc = service(tmp_path, lambda _: response({}))
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile="generic")
    try:
        saved = svc.intake.save(IntakeInput(name="Focus 请求映射", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        done = await finished(svc, await svc.submit("operator", saved["id"], task, uuid4().hex))
        assert len(requests) == 2
        first, second = [json.loads(body["messages"][1]["content"]) for body in requests]
        assert "previous_error" not in first
        assert "focus_ready_operations" not in second
        repair = second["previous_error"]
        assert repair == second["previous_order_error"]
        assert repair["detail"]["requirement_keys"] == ["request_1"]
        assert repair["detail"]["trusted_scope_clauses"] == ["request_2"]
        literal, gaps = repair["detail"]["violations"]
        assert literal["must_be_null"] == ["requested_checks.0.exact_value"]
        assert gaps["error_code"] == MISSING_REQUESTED_CHECKS
        assert gaps["count"] == len(index) - 1
        assert gaps["missing_checks"] == [dict(operation_id="insertJob", check_ref=f"check_{n}",
            source_ref="source_3") for n in range(2, len(index) + 1)]
        assert "omitted_detail" not in repair["detail"]
        assert all(value not in json.dumps(requests) for value in (unapproved, credential,
                   settings().api_key.get_secret_value(), "Authorization: Bearer"))
        assert {item["policy_id"] for item in done["summary"]["requested_checks"]} == {
            entry["policy_id"] for entry in index}
        assert not target_requests
    finally:
        await svc.close()
