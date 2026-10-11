import httpx
import pytest
from support import registry

from agenttest.contracts import ContractSummary, Operation
from agenttest.dsl import Scenario, Step
from agenttest.executor import Executor
from agenttest.policies import NeedsReview
from agenttest.report import Outcome, ReportStore
from agenttest.request_validation import validate_request, validate_value
from agenttest.safety import TargetConfig


@pytest.mark.parametrize("schema,value", [
    ({"type": "integer"}, True), ({"type": "integer"}, "1"),
    ({"type": "string", "minLength": 2}, "a"),
    ({"type": "string", "maxLength": 2}, "abc"),
    ({"type": "string", "enum": ["a", "b"]}, "c"),
    ({"type": "integer", "minimum": 2}, 1), ({"type": "number", "maximum": 2}, 2.1),
    ({"type": "number", "exclusiveMinimum": 1}, 1),
    ({"type": "number", "exclusiveMaximum": 2}, 2),
    ({"type": "integer", "minimum": 1, "exclusiveMinimum": True}, 1),
    ({"type": "integer", "format": "int32"}, 2**31),
    ({"type": "number"}, float("nan")),
    ({"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}, {}),
    ({"type": "object", "properties": {}}, {"unknown": 1}),
    ({"type": "object", "properties": {"x": {"type": "string", "readOnly": True}}}, {"x": "a"}),
    ({"type": "array", "items": {"type": "integer"}, "minItems": 1}, []),
    ({"type": "array", "items": {"type": "integer"}}, ["a"]),
    ({"type": "string", "nullable": True, "enum": ["a"]}, None),
])
def test_common_constraints_reject_invalid_values(schema, value):
    with pytest.raises(ValueError):
        validate_value(value, schema)


@pytest.mark.parametrize("schema,value", [
    ({"type": "integer", "minimum": 1, "maximum": 2}, 1),
    ({"type": "number", "exclusiveMinimum": 1, "exclusiveMaximum": 2}, 1.5),
    ({"type": "string", "minLength": 1, "maxLength": 2, "enum": ["中"]}, "中"),
    ({"type": "boolean"}, False), ({"type": "string", "nullable": True}, None),
    ({"type": "object", "properties": {"x": {"type": "array", "items": {"type": "integer"}}},
      "required": ["x"], "additionalProperties": False}, {"x": [1, 2]}),
])
def test_valid_boundaries_and_nested_values(schema, value):
    validate_value(value, schema)


@pytest.mark.parametrize("schema", [
    {}, {"type": ["string", "null"]}, {"oneOf": [{"type": "string"}]},
    {"type": "string", "pattern": "a+"}, {"type": "string", "format": "email"},
    {"type": "object", "additionalProperties": True},
    {"type": "object", "properties": {"nested": {"anyOf": []}}},
    {"type": "string", "minimum": 1},
    {"type": "string", "contentMediaType": "application/json"},
])
def test_unsupported_schema_explicitly_needs_review(schema):
    with pytest.raises(NeedsReview):
        validate_value("a", schema)


def test_parameter_names_required_and_types():
    operation = Operation(method="GET", path="/items/{id}", operation_id="items", parameters=[
        {"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}},
        {"in": "query", "name": "page", "schema": {"type": "integer", "minimum": 1}},
    ])
    validate_request(operation, {"id": 1}, {"page": 1}, {}, None)
    for path, query in [({}, {}), ({"id": "1"}, {}), ({"id": 1}, {"unknown": 1}),
                        ({"id": 1}, {"page": 0})]:
        with pytest.raises(ValueError):
            validate_request(operation, path, query, {}, None)


@pytest.mark.asyncio
async def test_dynamic_binding_is_validated_before_second_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"page": -1})

    contract = ContractSummary(version="3.1.0", operations=[Operation(
        method="GET", path="/items", operation_id="items", parameters=[
            {"in": "query", "name": "page", "schema": {"type": "integer", "minimum": 1}},
        ],
    )])
    executor = Executor(TargetConfig(allowed_operations=["items"]), contract,
                        ReportStore(tmp_path), httpx.MockTransport(handler), registry=registry())
    report = await executor.run(Scenario(name="dynamic", steps=[
        Step(id="first", operation_id="items", policy_id="items_v1"),
        Step(id="second", operation_id="items", policy_id="items_v1",
             query={"page": {"from_step": "first", "select": "$.page"}}),
    ]))
    assert report.outcome == Outcome.TOOL_ERROR and len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_body_schema_needs_review_before_io(tmp_path):
    calls = []
    operation = Operation(method="POST", path="/items", operation_id="items", request_body={
        "content": {"application/json": {"schema": {"type": "object", "oneOf": []}}},
    })
    executor = Executor(TargetConfig(allowed_operations=["items"], environment="isolated",
                                     allow_mutations=True),
                        ContractSummary(version="3.1.0", operations=[operation]),
                        ReportStore(tmp_path), httpx.MockTransport(lambda r: calls.append(r)),
                        registry=registry())
    report = await executor.run(Scenario(name="unsupported", steps=[Step(
        id="write", operation_id="items", policy_id="items_v1", body={"x": {"value": "a"}})]))
    assert report.outcome == Outcome.NEEDS_REVIEW and not calls
