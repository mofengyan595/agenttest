"""Common contract patterns remain enforceable from intake through execution inputs."""

import json

import httpx
import pytest
from support import registry

from agenttest.contracts import ContractSummary, Operation, load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.discovery_schema import witness
from agenttest.dsl import Scenario, Step
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.policies import NeedsReview
from agenttest.product_schema import literal_schema
from agenttest.report import Outcome, ReportStore
from agenttest.request_validation import supported_schema, validate_value
from agenttest.safety import TargetConfig


@pytest.mark.parametrize("pattern,value", [
    ("STUDENT|TEACHER|ADMIN", "ADMIN"),
    ("STUDENT|TEACHER|ADMIN", "prefixADMINsuffix"),
    ("[A-Za-z0-9_]+", "sample_name_12"),
    ("^[a-z]{3,8}$", "alice"),
    ("^[0-9]{2,}$", "1234"),
    ("^[A-Z_]*$", ""),
    ("^老师$", "老师"),
    ("^(?:READER|EDITOR)$", "READER"),
    ("^(READER|EDITOR)$", "EDITOR"),
])
def test_supported_patterns_keep_json_schema_search_semantics(pattern, value):
    validate_value(value, {"type": "string", "pattern": pattern})


@pytest.mark.parametrize("pattern,value", [
    ("STUDENT|TEACHER|ADMIN", "OWNER"),
    ("^[A-Za-z0-9_]+$", "bad/name"),
    ("^[a-z]{3,8}$", "Al"),
    ("^[a-z]{3,8}$", "alice\n"),
    ("^[0-9]{2,4}$", "12345"),
    ("^(?:READER|EDITOR)$", "prefixREADER"),
])
def test_invalid_pattern_values_cannot_reach_the_executor(pattern, value):
    with pytest.raises(ValidationFailure) as error:
        validate_value(value, {"type": "string", "pattern": pattern})
    assert error.value.diagnostic.code == "OPENAPI_VALUE_PATTERN"


@pytest.mark.parametrize("pattern", [
    "(a+)+$", "[a-z]+[0-9]+", r"(a)\1", "(?=a)a", "[z-a]+", "[A-z]+",
    "[a-z]{2000}", "[a-z]{4,2}", "[]+", "A||B", "a" * 161, 42,
])
def test_unsupported_or_unbounded_regex_forms_still_stop_explicitly(pattern):
    with pytest.raises(NeedsReview, match="UNSUPPORTED_REQUEST_PATTERN"):
        supported_schema({"type": "string", "pattern": pattern})


@pytest.mark.parametrize("schema", [
    {"type": "string", "pattern": "STUDENT|TEACHER|ADMIN"},
    {"type": "string", "pattern": "[A-Za-z0-9_]+", "minLength": 3, "maxLength": 32},
    {"type": "string", "pattern": "^[0-9]{3,8}$", "minLength": 4},
])
def test_suggestions_and_model_wire_constraints_preserve_the_pattern(schema):
    value = witness(schema)
    validate_value(value, schema)
    assert literal_schema(schema)["pattern"] == schema["pattern"]


def test_pattern_intake_is_generic_and_does_not_drop_the_validation_rule():
    document = json.dumps({"openapi": "3.1.0", "info": {"title": "Members", "version": "1"},
        "paths": {"/members": {"get": {"operationId": "listMembers", "parameters": [
            {"name": "scope", "in": "query", "schema": {
                "type": "string", "pattern": "READER|EDITOR"}}],
            "responses": {"200": {"description": "ok"}}}}}})
    contract = load_openapi(document)
    suggestions = analyze_contract(contract)
    assert suggestions[0]["supported"]
    field = suggestions[0]["arguments"][0]
    assert field["value"] in {"READER", "EDITOR"}
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18999",
        isolated_confirmed=True, assisted=True, operations=[dict(operation_id="listMembers",
            alias="查询成员", statuses=[200], arguments=[dict(location="query", name="scope",
                generated=True, value=field["value"])])]))
    project = build_generic_project("web_members", document, contract, setup)
    rule = project.registry.definitions()[0].binding_rules[0].literal_schema
    assert rule["pattern"] == "READER|EDITOR"
    with pytest.raises(ValidationFailure):
        validate_value("OWNER", rule)


@pytest.mark.asyncio
async def test_pattern_argument_is_rejected_before_any_http(tmp_path):
    calls = []
    contract = ContractSummary(version="3.1.0", operations=[Operation(method="GET",
        path="/items", operation_id="items", parameters=[{"name": "scope", "in": "query",
            "schema": {"type": "string", "pattern": "^READER$"}}])])
    executor = Executor(TargetConfig(allowed_operations=["items"]), contract,
        ReportStore(tmp_path), httpx.MockTransport(lambda request: calls.append(request)),
        registry=registry())
    report = await executor.run(Scenario(name="invalid scope", steps=[Step(id="read",
        operation_id="items", policy_id="items_v1", query={"scope": {"value": "OWNER"}})]))
    assert report.outcome == Outcome.TOOL_ERROR and not calls
