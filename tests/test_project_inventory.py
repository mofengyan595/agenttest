"""The entire uploaded denominator survives partial readiness and large projects."""

import json

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.project_inventory import inventory


def contract_document():
    paths = {f"/lookup/{i}": {"get": {"operationId": f"lookup{i}",
        "responses": {"200": {"description": "ok"}}}} for i in range(42)}
    nested = dict(type="object", required=["labels"], properties=dict(labels=dict(
        type="array", minItems=2, items=dict(type="string", enum=["new", "old"]))))
    body = dict(type="object", required=["details"], properties=dict(details=nested))
    paths["/records"] = {"post": {"operationId": "createRecord", "requestBody": {
        "required": True, "content": {"application/json": {"schema": body}}},
        "responses": {"201": {"description": "created", "links": {"read": {
            "operationId": "readRecord", "parameters": {"id": "$response.body#/id"}}}}}}}
    paths["/records/{id}"] = {"get": {"operationId": "readRecord", "parameters": [
        {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}}],
        "responses": {"200": {"description": "ok"}}}}
    paths["/private"] = {"get": {"operationId": "readPrivate", "security": [{"bearer": []}],
        "responses": {"200": {"description": "ok"}}}}
    paths["/missing/{item}"] = {"get": {"operationId": "missing", "parameters": [
        {"in": "path", "name": "item", "required": True, "schema": {"type": "string"}}],
        "responses": {"200": {"description": "ok"}}}}
    paths["/upload"] = {"post": {"operationId": "upload", "requestBody": {"content": {
        "multipart/form-data": {"schema": {"type": "object", "properties": {
            "file": {"type": "string", "format": "binary"}}}}}},
        "responses": {"200": {"description": "ok"}}}}
    return json.dumps(dict(openapi="3.0.3", info=dict(title="New structure", version="1"),
        paths=paths, components=dict(securitySchemes=dict(
            bearer=dict(type="http", scheme="bearer")))))


def proposed_setup(contract):
    suggestions = analyze_contract(contract)
    return GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998", assisted=True,
        isolated_confirmed=True, operations=[dict(operation_id=s["operation_id"], alias=s["alias"],
            statuses=s["statuses"], checks=s["checks"], arguments=[{k: v for k, v in a.items()
                if k not in {"schema", "basis", "dependency_basis", "required", "value_type"}}
                for a in s["arguments"]]) for s in suggestions if s["supported"]]))


def test_input_count_above_plan_capacity_is_explicitly_unsupported_not_falsely_ready():
    body = dict(type="object", properties={f"field_{i}": dict(type="string") for i in range(31)})
    document = dict(openapi="3.0.3", info=dict(title="Input limit", version="1"), paths={
        "/wide": {"post": dict(operationId="wide", requestBody=dict(content={
            "application/json": dict(schema=body)}), responses={"200": dict(description="ok")})}})
    analyzed = analyze_contract(load_openapi(json.dumps(document)))
    assert not analyzed[0]["supported"]
    assert "单个接口一次计划最多支持 30 项输入" in analyzed[0]["reason"]


def test_scalar_response_can_be_selected_as_a_whole_without_writing_a_selector():
    doc = dict(openapi="3.0.3", info=dict(title="Scalar reply", version="1"), paths={
        "/session": {"post": dict(operationId="session", responses={"200": dict(
            description="token", content={"application/json": dict(
                schema=dict(type="string"))})})}})
    result = analyze_contract(load_openapi(json.dumps(doc)))
    assert result[0]["response_fields"] == [dict(
        selector="$", label="整个响应", value_type="string")]


def test_array_containers_can_be_checked_without_asserting_that_the_first_item_exists():
    doc = dict(openapi="3.0.3", info=dict(title="Empty list", version="1"), paths={
        "/list": {"get": dict(operationId="list", responses={"200": dict(description="ok",
            content={"application/json": dict(schema=dict(type="object", properties=dict(
                data=dict(type="array", items=dict(type="string")))))})})}})
    fields = analyze_contract(load_openapi(json.dumps(doc)))[0]["response_fields"]
    assert dict(selector="$.data", label="data", value_type="array") in fields


def test_same_path_creation_scalar_envelope_suggests_id_with_review_basis():
    raw = json.loads(contract_document())
    response = raw["paths"]["/records"]["post"]["responses"]["201"]
    response.pop("links")
    response["content"] = {"application/json": dict(schema=dict(type="object", properties=dict(
        code=dict(type="integer"), data=dict(type="string"))))}
    rows = analyze_contract(load_openapi(json.dumps(raw)))
    detail = next(o for o in rows if o["operation_id"] == "readRecord")
    assert detail["arguments"][0]["source_operation"] == "createRecord"
    assert detail["arguments"][0]["selector"] == "$.data"
    assert "需确认" in detail["arguments"][0]["dependency_basis"]


def test_mutation_never_suggests_an_existing_list_first_item_as_its_resource():
    raw = json.loads(contract_document())
    raw["paths"]["/records"] = {"get": dict(operationId="listRecords", responses={
        "200": dict(description="ok", content={"application/json": dict(schema=dict(
            type="array", items=dict(type="object", properties=dict(id=dict(type="string")))))})})}
    raw["paths"]["/records/{id}"]["patch"] = {
        **raw["paths"]["/records/{id}"]["get"], "operationId": "modifyRecord"}
    analyzed = analyze_contract(load_openapi(json.dumps(raw)))
    write = next(o for o in analyzed if o["operation_id"] == "modifyRecord")
    read = next(o for o in analyzed if o["operation_id"] == "readRecord")
    assert write["arguments"][0]["source_operation"] is None
    assert write["arguments"][0]["needs_input"]
    assert read["arguments"][0]["source_operation"] == "listRecords"


def test_full_inventory_nested_links_and_partial_authorization():
    contract = load_openapi(contract_document())
    setup = proposed_setup(contract)
    listing = inventory(contract, setup, {})
    assert listing["total"] == 47
    assert listing["counts"] == dict(READY=42, NEEDS_INPUT=3, UNAUTHORIZED=2, UNSUPPORTED=0)
    nested = next(s for s in setup.operations if s.operation_id == "createRecord")
    assert nested.arguments[0].value == {"labels": ["new", "new"]}
    read = next(s for s in setup.operations if s.operation_id == "readRecord")
    assert read.arguments[0].source_operation == "createRecord"
    assert read.arguments[0].selector == "$.id"
    project = build_generic_project("large", contract_document(), contract, setup, environ={})
    assert len(project.catalog.goals) == 42
    assert len(project.target.allowed_operations) == 42
    setup.allow_mutations = True
    listing = inventory(contract, setup, {})
    assert listing["counts"]["READY"] == 44
    project = build_generic_project("large", contract_document(), contract, setup, environ={})
    assert len(project.catalog.goals) == 44
    rule = next(p for p in project.registry.definitions() if p.operation_id == "createRecord")
    assert "enum" not in rule.binding_rules[0].literal_schema


def test_missing_input_and_unsupported_reasons_are_actionable():
    contract = load_openapi(contract_document())
    setup = proposed_setup(contract)
    listing = inventory(contract, setup, {})
    for item in listing["operations"]:
        assert item["reason"] and item["next_step"]
    missing = next(e for e in listing["operations"] if e["operation_id"] == "missing")
    assert "item" in missing["reason"]
    upload = next(e for e in listing["operations"] if e["operation_id"] == "upload")
    assert upload["state"] == "UNAUTHORIZED"
    setup.allow_mutations = True
    upload = next(e for e in inventory(contract, setup, {})["operations"]
                  if e["operation_id"] == "upload")
    assert upload["state"] == "NEEDS_INPUT" and "CSV" in upload["reason"]
    setup.allow_mutations = False
    setup.operations[0].enabled = False
    assert inventory(contract, setup)["counts"]["UNAUTHORIZED"] == 3


def test_standard_rest_dependency_is_suggested_without_document_links_or_business_aliases():
    raw = json.loads(contract_document())
    response = raw["paths"]["/records"]["post"]["responses"]["201"]
    response.pop("links")
    response["content"] = {"application/json": dict(schema=dict(type="object",
        properties=dict(data=dict(type="object", properties=dict(id=dict(type="string"))))))}
    contract = load_openapi(json.dumps(raw))
    suggestions = analyze_contract(contract)
    read = next(o for o in suggestions if o["operation_id"] == "readRecord")
    argument = read["arguments"][0]
    assert argument["source_operation"] == "createRecord"
    assert argument["selector"] == "$.data.id"
    assert "需确认" in argument["dependency_basis"]
    assert not read["enabled"]  # the suggestion itself is never execution authority


def test_new_defaults_preserve_legacy_intake_serialization():
    from test_generic_intake import setup

    config = setup()
    saved = config.model_dump()
    assert set(saved) == {"base_url", "isolated_confirmed", "allow_mutations", "max_requests",
                          "roles", "operations"}
    assert "access_checks" not in saved["operations"][0]
    assert "generated" not in saved["operations"][0]["arguments"][0]
    assert GenericSetup.model_validate(saved).model_dump() == saved


def test_product_intake_suggests_valid_common_formats_without_changing_legacy_discovery():
    from agenttest.request_validation import validate_value

    raw = json.loads(contract_document())
    properties = {name: dict(type="string", format=name)
                  for name in ("uuid", "email", "date", "date-time")}
    raw["paths"]["/records"]["post"]["requestBody"]["content"]["application/json"][
        "schema"] = dict(type="object", properties=properties, required=list(properties))
    contract = load_openapi(json.dumps(raw))
    operation = next(o for o in analyze_contract(contract) if o["operation_id"] == "createRecord")
    assert operation["supported"]
    assert len(operation["arguments"]) == 4
    for argument in operation["arguments"]:
        validate_value(argument["value"], properties[argument["name"]])


def test_password_name_without_format_needs_identity_and_never_copies_document_example():
    raw = json.loads(contract_document())
    raw["paths"]["/records"]["post"]["requestBody"]["content"]["application/json"][
        "schema"] = dict(type="object", properties=dict(username=dict(type="string"),
            password=dict(type="string", example="not-a-model-input")),
            required=["username", "password"])
    contract = load_openapi(json.dumps(raw))
    suggestion = next(o for o in analyze_contract(contract) if o["operation_id"] == "createRecord")
    password = next(a for a in suggestion["arguments"] if a["name"] == "password")
    assert password["needs_input"] and password["value"] is None
