"""Making registration schemas readable must not turn credentials into public input literals."""

import json

import pytest

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.project_intake import IntakeInput
from agenttest.project_inventory import inventory
from agenttest.report import ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore

DOCUMENT = json.dumps({"openapi": "3.1.0", "info": {"title": "Members", "version": "1"},
    "paths": {"/signup": {"post": {"operationId": "registerMember", "requestBody": {
        "content": {"application/json": {"schema": {"type": "object",
            "properties": {"username": {"type": "string", "pattern": "[A-Za-z0-9_]+"},
                "password": {"type": "string", "format": "password", "minLength": 8}},
            "required": ["username", "password"]}}}},
            "responses": {"201": {"description": "ok"}}}}}})


def setup():
    return GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18999",
        isolated_confirmed=True, allow_mutations=True, assisted=True, operations=[dict(
            operation_id="registerMember", alias="注册成员", statuses=[201], arguments=[
                dict(location="body", name="username", value="member"),
                dict(location="body", name="password", value=None, needs_input=True)])]))


def test_sensitive_nonlogin_is_blocked_even_if_browser_claims_inputs_are_ready():
    contract = load_openapi(DOCUMENT)
    assert analyze_contract(contract)[0]["supported"]  # Parsing is distinct from safe execution.
    config = setup()
    config.operations[0].arguments[1].needs_input = False
    item = inventory(contract, config, {})["operations"][0]
    assert item["state"] == "NEEDS_INPUT"
    assert "凭据" in item["reason"]
    config.assisted = False
    with pytest.raises(ValueError, match="SENSITIVE_INPUT_BINDING_REQUIRED"):
        build_generic_project("web_signup", DOCUMENT, contract, config)


@pytest.mark.asyncio
async def test_plain_registration_password_is_rejected_before_even_draft_persistence(tmp_path):
    service = TaskService({}, TaskStore(tmp_path / "tasks.sqlite3"), ReportStore(tmp_path),
                          lambda _: None, generic_intake=True, environ={})
    config = setup()
    config.operations[0].arguments[1].value = "private-registration-test-only"
    try:
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            service.intake.save(IntakeInput(name="注册", document=DOCUMENT, generic=config),
                                "operator")
        assert service.intake.records() == []
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("array", [False, True])
async def test_nested_sensitive_schema_cannot_be_generated_collected_or_saved(tmp_path, array):
    document = json.loads(DOCUMENT)
    schema = document["paths"]["/signup"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]
    child = {"type": "object", "properties": {"secret": {
        "type": "string", "format": "password"}}, "required": ["secret"]}
    schema["properties"]["details"] = {"type": "array", "items": child} if array else child
    del schema["properties"]["password"]
    schema["required"] = ["username", "details"]
    text = json.dumps(document)
    contract = load_openapi(text)
    suggestion = analyze_contract(contract)[0]
    field = next(a for a in suggestion["arguments"] if a["name"] == "details")
    assert field["value"] is None and field["needs_input"]
    config = setup()
    argument = config.operations[0].arguments[1]
    argument.name = "details"
    argument.needs_input = False
    assert inventory(contract, config, {})["operations"][0]["state"] == "UNSUPPORTED"
    config.assisted = False
    with pytest.raises(ValueError, match="SENSITIVE_INPUT_BINDING_REQUIRED"):
        build_generic_project("web_nested_signup", text, contract, config)
    value = {"secret": "private-nested-test-only"}
    argument.value = [value] if array else value
    service = TaskService({}, TaskStore(tmp_path / "tasks.sqlite3"), ReportStore(tmp_path),
                          lambda _: None, generic_intake=True, environ={})
    try:
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            service.intake.save(IntakeInput(name="嵌套注册", document=text, generic=config),
                                "operator")
        assert service.intake.records() == []
    finally:
        await service.close()
