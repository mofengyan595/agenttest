"""Contract-known literal types prevent field wrappers without bypassing runtime checks."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_generic_arguments import context, draft, planner


def checker(public):
    wire = json.loads(planner().prepare_request(public))
    schema = wire["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("kind,valid,invalid", [
    ("string", "literal", {"object": [{"key": "subject", "value": "literal"}]}),
    ("integer", 3, 3.5), ("number", 1.25, "1.25"), ("boolean", False, 0),
    ("object", {"object": []}, "object"), ("array", {"array": []}, {"object": []}),
])
def test_wire_restricts_literal_kind_by_declared_field(kind, valid, invalid):
    public = context()
    public["operations"][0]["request_body"]["content"]["application/json"]["properties"][
        "subject"] = {"type": kind}
    original = copy.deepcopy(public)
    schema = checker(public)
    assert schema.is_valid(draft(value=valid))
    assert not schema.is_valid(draft(value=invalid))
    reference = draft(value=None)
    reference["steps"][0]["arguments"][0].update(from_step="earlier", select="$.key")
    assert schema.is_valid(reference)
    assert public == original


def test_identical_slot_names_with_different_operation_types_are_not_merged():
    public = context()
    second = copy.deepcopy(public["operations"][0])
    second["id"] = "numeric_create"
    second["request_body"]["content"]["application/json"]["properties"]["subject"] = {
        "type": "integer"}
    public["operations"].append(second)
    schema = checker(public)
    assert schema.is_valid(draft(value="text"))
    assert not schema.is_valid(draft(value=2))
    assert schema.is_valid(draft(operation="numeric_create", value=2))
    assert not schema.is_valid(draft(operation="numeric_create", value="text"))
