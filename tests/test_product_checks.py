"""Requested negatives cannot disappear behind a passing normal flow or a provider schema."""

import copy
import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_product_design import case, project, settings
from test_product_focus import focus_response
from test_product_tasks import finished, response, service

from agenttest.diagnostics import ValidationFailure
from agenttest.product_checks import (
    RequestedCheck,
    check_index,
    validate_requested_checks,
)
from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_design import design_context, design_groups
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput

REQUEST = "创建资料，另测数量缺失、数量类型错误、名称长度21字。"


def test_unassigned_operator_literal_holds_only_the_default_check_without_changing_authority():
    from test_negative_values import policy, read_project

    from agenttest.product_checks import partition_requested_checks

    proj, _ = read_project()
    before = proj.registry.digest()
    wrong_type = policy(proj)
    wrong_enum = policy(proj, name="order", kind="enum_outside")
    quote = 'limit使用“moss_units”，order使用“slant_order”。'
    checks = [dict(policy_id=wrong_type.id, source_quote=quote), dict(
        policy_id=wrong_enum.id, source_quote=quote, exact_value=dict(value="slant_order"))]
    executable, pending = partition_requested_checks(proj, checks)
    assert [c["policy_id"] for c in executable] == [wrong_enum.id]
    assert pending == [dict(policy_id=wrong_type.id, operation_id="readReports", field="limit",
        requested_value=["moss_units"], reason="NEGATIVE_LITERAL_NOT_PRESERVED")]
    checks[0]["exact_value"] = dict(value="moss_units")
    executable, pending = partition_requested_checks(proj, checks)
    assert not pending and len(executable) == 2
    checks[0].pop("exact_value")
    executable, pending = partition_requested_checks(proj, checks, input_requirements=[dict(
        operation_id="other", value="moss_units")])
    assert not pending and len(executable) == 2  # A known ordinary string is accounted separately.
    assert proj.registry.digest() == before


def checks_for(proj):
    index = check_index(proj)
    return [dict(policy_id=next(entry["policy_id"] for entry in index if all(
        entry.get(k) == value for k, value in match.items())), source_quote=quote)
        for match, quote in [
            (dict(name="quantity", kind="required_missing"), "数量缺失"),
            (dict(name="quantity", kind="type_mismatch"), "数量类型错误"),
            (dict(name="name", kind="boundary_outside", invalid_length=21), "名称长度21字")]]


def proposal(checks):
    return dict(interpretation="创建资料并逐项验证三种已批准异常输入", relevant=True,
        cases=[case()], questions=[], not_covered=[], required_cases={
            item["policy_id"]: dict(title="验证" + item["source_quote"], before=[],
                check=case(policy=item["policy_id"])["plan"]["steps"][0], after=[])
            for item in checks})


def test_requested_check_provenance_scope_and_existing_approval_are_independent():
    proj, _ = project(negative=True)
    checks = [RequestedCheck.model_validate(item) for item in checks_for(proj)]
    context = dict(user_task=REQUEST, approved_checks=check_index(proj))
    assert all(entry["expected_statuses"] == [422] for entry in context["approved_checks"])
    assert all(entry["field_constraints"]["type"] == "integer"
               for entry in context["approved_checks"] if entry["name"] == "quantity")
    validate_requested_checks(checks, context, ["insertJob"])
    for invalid in ([checks[0], checks[0]],
                    [checks[0].model_copy(update=dict(policy_id="invented"))],
                    [checks[0].model_copy(update=dict(source_quote="未提出的检查"))]):
        with pytest.raises(ValidationFailure):
            validate_requested_checks(invalid, context, ["insertJob"])
    with pytest.raises(ValidationFailure):
        validate_requested_checks(checks, context, ["readJob"])


def test_wire_requires_every_negative_case_with_its_exact_approved_check_step():
    proj, _ = project(negative=True)
    checks = checks_for(proj)
    context = design_context(proj, REQUEST, design_groups(proj)[0], 40, 2)
    context["requested_checks"] = checks
    model = DesignModel(settings(), profile="generic")
    body = json.loads(model.prepare_request(context))
    validate = Draft202012Validator(body["response_format"]["json_schema"]["schema"])
    wire = proposal(checks)
    assert validate.is_valid(wire), list(validate.iter_errors(wire))
    assert len(json.loads(model._decode_output(json.dumps(wire), context))["cases"]) == 4
    missing = copy.deepcopy(wire)
    missing["required_cases"].pop(checks[0]["policy_id"])
    wrong = copy.deepcopy(wire)
    wrong["required_cases"][checks[0]["policy_id"]]["check"]["policy_id"] = "web_rule_1"
    invalid_baseline = copy.deepcopy(wire)
    invalid_baseline["required_cases"][checks[0]["policy_id"]]["check"]["arguments"][1]["value"] = 0
    assert not validate.is_valid(invalid_baseline)
    required = body["response_format"]["json_schema"]["schema"]["properties"]["required_cases"]
    references = {entry["properties"]["check"]["properties"]["arguments"]["$ref"]
                  for entry in required["properties"].values()}
    assert len(references) == 1
    assert all(entry["properties"][slot]["maxItems"] == 0
               for entry in required["properties"].values() for slot in ("before", "after"))
    for slot in ("cases", "before", "after"):
        duplicate = copy.deepcopy(wire)
        if slot == "cases":
            duplicate["cases"].append(case(policy=checks[0]["policy_id"]))
        else:
            duplicate["required_cases"][checks[0]["policy_id"]][slot].append(
                case(policy=checks[0]["policy_id"])["plan"]["steps"][0])
        assert not validate.is_valid(duplicate)
        with pytest.raises(ValidationFailure, match="DESIGN_REQUESTED_CHECK_DUPLICATE"):
            model._decode_output(json.dumps(duplicate), context)
    for invalid in (missing, wrong):
        assert not validate.is_valid(invalid)
        with pytest.raises(ValidationFailure, match="DESIGN_REQUESTED_CHECKS_MISSING"):
            model._decode_output(json.dumps(invalid), context)
    unrequested = next(p.id for p in proj.registry.definitions()
        if p.negative and p.id not in {c["policy_id"] for c in checks})
    extra = copy.deepcopy(wire)
    extra["cases"].append(case(policy=unrequested))
    assert not validate.is_valid(extra)
    with pytest.raises(ValidationFailure, match="DESIGN_NEGATIVE_CHECK_NOT_REQUESTED"):
        model._decode_output(json.dumps(extra), context)


@pytest.mark.asyncio
async def test_requested_design_context_excludes_unrequested_negative_rules():
    from agenttest.product_design import propose_design

    proj, _ = project(negative=True)
    checks = checks_for(proj)
    before = proj.registry.digest()
    requested = {c["policy_id"] for c in checks}
    class ScopedDesigner:
        async def generate(self, context):
            negative_ids = {p["policy_id"] for p in context["approved_negative_expectations"]}
            assert negative_ids == requested
            assert {p["id"] for p in context["policies"] if "negative_test" in p} == requested
            model = DesignModel(settings(), profile="generic")
            model.prepare_request(context)
            return model._decode_output(json.dumps(proposal(checks)), context)
    await propose_design(proj, REQUEST, design_groups(proj)[0], ScopedDesigner(), environ={},
        remaining_requests=40, inventory_total=2, requested_checks=checks)
    assert proj.registry.digest() == before


@pytest.mark.asyncio
async def test_unneeded_successful_creation_in_a_negative_is_rejected_before_http(
        tmp_path, monkeypatch):
    proj, setup = project(negative=True)
    checks, sent = checks_for(proj), []
    def focus(request):
        return focus_response(dict(interpretation="创建并验证三个指定异常输入", mode="selected",
            operation_ids=["insertJob"], question=None, requested_checks=checks), request,
            approved_checks=check_index(proj))
    def designer(_):
        wire = proposal(checks)
        extra = case()["plan"]["steps"][0]
        extra["id"] = "unneeded_setup"
        wire["required_cases"][checks[0]["policy_id"]]["before"] = [extra]
        return response(wire)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda request: sent.append(request)))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile="generic")
    try:
        saved = svc.intake.save(IntakeInput(name="异常场景无需额外写入",
            document=proj.document.decode(), generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], REQUEST, uuid4().hex)
        done = await finished(svc, row)
        assert not sent and done["summary"]["context_usage"]["requests"] == 0
        assert any(r.get("reason") == "DESIGN_NEGATIVE_UNNEEDED_WRITE"
                   for r in done["summary"]["design_records"])
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("omit", [False, True])
async def test_actual_task_counts_requested_checks_or_stops_before_normal_http_if_omitted(
        tmp_path, monkeypatch, omit):
    proj, setup = project(negative=True)
    checks, sent = checks_for(proj), []

    def focus(request):
        return focus_response(dict(interpretation="创建并验证三个指定异常输入", mode="selected",
            operation_ids=["insertJob"], question=None, requested_checks=checks), request,
            approved_checks=check_index(proj))

    def designer(_):
        wire = proposal(checks)
        if omit:
            wire["required_cases"].pop(checks[0]["policy_id"])
        return response(wire)

    def target(request):
        data = json.loads(request.content)
        sent.append(data)
        invalid = ("quantity" not in data or type(data["quantity"]) is not int
                   or len(data["name"]) > 20)
        return httpx.Response(422 if invalid else 201, json=dict(id="fresh-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")
    try:
        saved = svc.intake.save(IntakeInput(name="明确场景不能消失",
            document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], REQUEST, uuid4().hex)
        done = await finished(svc, row)
        summary = done["summary"]
        assert summary["coverage"]["requested_check_denominator"] == 3
        assert summary["coverage"]["executed_requested_checks"] == (0 if omit else 3)
        assert summary["complete"] is (not omit)
        assert len(sent) == (0 if omit else 4)
        if omit:
            assert summary["design_records"][-1]["reason"] == "DESIGN_REQUESTED_CHECKS_MISSING"
        else:
            assert all(c["outcome"] == "PASS" for c in summary["cases"])
            assert any("quantity" not in data for data in sent)
            assert any(type(data.get("quantity")) is str for data in sent)
            assert any(len(data["name"]) == 21 for data in sent)
    finally:
        await svc.close()


def test_optional_negative_wire_requires_an_actual_approved_negative_step():
    from test_negative_values import case as read_case
    from test_negative_values import policy, read_project

    proj, _ = read_project()
    base = next(p for p in proj.registry.definitions() if not p.negative)
    context = design_context(proj, "正常读取并检查文档异常", design_groups(proj)[0], 20, 1)
    model = DesignModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    wire = dict(interpretation="正常读取和实际类型拒绝", relevant=True, cases=[read_case(base.id),
        dict(title="类型错误应被拒绝", before=[], after=[],
            check=read_case(policy(proj).id)["plan"]["steps"][0])], questions=[], not_covered=[])
    assert Draft202012Validator(schema).is_valid(wire)
    decoded = json.loads(model._decode_output(json.dumps(wire), context))
    assert decoded["cases"][1]["kind"] == "negative"
    assert decoded["cases"][1]["plan"]["steps"][0]["policy_id"] == policy(proj).id
    fake = copy.deepcopy(wire)
    fake["cases"] = [read_case(base.id)]
    fake["cases"][0]["kind"] = "negative"
    assert not Draft202012Validator(schema).is_valid(fake)
    with pytest.raises(ValidationFailure, match="DESIGN_NEGATIVE_CASE_OUTSIDE_REQUIRED"):
        model._decode_output(json.dumps(fake), context)
