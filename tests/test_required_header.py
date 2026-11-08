"""Required header parameters: contract, approval, grading, wire and idempotency."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from agenttest.contracts import ContractSummary, Operation, load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.dsl import AssertionPolicy, Scenario
from agenttest.executor import Executor
from agenttest.policies import BindingRule, PolicyRegistry, RegisteredPolicy
from agenttest.report import Outcome, ReportStore, RunReport
from agenttest.request_validation import (
    public_headers,
    request_schemas,
    validate_request,
)
from agenttest.safety import RoleCredential, TargetConfig

ROOT = Path(__file__).resolve().parents[1]


def load_fixture():
    spec = importlib.util.spec_from_file_location(
        "rw_header_fixture", ROOT / "scripts/rw_lifecycle/write_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KEY = dict(name="Idempotency-Key", **{"in": "header"}, required=True,
           schema=dict(type="string", pattern=r"[A-Za-z0-9_-]{8,64}"))


def operation(method="POST", parameters=(KEY,), security=()):
    return Operation(method=method, path="/items", operation_id="items",
                     parameters=list(parameters), security=list(security))


def contract(**kwargs):
    schemes = {"bearer": {"type": "http", "scheme": "bearer"}}
    return ContractSummary(version="3.1.0", operations=[operation(**kwargs)],
                           security_schemes=schemes)


def registry(header_rule=True, roles=(None,), closed=False):
    rules = [BindingRule(location="headers", name="Idempotency-Key",
                         literal_schema=dict(type="string"))] if header_rule else []
    return PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=list(roles), business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator header fixture",
                                   status={"codes": [200, 201]}),
        binding_rules=rules, closed_arguments=closed)])


def executor(tmp_path, handler, *, header_rule=True, roles=(None,), closed=False,
             config=None):
    item = contract()
    settings = TargetConfig(environment="isolated", allow_mutations=True,
                            allowed_operations=["items"], min_request_interval=0.0,
                            **(config or {}))
    return Executor(settings, item, ReportStore(tmp_path), httpx.MockTransport(handler),
                    registry=registry(header_rule, roles, closed))


def plan(**step):
    return Scenario.model_validate({"name": "header", "steps": [{
        "id": "create", "operation_id": "items", "policy_id": "items_v1", **step}]})


def test_required_header_is_validated_case_insensitively():
    item = operation()
    parameters, _ = request_schemas(item, None)
    assert set(parameters["header"]) == {"Idempotency-Key"}
    validate_request(item, {}, {}, {}, None, {"idempotency-key": "key-aaaa1111"})
    for headers, code in (({}, "OPENAPI_PARAMETER_REQUIRED"),
                          ({"X-Trace": "k"}, "OPENAPI_UNKNOWN_PARAMETER")):
        with pytest.raises(ValidationFailure) as error:
            validate_request(item, {}, {}, {}, None, headers)
        assert error.value.diagnostic.code == code


def test_reserved_and_malformed_header_parameters_are_rejected():
    for name, code in (("Authorization", "OPENAPI_RESERVED_HEADER_PARAMETER"),
                       ("Cookie", "OPENAPI_RESERVED_HEADER_PARAMETER"),
                       ("bad name", "OPENAPI_INVALID_HEADER_NAME")):
        item = operation(parameters=(dict(name=name, **{"in": "header"}, required=True,
                                          schema=dict(type="string")),))
        with pytest.raises(ValidationFailure) as error:
            request_schemas(item, None)
        assert error.value.diagnostic.code == code


def test_required_header_survives_openapi_import():
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="Seals", version="1"),
                               paths={"/items": {"post": dict(operationId="items", parameters=[
                                   {"name": "Idempotency-Key", "in": "header", "required": True,
                                    "schema": {"type": "string"}}], responses={
                                        "201": {"description": "created"}})}}))
    item, = load_openapi(document).operations
    parameters, _ = request_schemas(item, None)
    assert set(parameters["header"]) == {"Idempotency-Key"}
    validate_request(item, {}, {}, {}, None, {"Idempotency-Key": "key-aaaa1111"})


@pytest.mark.asyncio
async def test_unapproved_header_is_never_injected(tmp_path):
    calls = []
    run = executor(tmp_path, lambda request: calls.append(request) or httpx.Response(201),
                   header_rule=False)
    step = plan(headers={"Idempotency-Key": {"value": "key-aaaa1111"}}).steps[0]
    with pytest.raises(Exception) as error:
        registry(header_rule=False).resolve(step, "default")
    assert error.value.diagnostic.code == "POLICY_ARGUMENT_MISMATCH"
    report = await run.run(plan(headers={"Idempotency-Key": {"value": "key-aaaa1111"}}))
    assert report.outcome == Outcome.NEEDS_REVIEW
    assert calls == []


@pytest.mark.asyncio
async def test_approved_header_reaches_the_wire_with_declared_spelling(tmp_path):
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(201, json={"id": 1})

    run = executor(tmp_path, handler)
    report = await run.run(plan(headers={"idempotency-key": {"value": "key-aaaa1111"}}))
    assert report.outcome == Outcome.PASS, report.reason
    assert seen["headers"]["idempotency-key"] == "key-aaaa1111"

    def identified(request):
        seen["auth"] = (request.headers.get("authorization"), request.headers.get("x-trace"))
        return httpx.Response(201)

    traced = contract(parameters=(dict(name="X-Trace", **{"in": "header"}, required=True,
                                       schema=dict(type="string")),),
                      security=({"bearer": []},))
    policy = PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=["tester"], business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator header fixture",
                                   status={"codes": [201]}),
        binding_rules=[BindingRule(location="headers", name="X-Trace",
                                   literal_schema=dict(type="string"))])])
    settings = TargetConfig(environment="isolated", allow_mutations=True,
                            allowed_operations=["items"], min_request_interval=0.0,
                            roles={"tester": RoleCredential(
                                bearer_env="AGENTTEST_TARGET_HEADER_TEST")})
    traced_run = Executor(settings, traced, ReportStore(tmp_path),
                          httpx.MockTransport(identified), registry=policy)
    traced_run.environ = {"AGENTTEST_TARGET_HEADER_TEST": "private-header-token"}
    traced_report = await traced_run.run(plan(role="tester",
                                              headers={"X-Trace": {"value": "trace-1"}}))
    assert traced_report.outcome == Outcome.PASS, traced_report.reason
    assert seen["auth"] == ("Bearer private-header-token", "trace-1")


@pytest.mark.asyncio
async def test_header_cannot_replace_the_authorization_identity(tmp_path):
    calls = []
    item = contract(parameters=(dict(name="X-Trace", **{"in": "header"}, required=False,
                                     schema=dict(type="string")),))
    policy = PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=[None], business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator header fixture",
                                   status={"codes": [201]}))])
    settings = TargetConfig(environment="isolated", allow_mutations=True,
                            allowed_operations=["items"], min_request_interval=0.0)
    run = Executor(settings, item, ReportStore(tmp_path),
                   httpx.MockTransport(lambda request: calls.append(request)
                                       or httpx.Response(201)), registry=policy)
    report = await run.run(plan(headers={"Authorization": {"value": "Bearer injected"}}))
    assert report.outcome == Outcome.TOOL_ERROR and calls == []
    assert report.reason == "PLAN_REJECTED"
    assert report.diagnostic.code == "OPENAPI_UNKNOWN_PARAMETER"


@pytest.mark.asyncio
async def test_secret_header_value_is_refused(tmp_path):
    calls = []
    item = contract(parameters=(dict(name="X-Trace", **{"in": "header"}, required=True,
                                     schema=dict(type="string")),),
                    security=({"bearer": []},))
    token = "private-header-token"
    policy = registry_with_role()
    settings = TargetConfig(environment="isolated", allow_mutations=True,
                            allowed_operations=["items"], min_request_interval=0.0,
                            roles={"tester": RoleCredential(
                                bearer_env="AGENTTEST_TARGET_HEADER_TEST")})
    run = Executor(settings, item, ReportStore(tmp_path),
                   httpx.MockTransport(lambda request: calls.append(request)
                                       or httpx.Response(201)), registry=policy)
    run.environ = {"AGENTTEST_TARGET_HEADER_TEST": token}
    report = await run.run(plan(role="tester", headers={"X-Trace": {"value": token}}))
    assert report.outcome == Outcome.TOOL_ERROR and calls == []
    assert report.diagnostic.code == "DEPENDENCY_SECRET_VALUE"


def registry_with_role():
    return PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=["tester"], business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator header fixture",
                                   status={"codes": [201]}),
        binding_rules=[BindingRule(location="headers", name="X-Trace",
                                   literal_schema=dict(type="string"))])])


def test_sensitive_header_values_are_redacted():
    item = operation(parameters=(dict(name="X-Api-Key", **{"in": "header"}, required=True,
                                      schema=dict(type="string", format="password")),))
    public = public_headers(item, {"X-Api-Key": "unit-secret-value", "Idempotency-Key": "k-1"})
    assert public["X-Api-Key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_sensitive_header_never_persists_in_evidence(tmp_path):
    item = contract(parameters=(dict(name="X-Api-Key", **{"in": "header"}, required=True,
                                     schema=dict(type="string", format="password")),))
    policy = PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=[None], business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator header fixture",
                                   status={"codes": [200]}),
        binding_rules=[BindingRule(location="headers", name="X-Api-Key",
                                   literal_schema=dict(type="string"))])])
    settings = TargetConfig(environment="isolated", allow_mutations=True,
                            allowed_operations=["items"], min_request_interval=0.0)
    run = Executor(settings, item, ReportStore(tmp_path),
                   httpx.MockTransport(lambda request: httpx.Response(200, json={})),
                   registry=policy)
    draft = RunReport(scenario_sha256="0" * 64, contract_sha256="0" * 64,
                      environment="isolated", target_origin="http://127.0.0.1:19097",
                      task_mode="model_designed")
    report = await run.run(plan(headers={"X-Api-Key": {"value": "unit-secret-value"}}),
                           report=draft)
    raw = (tmp_path / report.run_id / "report.json").read_text(encoding="utf-8")
    assert "unit-secret-value" not in raw
    evidence = report.steps[0].request_evidence
    assert evidence and "X-Api-Key" not in json.dumps(evidence, ensure_ascii=False)


@pytest.mark.asyncio
async def test_ordinary_header_value_is_visible_in_evidence(tmp_path):
    item = contract(parameters=(dict(name="X-Trace", **{"in": "header"}, required=True,
                                     schema=dict(type="string")),))
    policy = PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=[None], business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator header fixture",
                                   status={"codes": [201]}),
        binding_rules=[BindingRule(location="headers", name="X-Trace",
                                   literal_schema=dict(type="string"))])])
    settings = TargetConfig(environment="isolated", allow_mutations=True,
                            allowed_operations=["items"], min_request_interval=0.0)
    run = Executor(settings, item, ReportStore(tmp_path),
                   httpx.MockTransport(lambda request: httpx.Response(201)), registry=policy)
    draft = RunReport(scenario_sha256="0" * 64, contract_sha256="0" * 64,
                      environment="isolated", target_origin="http://127.0.0.1:19097",
                      task_mode="model_designed")
    report = await run.run(plan(headers={"X-Trace": {"value": "trace-visible"}}), report=draft)
    evidence = json.dumps(report.steps[0].request_evidence, ensure_ascii=False)
    assert '"X-Trace": "trace-visible"' in evidence


@pytest.mark.asyncio
async def test_unknown_write_outcome_is_not_replayed(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("connection dropped after send", request=request)

    run = executor(tmp_path, handler)
    report = await run.run(plan(headers={"Idempotency-Key": {"value": "key-aaaa1111"}}))
    assert len(calls) == 1
    assert report.steps[0].attempts and len(report.steps[0].attempts) == 1
    assert report.steps[0].attempts[0].error_code


@pytest.mark.asyncio
async def test_idempotency_key_replay_returns_the_same_resource():
    fixture = load_fixture()
    app = fixture.create_fixture("reset-token", {"author": "author-token"})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:19097") as client:
        await client.post("/__namespace", json=dict(namespace="ns-header"),
                          headers={"x-reset-token": "reset-token"})
        auth = {"Authorization": "Bearer author-token"}
        first = await client.post("/items", json=dict(name="probe"),
                                  headers={**auth, "Idempotency-Key": "key-aaaa1111"})
        replay = await client.post("/items", json=dict(name="probe"),
                                   headers={**auth, "Idempotency-Key": "key-aaaa1111"})
        conflict = await client.post("/items", json=dict(name="other"),
                                     headers={**auth, "Idempotency-Key": "key-aaaa1111"})
        fresh = await client.post("/items", json=dict(name="probe"),
                                  headers={**auth, "Idempotency-Key": "key-bbbb2222"})
    assert (first.status_code, replay.status_code) == (201, 200)
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    assert fresh.status_code == 201 and fresh.json()["id"] != first.json()["id"]


@pytest.mark.asyncio
async def test_query_body_and_path_behaviour_is_unchanged(tmp_path):
    item = ContractSummary(version="3.1.0", operations=[Operation(
        method="POST", path="/items/{itemId}", operation_id="items", parameters=[
            dict(name="itemId", **{"in": "path"}, required=True, schema=dict(type="string")),
            dict(name="dry", **{"in": "query"}, schema=dict(type="boolean"))])])
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["json"] = request.content
        return httpx.Response(201)

    policy = PolicyRegistry([RegisteredPolicy(
        id="items_v1", operation_id="items", roles=[None], business_scenario="default",
        assertions=AssertionPolicy(reviewed=True, source="operator header fixture",
                                   status={"codes": [201]}))])
    settings = TargetConfig(environment="isolated", allow_mutations=True,
                            allowed_operations=["items"], min_request_interval=0.0)
    run = Executor(settings, item, ReportStore(tmp_path), httpx.MockTransport(handler),
                   registry=policy)
    report = await run.run(Scenario.model_validate({"name": "plain", "steps": [{
        "id": "create", "operation_id": "items", "policy_id": "items_v1",
        "path_parameters": {"itemId": {"value": "itm-1"}},
        "query": {"dry": {"value": True}}}]}))
    assert report.outcome == Outcome.PASS, report.reason
    assert "/items/itm-1?dry=true" in seen["url"]


def test_header_field_does_not_change_header_free_plan_fingerprints():
    """Exported regression assets compare fingerprints, so an empty field stays invisible."""
    from agenttest.report import fingerprint

    plain = plan()
    assert "headers" not in plain.steps[0].model_dump()
    assert fingerprint(plain.model_dump()) == fingerprint(
        {"name": "header", "steps": [{key: value for key, value in
                                      plain.steps[0].model_dump().items()}]})
    traced = plan(headers={"X-Trace": {"value": "trace-1"}})
    assert traced.steps[0].model_dump()["headers"] == {
        "X-Trace": {"value": "trace-1", "from_step": None, "select": "$"}}
