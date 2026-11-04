"""Exact operator literals stay inside an explicit, unchanged negative capability."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from test_product_design import settings
from test_product_focus import focus_response
from test_product_tasks import finished, response, service

from agenttest.contracts import load_openapi
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.negative import select_negative_approval
from agenttest.product_checks import RequestedCheck, check_index, validate_requested_checks
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput
from agenttest.report import Outcome, ReportStore


def read_project(variants=True):
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="Reports", version="1"),
        paths={"/reports": {"get": dict(operationId="readReports", summary="读取报告列表",
            parameters=[dict(name="limit", **{"in": "query"}, schema=dict(
                type="integer", minimum=1, maximum=3)), dict(name="order", **{"in": "query"},
                schema=dict(type="string", enum=["asc", "desc"]))], responses={
                    "200": dict(description="ok", content={"application/json": dict(schema=dict(
                        type="object", required=["ok"],
                        properties=dict(ok=dict(type="boolean"))))}),
                    "422": dict(description="invalid query")})}}))
    contract = load_openapi(document)
    suggestions = analyze_contract(contract)
    operations = [dict(operation_id=s["operation_id"], alias=s["alias"], statuses=s["statuses"],
        arguments=[{k: v for k, v in a.items() if k in {"location", "name", "value",
            "source_operation", "selector", "generated", "needs_input"}} for a in s["arguments"]])
        for s in suggestions]
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998",
        isolated_confirmed=True, assisted=True, approve_negative_tests=True,
        allow_negative_value_variants=variants, operations=operations))
    return build_generic_project("reports", document, contract, setup), setup


def policy(project, name="limit", kind="type_mismatch", value=None):
    return next(p for p in project.registry.definitions() if p.negative
        and p.negative.name == name and p.negative.kind == kind
        and (value is None or p.negative.value == value))


def case(identifier):
    return dict(title="读取报告并核对响应", kind="normal", plan=dict(name="read", steps=[dict(
        id="read", operation_id="readReports", role=None, policy_id=identifier,
        arguments=[dict(location="query", name=k, value=v, from_step=None, select=None)
                   for k, v in dict(limit=1, order="asc").items()])]))


def scenario(identifier, value):
    return Scenario.model_validate(dict(name="exact negative", steps=[dict(id="read",
        operation_id="readReports", policy_id=identifier, query=dict(
            limit=dict(value=1), order=dict(value="asc")), negative_value=dict(value=value))]))


@pytest.mark.parametrize("name,kind,original,value", [
    ("limit", "type_mismatch", None, "abc"),
    ("limit", "boundary_outside", 0, -2),
    ("limit", "boundary_outside", 4, 9),
    ("order", "enum_outside", None, "backwards"),
])
def test_explicit_value_keeps_approval_and_deviation_set(name, kind, original, value):
    p, _ = read_project()
    approval = policy(p, name, kind, original).negative
    before = approval.model_dump_json()
    chosen = select_negative_approval(approval, p.contract.operations[0], value, override=True)
    assert chosen.value == value and chosen.violation_codes == approval.violation_codes
    assert approval.model_dump_json() == before


@pytest.mark.parametrize("change,expected", [
    ("not_authorized", "NEGATIVE_VALUE_VARIANT_NOT_AUTHORIZED"),
    ("normal_policy", "NEGATIVE_VALUE_VARIANT_NOT_AUTHORIZED"),
    ("valid_value", "NEGATIVE_MUTATION_DOES_NOT_MATCH_KIND"),
    ("other_boundary", "NEGATIVE_VALUE_BOUNDARY_DIRECTION_CHANGED"),
    ("query_object", "NEGATIVE_QUERY_VARIANT_UNREPRESENTABLE"),
    ("nonfinite", "NEGATIVE_VALUE_NOT_JSON_DATA"),
])
@pytest.mark.asyncio
async def test_unauthorized_or_changed_value_scope_sends_zero_http(tmp_path, change, expected):
    p, _ = read_project(variants=change != "not_authorized")
    chosen = policy(p)
    value = "abc"
    if change == "normal_policy":
        chosen = next(r for r in p.registry.definitions() if not r.negative)
    elif change == "valid_value":
        value = 2
    elif change == "other_boundary":
        chosen, value = policy(p, kind="boundary_outside", value=0), 4
    elif change == "query_object":
        value = {"not": "a scalar"}
    elif change == "nonfinite":
        value = float("nan")
    sent = []
    result = await Executor(p.target, p.contract, ReportStore(tmp_path),
        httpx.MockTransport(lambda request: sent.append(request)), {}, p.registry).run(
            scenario(chosen.id, value))
    assert not sent and result.outcome in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}
    assert result.diagnostic.code == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_status", [422, 200])
async def test_exact_request_is_sent_without_rewriting_the_expected_status(tmp_path, actual_status):
    p, _ = read_project()
    chosen = policy(p)
    sent = []
    def target(request):
        sent.append(dict(request.url.params))
        return httpx.Response(actual_status, json=dict(ok=True))
    digest = p.registry.digest()
    result = await Executor(p.target, p.contract, ReportStore(tmp_path),
        httpx.MockTransport(target), {}, p.registry).run(scenario(chosen.id, "abc"))
    assert sent == [dict(limit="abc", order="asc")]
    assert result.outcome == (Outcome.PASS if actual_status == 422 else Outcome.ASSERTION_FAILED)
    assert p.registry.digest() == digest
    assert chosen.assertions.status.codes == [422]


@pytest.mark.asyncio
async def test_secret_override_is_rejected_before_any_http_and_never_logged(tmp_path):
    from agenttest.safety import RoleCredential

    p, _ = read_project()
    p.target.roles["tester"] = RoleCredential(bearer_env="AGENTTEST_TARGET_TEST_BEARER")
    secret = "private-unit-negative-secret"
    sent = []
    result = await Executor(p.target, p.contract, ReportStore(tmp_path),
        httpx.MockTransport(lambda request: sent.append(request)),
        {"AGENTTEST_TARGET_TEST_BEARER": secret}, p.registry).run(
            scenario(policy(p).id, secret))
    assert not sent and result.outcome != Outcome.PASS
    assert result.diagnostic.code == "DEPENDENCY_SECRET_VALUE"
    assert secret not in result.model_dump_json()
    assert all(secret.encode() not in f.read_bytes() for f in tmp_path.rglob("*") if f.is_file())


def test_old_defaults_and_ordinary_plan_bytes_do_not_gain_new_authority():
    p, setup = read_project(variants=False)
    assert "allow_negative_value_variants" not in setup.model_dump()
    fixed = policy(p).negative
    assert "allow_value_variants" not in fixed.model_dump()
    plan = scenario(policy(p).id, "abc")
    plan.steps[0].negative_value = None
    assert "negative_value" not in plan.steps[0].model_dump()
    same = select_negative_approval(fixed, p.contract.operations[0], fixed.value, override=True)
    assert same.model_dump_json() == fixed.model_dump_json()


@pytest.mark.asyncio
async def test_a_mutator_that_substitutes_a_default_value_is_stopped_before_http(
        tmp_path, monkeypatch):
    import agenttest.negative as mutations

    p, _ = read_project()
    original = mutations.mutate_request
    def substitute(approval, operation, path, query, payload, media):
        query, payload = original(approval, operation, path, query, payload, media)
        query[approval.name] = "wrong-type"
        return query, payload
    monkeypatch.setattr(mutations, "mutate_request", substitute)
    sent = []
    result = await Executor(p.target, p.contract, ReportStore(tmp_path),
        httpx.MockTransport(lambda request: sent.append(request)), {}, p.registry).run(
            scenario(policy(p).id, "abc"))
    assert not sent and result.outcome != Outcome.PASS
    assert result.diagnostic.code == "DEPENDENCY_EXACT_NEGATIVE_VALUE_CHANGED"


def test_changed_exact_literal_has_a_source_diagnostic():
    p, _ = read_project()
    check = RequestedCheck(policy_id=policy(p).id, source_quote="limit字符串abc",
                           exact_value=dict(value="abc"))
    context = dict(user_task="limit字符串abc", approved_checks=check_index(p))
    validate_requested_checks([check], context, ["readReports"])
    changed = deepcopy(check)
    changed.exact_value.value = "different"
    with pytest.raises(ValueError, match="FOCUS_INPUT_TEXT_CHANGED") as rejected:
        validate_requested_checks([changed], context, ["readReports"])
    position = next(i for i, entry in enumerate(context["approved_checks"])
                    if entry["policy_id"] == check.policy_id)
    assert rejected.value.diagnostic.fields == ["requested_checks.0.exact_value",
                                                 f"approved_checks.{position}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_shape", ["wrong_kind", "numeric_string"])
async def test_one_wrong_negative_kind_does_not_remove_independent_approved_checks(
        tmp_path, monkeypatch, wrong_shape):
    p, setup = read_project()
    normal = next(r for r in p.registry.definitions() if not r.negative)
    wrong = dict(policy_id=(policy(p).id if wrong_shape == "wrong_kind" else
                           policy(p, kind="boundary_outside", value=0).id),
                 source_quote="limit=-2与limit=9",
                 exact_value=dict(value=-2 if wrong_shape == "wrong_kind" else "-2"))
    valid = dict(policy_id=policy(p, kind="boundary_outside", value=4).id,
                 source_quote="limit=-2与limit=9", exact_value=dict(value=9))
    def focus(request):
        return focus_response(dict(interpretation="读取并检查两个明确错误值", mode="selected",
            operation_ids=["readReports"], question=None, requested_checks=[wrong, valid]), request,
            approved_checks=check_index(p))
    def designer(_):
        return response(dict(interpretation="先执行正常读取和独立获准检查", relevant=True,
            cases=[case(normal.id)], questions=[], not_covered=[], required_cases={
                valid["policy_id"]: dict(title="上界外九应拒绝", before=[],
                    check=case(valid["policy_id"])["plan"]["steps"][0], after=[])}))
    sent = []
    def target(request):
        sent.append(dict(request.url.params))
        return httpx.Response(422 if request.url.params["limit"] == "9" else 200,
                              json=dict(ok=True))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile="generic")
    try:
        saved = svc.intake.save(IntakeInput(name="局部受阻继续执行", document=p.document.decode(),
                                          generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "正常读取，并分别检查limit=-2与limit=9。",
                               uuid4().hex)
        done = await finished(svc, row)
        s = done["summary"]
        assert done["status"] == "FINISHED" and not s["complete"]
        assert s["coverage"]["requested_check_denominator"] == 2
        assert s["coverage"]["executed_requested_checks"] == 1
        assert len(s["pending_negative_values"]) == 1
        assert sent == [dict(limit="1", order="asc"), dict(limit="9", order="asc")]
        assert all(c["outcome"] == "PASS" for c in s["cases"])
    finally:
        await svc.close()


def test_boundary_and_enum_values_keep_their_wire_types_without_coercion():
    from jsonschema import Draft202012Validator

    from agenttest.product_focus_wire import operation_checks
    from agenttest.product_inputs import input_fields

    p, _ = read_project()
    context = dict(approved_ids=["readReports"],
        user_task="检查limit=-2和order枚举外字符串backwards",
        input_fields=input_fields(p), approved_checks=check_index(p))
    model = FocusModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    validator = Draft202012Validator(schema)
    mapping = operation_checks(context["approved_checks"])["readReports"]
    reference = next(k for k, v in mapping.items() if v["policy_id"] == policy(
        p, kind="boundary_outside", value=0).id)
    check = dict(check_ref=reference, source_ref="source_1", exact_value=dict(value=-2))
    bundle = dict(input_requirements=[], input_questions=[], expectation_questions=[],
                  requested_checks=[check], test_normal=False, existing_resource=False)
    wire = dict(interpretation="按文档检查明确指定的下界值", scope=dict(mode="selected",
        operations=dict(readReports=bundle), question=None), blocked_operations={})
    assert validator.is_valid(wire)
    check["exact_value"]["value"] = "-2"
    assert not validator.is_valid(wire)
    check["check_ref"] = next(k for k, v in mapping.items() if v["policy_id"] == policy(
        p, name="order", kind="enum_outside").id)
    check["exact_value"]["value"] = "backwards"
    assert validator.is_valid(wire)
    check["exact_value"]["value"] = True
    assert not validator.is_valid(wire)


@pytest.mark.asyncio
@pytest.mark.parametrize("variants", [False, True])
async def test_task_holds_unapproved_value_or_runs_exactly_and_replays_without_model(
        tmp_path, monkeypatch, variants):
    p, setup = read_project(variants)
    normal = next(r for r in p.registry.definitions() if not r.negative)
    check = dict(policy_id=policy(p).id, source_quote="limit字符串abc",
                 exact_value=dict(value="abc"))
    def focus(request):
        return focus_response(dict(interpretation="读取报告并检查指定的错误值", mode="selected",
            operation_ids=["readReports"], question=None, requested_checks=[check]), request,
            approved_checks=check_index(p))
    def designer(_):
        wire = dict(interpretation="正常读取，受阻部分等待明确授权", relevant=True,
                    cases=[case(normal.id)], questions=[], not_covered=[])
        if variants:
            wire["required_cases"] = {check["policy_id"]: dict(title="原样验证abc", before=[],
                check=case(check["policy_id"])["plan"]["steps"][0], after=[])}
        return response(wire)
    sent = []
    def target(request):
        query = dict(request.url.params)
        sent.append(query)
        return httpx.Response(422 if query["limit"] == "abc" else 200, json=dict(ok=True))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile="generic")
    try:
        saved = svc.intake.save(IntakeInput(name="精确异常值", document=p.document.decode(),
                                          generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "正常读取报告，再用limit字符串abc检查422。",
                               uuid4().hex)
        done = await finished(svc, row)
        s = done["summary"]
        assert s["coverage"]["requested_check_denominator"] == 1
        assert s["coverage"]["executed_requested_checks"] == int(variants)
        assert all(q["limit"] != "wrong-type" for q in sent)
        assert s["complete"] is variants
        if not variants:
            assert len(sent) == 1 and s["pending_negative_values"][0]["requested_value"] == "abc"
            assert any("尚未授权" in q["question"] for q in s["questions"])
        else:
            assert sent == [dict(limit="1", order="asc"), dict(limit="abc", order="asc")]
            negative = next(c for c in s["cases"] if c.get("approved_negative_steps"))
            svc.export_regression(row["id"], "operator", negative["run_id"])
            assert svc.saved.list("operator")[0]["title"] == "原样验证abc"
            repeated = await svc.saved.submit("operator", row["id"], negative["run_id"],
                                              uuid4().hex)
            fixed = await finished(svc, repeated)
            assert fixed["summary"]["fixed_regression_passed"]
            assert fixed["summary"]["context_usage"]["model_calls"] == 0
            assert sent[-1] == dict(limit="abc", order="asc")
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_missing_literal_never_sends_the_default_bad_value_and_independent_work_continues(
        tmp_path, monkeypatch):
    p, setup = read_project()
    normal = next(r for r in p.registry.definitions() if not r.negative)
    quote = 'limit使用“fern_units”，order使用“oblique_order”'
    missing = dict(policy_id=policy(p).id, source_quote=quote)
    explicit = dict(policy_id=policy(p, name="order", kind="enum_outside").id,
                    source_quote=quote, exact_value=dict(value="oblique_order"))
    def focus(request):
        return focus_response(dict(interpretation="正常读取并核对两个指定错误值", mode="selected",
            operation_ids=["readReports"], normal_operation_ids=["readReports"], question=None,
            requested_checks=[missing, explicit]), request, approved_checks=check_index(p))
    def designer(_):
        return response(dict(interpretation="遗漏的字符串等待，正常与另一异常继续", relevant=True,
            cases=[case(normal.id)], questions=[], not_covered=[], required_cases={
                explicit["policy_id"]: dict(title="枚举外值应拒绝", before=[], after=[],
                    check=case(explicit["policy_id"])["plan"]["steps"][0])}))
    sent = []
    def target(request):
        sent.append(dict(request.url.params))
        return httpx.Response(422 if request.url.params["order"] == "oblique_order" else 200,
                              json=dict(ok=True))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(
        settings(), transport=httpx.MockTransport(focus), profile="generic")
    try:
        saved = svc.intake.save(IntakeInput(name="明确错误值不能省略",
            document=p.document.decode(), generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "先正常读取，再分别检查" + quote,
                               uuid4().hex)
        done = await finished(svc, row)
        summary = done["summary"]
        assert done["status"] == "FINISHED" and not summary["complete"]
        assert summary["pending_negative_values"][0]["reason"] == "NEGATIVE_LITERAL_NOT_PRESERVED"
        assert summary["coverage"]["executed_requested_checks"] == 1
        assert sent == [dict(limit="1", order="asc"), dict(limit="1", order="oblique_order")]
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_fixed_valid_baseline_has_independent_explicit_negative_authority(tmp_path):
    proj, setup = read_project()
    argument = setup.operations[0].arguments[0]
    argument.generated = False
    argument.value = 1
    fixed = build_generic_project("reports", proj.document.decode(), proj.contract, setup)
    assert not any(p.negative and p.negative.name == "limit" for p in fixed.registry.definitions())
    argument.negative_enabled = True
    enabled = build_generic_project("reports", proj.document.decode(), proj.contract, setup)
    approved = policy(enabled, kind="boundary_outside", value=0)
    baseline = next(p for p in enabled.registry.definitions()
                    if p.id == approved.negative.base_policy_id)
    assert baseline.binding_rules[0].literal_schema["enum"] == [1]
    seen = []

    def target(request):
        seen.append(dict(request.url.params))
        return httpx.Response(422, json={"error": "out of range"})

    report = await Executor(enabled.target, enabled.contract, ReportStore(tmp_path),
        registry=enabled.registry, environ={}, transport=httpx.MockTransport(target)).run(
            scenario(approved.id, 0))
    assert report.outcome == Outcome.PASS
    assert seen == [dict(limit="0", order="asc")]
    assert baseline.binding_rules[0].literal_schema["enum"] == [1]


def test_explicit_numeric_negative_field_cannot_change_even_when_value_is_identical():
    from agenttest.diagnostics import ValidationFailure

    index = [dict(policy_id="page_bad", operation_id="list", name="page", invalid_value=0)]
    context = dict(user_task="检查size=0按文档拒绝", approved_checks=index)
    for value in (None, dict(value=0)):
        check = RequestedCheck(policy_id="page_bad", source_quote=context["user_task"],
            exact_value=value)
        with pytest.raises(ValidationFailure, match="FOCUS_NEGATIVE_FIELD_CHANGED"):
            validate_requested_checks([check], context, ["list"])
    index[0]["name"] = "size"
    validate_requested_checks([check], context, ["list"])
