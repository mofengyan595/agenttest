import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_product_design import settings
from test_product_tasks import finished, response, service

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.product_checks import (
    RequestedCheck,
    check_index,
    partition_requested_checks,
    requested_invalid_value,
    validate_requested_checks,
)
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput
from agenttest.report import ModelCallStats


def text_project(variants=True):
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="Text lookup", version="1"),
        paths={"/lookup": {"get": dict(operationId="lookup", parameters=[dict(name="q",
            **{"in": "query"}, required=True,
            schema=dict(type="string", minLength=1, maxLength=4))],
            responses={"200": dict(description="ok"), "422": dict(description="invalid")})}}))
    contract = load_openapi(document)
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998",
        isolated_confirmed=True, assisted=True, approve_negative_tests=True,
        allow_negative_value_variants=variants, operations=[dict(operation_id="lookup",
            alias="文本查询", statuses=[200], arguments=[dict(location="query", name="q",
                                                          value="ok", generated=True)])]))
    project = build_generic_project("length", document, contract, setup)
    policy = next(p for p in project.registry.definitions() if p.negative
                  and p.negative.kind == "boundary_outside" and len(p.negative.value) > 4)
    return project, setup, policy


@pytest.mark.parametrize("variants", [False, True])
def test_exact_length_obeys_source_and_existing_variation_authority(variants):
    project, _, policy = text_project(variants)
    check = RequestedCheck(policy_id=policy.id, source_quote="查询q长度7", exact_length=7)
    before = project.registry.digest()
    context = dict(user_task=check.source_quote, approved_checks=check_index(project))
    validate_requested_checks([check], context, ["lookup"])
    assert requested_invalid_value(check, policy) == "x" * 7
    executable, pending = partition_requested_checks(project, [check.model_dump()])
    assert bool(executable) == variants and bool(pending) != variants
    assert project.registry.digest() == before
    if pending:
        assert pending[0]["requested_value"] == "x" * 7
    default = check.model_copy(update={"exact_length": len(policy.negative.value),
        "source_quote": f"查询q长度{len(policy.negative.value)}"})
    validate_requested_checks([default], dict(user_task=default.source_quote,
        approved_checks=check_index(project)), ["lookup"])
    assert requested_invalid_value(default, policy) == policy.negative.value
    assert partition_requested_checks(project, [default.model_dump()])[0]


@pytest.mark.parametrize("change", [dict(exact_length=8), dict(exact_value=dict(value="xxxxxxx")),
                                  dict(policy="wrong_type")])
def test_length_cannot_change_source_or_mutation_kind(change):
    change = dict(change)
    project, _, policy = text_project()
    if change.pop("policy", None):
        policy = next(p for p in project.registry.definitions() if p.negative
                      and p.negative.kind == "type_mismatch")
    check = RequestedCheck.model_validate(dict(policy_id=policy.id,
        source_quote="查询q长度7", exact_length=7, **change)) if "exact_length" not in change else (
        RequestedCheck(policy_id=policy.id, source_quote="查询q长度7", **change))
    with pytest.raises(ValueError):
        validate_requested_checks([check], dict(user_task="查询q长度7",
            approved_checks=check_index(project)), ["lookup"])


def test_length_wire_is_available_only_for_string_boundary_check():
    project, _, policy = text_project()
    model = FocusModel(settings(), profile="generic")
    context = dict(approved_ids=["lookup"], user_task="查询q长度7", unavailable_operations=[],
        approved_checks=check_index(project), input_fields=[], permission_question_roles={})
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    from agenttest.product_focus_wire import operation_checks
    checks = operation_checks(context["approved_checks"])["lookup"]
    ref = next(ref for ref, row in checks.items() if row["policy_id"] == policy.id)
    rows = [row for definition in schema["$defs"].values()
            for row in definition.get("properties", {}).get("requested_checks", {}).get(
                "items", {}).get("anyOf", [])]
    branch = next(row for row in rows if ref in row["properties"]["check_ref"]["enum"])
    assert "exact_length" in branch["properties"]
    for row in rows:
        if "exact_length" in row["properties"]:
            assert all(checks[ref]["kind"] == "boundary_outside" for ref in row[
                "properties"]["check_ref"]["enum"])


@pytest.mark.asyncio
async def test_exact_length_is_executed_and_fixed_replayed_without_model(tmp_path, monkeypatch):
    project, setup, negative = text_project()
    sent = []
    check = dict(policy_id=negative.id, source_quote="查询q长度7，按文档422检查", exact_length=7)

    class Focus:
        stats = ModelCallStats(calls=1, input_tokens=1, output_tokens=1, total_tokens=2)
        async def generate(self, context):
            return json.dumps(dict(interpretation="按指定长度检查输入拒绝", mode="selected",
                operation_ids=["lookup"], normal_operation_ids=[], question=None,
                pending_questions=[], not_covered=[], requested_checks=[check]))

    def design(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert context["requested_checks"][0]["exact_length"] == 7
        assert context["requested_checks"][0]["exact_value_authorized"]
        return response(dict(interpretation="准备合法基线，由执行器变更为指定长度", cases=[],
            required_cases={negative.id: dict(title="查询长度7应被拒绝", before=[], after=[],
                check=dict(id="invalid", operation_id="lookup", role=None, policy_id=negative.id,
                    arguments=[dict(location="query", name="q", value="ok",
                                    from_step=None, select=None)]))}, questions=[], not_covered=[]))

    def target(request):
        sent.append(request.url.params["q"])
        return httpx.Response(422 if len(sent[-1]) == 7 else 200, json={})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, design)
    svc.focus_factory = lambda _: Focus()
    try:
        saved = svc.intake.save(IntakeInput(name="指定文本长度", document=project.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], check["source_quote"], uuid4().hex)
        done = await finished(svc, row)
        assert done["summary"]["complete"], done["summary"]
        case = done["summary"]["cases"][0]
        assert case["approved_negative_steps"] == case["negative_rejections_passed"] == 1
        assert sent == ["x" * 7]
        svc.export_regression(row["id"], "operator", case["run_id"])
        fixed = await svc.saved.submit("operator", row["id"], case["run_id"], uuid4().hex)
        replay = await finished(svc, fixed)
        assert replay["summary"]["fixed_regression_passed"]
        assert replay["summary"]["context_usage"]["model_calls"] == 0
        assert sent == ["x" * 7, "x" * 7]
    finally:
        await svc.close()


def test_query_wrong_type_wire_keeps_scalar_values_without_fabricated_arrays():
    from agenttest.product_checks import exact_check_shape

    project, _, _ = text_project()
    entry = next(e for e in check_index(project) if e["kind"] == "type_mismatch")
    schema = exact_check_shape(entry, {})
    validator = Draft202012Validator(schema)
    for value in ["literal", 7, 0.5, False]:
        assert validator.is_valid(dict(value=value))
    assert validator.is_valid(None)
    assert not validator.is_valid(dict(value={"array": [-11]}))
    assert not validator.is_valid(dict(value=[-11]))
    assert not validator.is_valid(dict(value=None))
