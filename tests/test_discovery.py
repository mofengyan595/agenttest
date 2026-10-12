"""Independent synthetic discovery regressions; no frozen evaluation samples changed."""

import copy
import json
from pathlib import Path

import httpx
import pytest

from agenttest.__main__ import main
from agenttest.contracts import ContractSummary, Operation, load_openapi
from agenttest.discovery import discover, discovery_json
from agenttest.discovery_schema import fields, versioned_schema, witness
from agenttest.dsl import Scenario, validate_plan
from agenttest.executor import Executor
from agenttest.policies import NeedsReview, PolicyRegistry, RegisteredPolicy
from agenttest.request_validation import validate_request, validate_value
from agenttest.safety import TargetConfig

FIXTURES = Path("tests/fixtures/discovery")


def policy(op="items", id="items_v1", roles=None, **kwargs):
    return RegisteredPolicy(
        id=id,
        operation_id=op,
        roles=roles or [None],
        business_scenario="default",
        assertions={"reviewed": True, "source": "synthetic operator", "status": {"codes": [200]}},
        **kwargs,
    )


def single(schema, *, location="query", required=True, version="3.1.0"):
    op = Operation(
        operation_id="items",
        path="/items",
        method="GET",
        parameters=[{"in": location, "name": "value", "required": required, "schema": schema}],
    )
    return ContractSummary(version=version, operations=[op])


def configured(contract):
    return TargetConfig(
        allowed_operations=[o.operation_id for o in contract.operations if o.operation_id]
    )


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_real_contract_fixtures_and_coverage_denominators(version):
    contract = load_openapi((FIXTURES / f"openapi-{version}.yaml").read_bytes())
    result = discover(
        contract,
        PolicyRegistry.from_json((FIXTURES / "policies.json").read_bytes()),
        configured(contract),
        excluded_operation_ids=["excludedOp"],
    )
    coverage = result.coverage
    assert coverage.operation.contract_operations == 4
    assert coverage.operation.ratio.model_dump() == {"numerator": 1, "denominator": 2}
    assert coverage.operation.missing_operation_id == ["GET /anonymous"]
    assert coverage.operation.excluded_operations == ["excludedOp"]
    assert coverage.operation.unsupported_operations == ["unsupportedOp"]
    assert coverage.scenario.denominator == len(coverage.scenario_slots) == 9
    assert coverage.scenario.numerator == 8
    normal = next(
        c
        for c in result.candidates
        if c.scenario_kind == "normal" and c.operation_id == "listResources"
    )
    assert normal.executable_status == "STATIC_VALIDATED"
    assert normal.schema_valid and normal.policy_match
    assert next(v for v in normal.input_variations if v.name == "limit").numeric_value == 2
    assert all(c.review_status == "NEEDS_REVIEW" for c in result.candidates)
    assert coverage.fault_mapping_status == "NOT_MAPPED"
    assert coverage.fault_executed == coverage.fault_detected == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "minimum": -2, "maximum": 4},
        {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 1.0},
        {"type": "integer", "enum": [3, 7], "minimum": 4},
        {"type": "string", "minLength": 2, "maxLength": 4},
        {"type": "string", "enum": ["first", "last"]},
        {"type": "boolean", "enum": [False, True]},
    ],
)
def test_normal_witnesses_satisfy_existing_validator(schema):
    value = witness(schema)
    validate_value(value, schema)
    contract = single(schema)
    validate_request(contract.operations[0], {}, {"value": value}, {}, None)
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    normals = [c for c in result.candidates if c.polarity == "normal"]
    assert normals and all(c.schema_valid for c in normals)
    assert all(c.policy_match for c in normals)


@pytest.mark.parametrize(
    "schema,kind,normal,negative",
    [
        ({"type": "integer", "minimum": 2}, "numeric_lower", 2, 1),
        ({"type": "integer", "exclusiveMinimum": 2}, "numeric_lower", 3, 2),
        ({"type": "integer", "exclusiveMaximum": 2}, "numeric_upper", 1, 2),
        ({"type": "integer", "maximum": 2.5}, "numeric_upper", 2, 3),
    ],
)
def test_exact_integer_edges(schema, kind, normal, negative):
    contract = single(schema)
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    entries = [c for c in result.candidates if c.scenario_kind == kind]
    assert (
        next(c for c in entries if c.polarity == "normal").input_variations[-1].numeric_value
        == normal
    )
    assert (
        next(c for c in entries if c.polarity == "negative").input_variations[0].numeric_value
        == negative
    )
    with pytest.raises(ValueError):
        validate_value(negative, schema)


def test_required_type_enum_and_string_length_descriptions():
    contract = single({"type": "string", "enum": ["aa", "bbbb"], "minLength": 2, "maxLength": 4})
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    kinds = {c.scenario_kind for c in result.candidates}
    assert {
        "required_missing",
        "type_mismatch",
        "enum_boundary",
        "string_min_length",
        "string_max_length",
    } <= kinds
    negatives = [c for c in result.candidates if c.polarity == "negative"]
    assert negatives and all(c.executable_status == "STATIC_ONLY_NEGATIVE" for c in negatives)
    assert all(c.policy_refs == [] and c.policy_match is False for c in negatives)
    assert all("TRUSTED_NEGATIVE_EXPECTATION_REQUIRED" in c.missing_evidence for c in negatives)
    assert any(v.string_length == 5 for c in negatives for v in c.input_variations)
    assert not any(
        c.scenario_kind == "enum_boundary" and c.polarity == "negative"
        for c in discover(
            single({"type": "boolean", "enum": [False, True]}), PolicyRegistry()
        ).candidates
    )


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "pattern": "^[a-z]+$"},
        {"type": "string", "format": "email"},
        {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        {"anyOf": [{"type": "boolean"}]},
        {"type": ["string", "null"]},
        {"type": "array", "items": {"type": "integer"}},
    ],
)
def test_unsupported_schema_is_review_only_and_stays_in_denominator(schema):
    result = discover(single(schema), PolicyRegistry())
    assert len(result.candidates) == 1
    assert result.candidates[0].polarity == "review"
    assert result.coverage.operation.ratio.model_dump() == {"numerator": 0, "denominator": 1}
    assert result.coverage.operation.unsupported_operations == ["items"]


@pytest.mark.parametrize(
    "version,schema",
    [
        ("3.0.3", {"type": "integer", "exclusiveMinimum": 1}),
        ("3.1.0", {"type": "integer", "minimum": 1, "exclusiveMinimum": True}),
        ("3.1.0", {"type": "string", "nullable": True}),
    ],
)
def test_dialect_is_not_guessed(version, schema):
    with pytest.raises(NeedsReview, match="UNSUPPORTED_SCHEMA_DIALECT"):
        versioned_schema(schema, version)


def dependency_inputs(relation="same", selector="$.id", source="create_v1"):
    contract = load_openapi(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "synthetic", "version": "1"},
                "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
                "paths": {
                    "/login": {
                        "post": {
                            "operationId": "login",
                            "responses": {"200": {"description": "OK"}},
                        }
                    },
                    "/resources": {
                        "post": {
                            "operationId": "create",
                            "security": [{"bearer": []}],
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string", "minLength": 1}
                                            },
                                            "required": ["name"],
                                            "additionalProperties": False,
                                        }
                                    }
                                },
                            },
                            "responses": {"201": {"description": "Created"}},
                        }
                    },
                    "/resources/{id}": {
                        "get": {
                            "operationId": "read",
                            "security": [{"bearer": []}],
                            "parameters": [
                                {
                                    "in": "path",
                                    "name": "id",
                                    "required": True,
                                    "schema": {"type": "integer"},
                                }
                            ],
                            "responses": {"200": {"description": "OK"}},
                        }
                    },
                },
            }
        )
    )
    registry = PolicyRegistry(
        [
            policy("login", "login_v1", ["alice", "bob"], closed_arguments=True),
            policy(
                "create",
                "create_v1",
                ["alice"],
                closed_arguments=True,
                binding_rules=[
                    {
                        "location": "body",
                        "name": "name",
                        "literal_schema": {"type": "string", "minLength": 1},
                    }
                ],
            ),
            policy(
                "read",
                "read_v1",
                ["alice" if relation == "same" else "bob"],
                closed_arguments=True,
                binding_rules=[
                    {
                        "location": "path_parameters",
                        "name": "id",
                        "source_policy_id": source,
                        "source_selector": selector,
                        "role_relation": relation,
                    }
                ],
            ),
        ]
    )
    config = TargetConfig(
        environment="isolated",
        allow_mutations=True,
        allowed_operations=["login", "create", "read"],
        roles={
            r: {
                "login_operation": "login",
                "username_env": "SYNTHETIC_USER",
                "password_env": "SYNTHETIC_PASSWORD",
            }
            for r in ("alice", "bob")
        },
    )
    return contract, registry, config


@pytest.mark.parametrize("relation", ["same", "different"])
def test_dependency_and_authentication_order_come_from_trusted_metadata(relation):
    result = discover(*dependency_inputs(relation))
    flow = next(c for c in result.candidates if c.scenario_kind == "resource_flow")
    assert flow.operation_ids == ["login", "create", "read"]
    assert flow.policy_refs == ["create_v1", "login_v1", "read_v1"]
    assert flow.dependencies[0].role_relation == relation
    assert flow.authentication_order[0] == "login:alice -> create:alice"
    reader = "alice" if relation == "same" else "bob"
    assert flow.authentication_order[-1] == f"login:{reader} -> read:{reader}"
    assert flow.policy_match is True and flow.schema_valid is None
    assert flow.executable_status == "BLOCKED"
    assert "RUNTIME_CREDENTIALS_REQUIRED" in flow.missing_evidence
    assert "RUNTIME_RESOURCE_VALUE_VALIDATION" in flow.missing_evidence


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"selector": "$"}, "MISSING_RESOURCE_SELECTOR"),
        ({"source": "unknown_v1"}, "UNKNOWN_SOURCE_POLICY"),
    ],
)
def test_missing_dependency_evidence_stays_reviewable(kwargs, code):
    result = discover(*dependency_inputs(**kwargs))
    flow = next(c for c in result.candidates if c.scenario_kind == "resource_flow")
    assert flow.rejection_reason == code and flow.executable_status == "BLOCKED"


def test_cross_role_missing_account_and_unknown_role():
    contract, registry, config = dependency_inputs("different")
    del config.roles["alice"]
    result = discover(contract, registry, config)
    flow = next(c for c in result.candidates if c.scenario_kind == "resource_flow")
    assert flow.rejection_reason == "DEPENDENCY_ROLE_EVIDENCE_MISSING"


def test_no_inferred_resource_relation_or_policy_authorization():
    contract = single({"type": "integer"}, location="path")
    contract.operations[0].path = "/items/{value}"
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    normal = next(c for c in result.candidates if c.scenario_kind == "normal")
    assert normal.rejection_reason == "RESOURCE_BINDING_EVIDENCE_MISSING"
    assert not any(c.scenario_kind == "resource_flow" for c in result.candidates)
    unknown = discover(single({"type": "integer"}), PolicyRegistry())
    assert (
        "TRUSTED_EXPECTATION_REQUIRED"
        in next(c for c in unknown.candidates if c.scenario_kind == "normal").missing_evidence
    )


def test_policy_schema_intersection_and_rejected_boundaries():
    contract = single({"type": "integer", "minimum": 1, "maximum": 9})
    p = policy(
        binding_rules=[
            {
                "location": "query",
                "name": "value",
                "literal_schema": {"type": "integer", "minimum": 4, "maximum": 6},
            }
        ]
    )
    result = discover(contract, PolicyRegistry([p]), configured(contract))
    normal = next(c for c in result.candidates if c.scenario_kind == "normal")
    assert normal.input_variations[0].numeric_value == 4
    assert any(
        c.scenario_kind == "numeric_lower" and c.polarity == "review" for c in result.candidates
    )


def test_stability_deduplication_no_mutation_and_definition_copies():
    contract = single({"type": "integer", "minimum": 1, "maximum": 3})
    p = policy(roles=[None, None])
    registry = PolicyRegistry([p])
    original = copy.deepcopy(contract.model_dump())
    first = discover(
        contract, registry, configured(contract), excluded_operation_ids=["unknown", "unknown"]
    )
    registry.definitions()[0].roles.clear()
    second = discover(contract, registry, configured(contract), excluded_operation_ids=["unknown"])
    assert discovery_json(first) == discovery_json(second)
    assert len({c.candidate_id for c in first.candidates}) == len(first.candidates)
    assert contract.model_dump() == original


def test_no_secrets_http_execution_or_model_calls(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Discovery must not call runtime capabilities")

    monkeypatch.setattr(Executor, "run", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)
    monkeypatch.setenv("SYNTHETIC_PASSWORD", "private-environment-password")
    contract = single(
        {
            "type": "string",
            "enum": ["raw-enum-password"],
            "example": "example-secret",
            "default": "default-secret",
        }
    )
    contract.operations[0].summary = "sk-rawsecretvalue12345"
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    encoded = discovery_json(result)
    for private in (
        "raw-enum-password",
        "example-secret",
        "default-secret",
        "private-environment-password",
        "sk-rawsecretvalue12345",
    ):
        assert private not in encoded
    assert result.http_requests == result.model_calls == result.tests_executed == 0
    with pytest.raises(ValueError):
        Scenario.model_validate(result.candidates[0].model_dump())


def test_remote_reference_is_rejected_without_fetch():
    raw = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "safe", "version": "1"},
            "paths": {},
            "components": {"schemas": {"bad": {"$ref": "https://remote.invalid/x"}}},
        }
    )
    with pytest.raises(ValueError, match="local"):
        load_openapi(raw)


def test_cli_output_is_reproducible_exclusive_and_not_a_runreport(tmp_path, capsys):
    args = [
        "discover",
        "--contract",
        str(FIXTURES / "openapi-3.1.yaml"),
        "--policies",
        str(FIXTURES / "policies.json"),
        "--exclude-operation",
        "excludedOp",
    ]
    one, two = tmp_path / "one.discovery.json", tmp_path / "two.discovery.json"
    assert main([*args, "--output", str(one)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["http_requests"] == summary["tests_executed"] == 0
    assert main([*args, "--output", str(two)]) == 0
    capsys.readouterr()
    assert one.read_bytes() == two.read_bytes()
    assert main([*args, "--output", str(one)]) == 2
    capsys.readouterr()
    assert one.read_bytes() == two.read_bytes()
    assert main([*args, "--output", str(tmp_path / "report.json")]) == 2
    assert not (tmp_path / "report.json").exists()


def test_negative_inputs_still_fail_original_plan_validation():
    contract = single({"type": "integer", "minimum": 1})
    scenario = Scenario.model_validate(
        {
            "name": "synthetic negative",
            "steps": [
                {
                    "id": "read",
                    "operation_id": "items",
                    "policy_id": "items_v1",
                    "query": {"value": {"value": 0}},
                }
            ],
        }
    )
    with pytest.raises(ValueError):
        validate_plan(scenario, contract, configured(contract), environ={})


def test_output_budget_and_empty_denominators(monkeypatch):
    import agenttest.discovery as module

    empty = discover(ContractSummary(version="3.1.0", operations=[]), PolicyRegistry())
    assert empty.coverage.operation.ratio.denominator == empty.coverage.scenario.denominator == 0
    monkeypatch.setattr(module, "MAX_CANDIDATES", 1)
    with pytest.raises(ValueError, match="DISCOVERY_CANDIDATE_BUDGET"):
        discover(single({"type": "integer"}), PolicyRegistry())


def test_unsupported_body_and_bounded_witness():
    op = Operation(
        method="POST",
        path="/items",
        operation_id="items",
        request_body={"content": {"multipart/form-data": {"schema": {"type": "object"}}}},
    )
    with pytest.raises(NeedsReview, match="UNSUPPORTED_DISCOVERY_BODY_MEDIA"):
        fields(op, "3.1.0", "#/operations/0")
    with pytest.raises(NeedsReview, match="DISCOVERY_NO_BOUNDED_WITNESS"):
        witness({"type": "string", "minLength": 1000000})


def test_required_json_body_and_fields_are_distinct_negative_candidates():
    contract, registry, config = dependency_inputs()
    result = discover(contract, registry, config)
    candidates = [
        c
        for c in result.candidates
        if c.operation_id == "create" and c.scenario_kind == "required_missing"
    ]
    actions = {v.action for c in candidates for v in c.input_variations}
    assert actions == {"omit_required_body", "omit_required"}
    assert all(c.executable_status == "STATIC_ONLY_NEGATIVE" for c in candidates)
    assert any(c.schema_basis[0].endswith("/schema/required") for c in candidates)


def test_unreviewed_empty_and_unknown_policies_never_match():
    contract = single({"type": "integer"})
    for approved in (False, True):
        p = policy()
        p.assertions.reviewed = approved
        if approved:
            p.assertions.source = ""
        result = discover(
            contract, PolicyRegistry([p, policy("absent", "unknown_v1")]), configured(contract)
        )
        normal = next(c for c in result.candidates if c.scenario_kind == "normal")
        assert normal.executable_status == "BLOCKED" and normal.policy_match is not True
        assert "POLICY_OPERATION_ABSENT:unknown_v1" in result.issues
        assert not next(
            r for r in result.coverage.policy if r.policy_id == "unknown_v1"
        ).in_contract


def test_optional_parameters_do_not_conflict_with_closed_approved_arguments():
    contract = single({"type": "integer"}, required=False)
    result = discover(
        contract, PolicyRegistry([policy(closed_arguments=True)]), configured(contract)
    )
    normal = next(c for c in result.candidates if c.scenario_kind == "normal")
    assert normal.executable_status == "STATIC_VALIDATED"
    assert normal.input_variations == []
    assert any(c.scenario_kind == "type_mismatch" for c in result.candidates)


def test_dependency_cycle_and_step_budget_are_rejected():
    contract, registry, config = dependency_inputs()
    entries = registry.definitions()
    create = next(p for p in entries if p.id == "create_v1")
    create.binding_rules[0].literal_schema = None
    create.binding_rules[0].source_policy_id = "read_v1"
    create.binding_rules[0].source_selector = "$.name"
    create.binding_rules[0].role_relation = "same"
    result = discover(contract, PolicyRegistry(entries), config)
    assert any(c.rejection_reason == "DEPENDENCY_CYCLE_OR_BUDGET" for c in result.candidates)
    config.max_steps = 1
    result = discover(contract, registry, config)
    assert (
        next(c for c in result.candidates if c.scenario_kind == "resource_flow").rejection_reason
        == "DSL_STEP_LIMIT"
    )


def test_unsupported_authentication_does_not_become_policy_ready():
    contract = single({"type": "integer"})
    contract.security_schemes = {"key": {"type": "apiKey", "in": "header", "name": "X-Key"}}
    contract.operations[0].security = [{"key": []}]
    config = TargetConfig(allowed_operations=["items"], roles={"reader": {}})
    result = discover(contract, PolicyRegistry([policy(roles=["reader"])]), config)
    assert all(c.executable_status != "STATIC_VALIDATED" for c in result.candidates)
    assert any(
        c.rejection_reason == "DSL_STAGE_1_SUPPORTS_HTTP_BEARER_AUTHENTICATION_ONLY"
        for c in result.candidates
    )


def test_sensitive_metadata_rejection_and_cli_errors_do_not_echo_inputs(tmp_path, capsys):
    contract = single({"type": "integer"})
    contract.operations[0].operation_id = "password=raw-private-value"
    with pytest.raises(ValueError, match="DISCOVERY_UNSAFE_METADATA"):
        discover(contract, PolicyRegistry())
    raw = tmp_path / "private.json"
    raw.write_text("password=private-json-error", encoding="utf-8")
    assert (
        main(
            [
                "discover",
                "--contract",
                str(raw),
                "--policies",
                str(raw),
                "--output",
                str(tmp_path / "safe.discovery.json"),
            ]
        )
        == 2
    )
    assert "private-json-error" not in capsys.readouterr().out


def test_fractional_edges_and_zero_length_are_not_invented():
    from agenttest.discovery_schema import edge

    assert edge({"type": "integer", "exclusiveMinimum": 1.1}, True) == 2
    assert edge({"type": "integer", "minimum": 2, "exclusiveMinimum": 1}, True) == 2
    result = discover(single({"type": "string", "minLength": 0}), PolicyRegistry())
    assert not any(
        c.scenario_kind == "string_min_length" and c.polarity == "negative"
        for c in result.candidates
    )


def test_login_schema_values_remain_unknown_and_input_identity_binds_ids():
    result = discover(*dependency_inputs())
    assert all(c.schema_valid is None for c in result.candidates if c.operation_id == "login")
    contract = single({"type": "string", "enum": ["private-one"]})
    first = discover(contract, PolicyRegistry())
    contract.operations[0].parameters[0]["schema"]["enum"] = ["private-two"]
    second = discover(contract, PolicyRegistry())
    assert {c.candidate_id for c in first.candidates}.isdisjoint(
        {c.candidate_id for c in second.candidates}
    )


@pytest.mark.parametrize("version,schema,valid,invalid", [
    ("3.0.3", {"type": "integer", "minimum": 1, "exclusiveMinimum": True}, 2, 1),
    ("3.0.3", {"type": "integer", "maximum": 2, "exclusiveMaximum": True}, 1, 2),
    ("3.1.0", {"type": "integer", "exclusiveMinimum": 1}, 2, 1),
    ("3.1.0", {"type": "integer", "exclusiveMaximum": 2}, 1, 2),
])
def test_exclusive_dialects_really_validate_edges(version, schema, valid, invalid):
    result = discover(single(schema, version=version), PolicyRegistry([policy()]),
                      configured(single(schema, version=version)))
    edge_kind = "numeric_lower" if "exclusiveMinimum" in schema else "numeric_upper"
    candidate = next(c for c in result.candidates
                     if c.scenario_kind == edge_kind and c.polarity == "normal")
    assert candidate.input_variations[0].numeric_value == valid
    assert candidate.executable_status == "STATIC_VALIDATED"
    validate_value(valid, schema)
    with pytest.raises(ValueError):
        validate_value(invalid, schema)


@pytest.mark.parametrize("schema", [
    {"type": "integer", "enum": [1, 2], "minimum": 4},
    {"type": "integer", "minimum": 4, "maximum": 2},
    {"type": "number", "exclusiveMinimum": 2, "exclusiveMaximum": 2},
    {"type": "string", "minLength": 4, "maxLength": 2},
    {"type": "string", "minLength": 1000},
    {"type": "integer", "minimum": "invalid"},
    {"type": "integer", "minimum": float("inf")},
    {"type": "integer", "minimum": 10 ** 1000},
    {"type": "string", "minLength": -1},
    {"type": "string", "maxLength": 1.5},
    {"type": "integer", "enum": []},
])
def test_no_valid_witness_or_invalid_schema_never_becomes_static_validated(schema):
    contract = single(schema)
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    assert not any(c.executable_status == "STATIC_VALIDATED" for c in result.candidates)
    assert all(c.polarity == "review" for c in result.candidates if c.scenario_kind == "normal")


def optional_body_contract():
    return ContractSummary(version="3.1.0", operations=[Operation(
        operation_id="items", path="/items", method="GET", request_body={
            "required": False, "content": {"application/json": {"schema": {
                "type": "object", "required": ["value"], "additionalProperties": False,
                "properties": {"value": {"type": "integer", "minimum": 1},
                               "extra": {"type": "integer"}}}}}})])


def test_optional_body_can_be_omitted_under_closed_no_body_policy():
    contract = optional_body_contract()
    result = discover(contract, PolicyRegistry([policy(closed_arguments=True)]),
                      configured(contract))
    normal = next(c for c in result.candidates if c.scenario_kind == "normal")
    assert normal.executable_status == "STATIC_VALIDATED" and normal.input_variations == []
    assert normal.schema_valid and normal.policy_match


def test_optional_body_when_selected_must_include_required_fields():
    contract = optional_body_contract()
    p = policy(closed_arguments=True, binding_rules=[
        {"location": "body", "name": "value", "literal_schema": {"type": "integer"}}])
    result = discover(contract, PolicyRegistry([p]), configured(contract))
    normal = next(c for c in result.candidates if c.scenario_kind == "normal")
    assert normal.executable_status == "STATIC_VALIDATED"
    assert [v.name for v in normal.input_variations] == ["value"]


def test_source_policy_needs_independent_approval():
    contract, registry, config = dependency_inputs()
    entries = registry.definitions()
    next(p for p in entries if p.id == "create_v1").assertions.reviewed = False
    result = discover(contract, PolicyRegistry(entries), config)
    flow = next(c for c in result.candidates if c.scenario_kind == "resource_flow")
    assert flow.executable_status == "BLOCKED" and flow.policy_match is not True
    assert flow.rejection_reason == "POLICY_SCOPE_MISMATCH"


def test_multiple_source_policies_follow_exact_approved_reference():
    contract, registry, config = dependency_inputs()
    extra = policy("create", "create_alternative_v1", ["alice"], closed_arguments=True)
    # The alternative is unusable; it must not replace the explicitly approved create_v1.
    result = discover(contract, PolicyRegistry([extra, *registry.definitions()]), config)
    flow = next(c for c in result.candidates if c.scenario_kind == "resource_flow")
    assert flow.dependencies[0].source_policy_id == "create_v1"
    assert "create_alternative_v1" not in flow.policy_refs
    assert flow.policy_match is True and flow.executable_status == "BLOCKED"


def test_every_schema_pointer_resolves_in_fingerprinted_summary():
    contract = single({"type": "integer", "minimum": 2, "exclusiveMinimum": 1, "maximum": 5})
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    from agenttest.report import fingerprint

    summary = contract.model_dump()
    assert fingerprint(summary) == result.contract_sha256
    for candidate in result.candidates:
        for pointer in candidate.schema_basis:
            assert pointer.startswith("#/operations/")
            value = summary
            for token in pointer[2:].split("/"):
                key = token.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
    lower = next(c for c in result.candidates
                 if c.scenario_kind == "numeric_lower" and c.polarity == "normal")
    assert {p.rsplit("/", 1)[1] for p in lower.schema_basis} == {"minimum", "exclusiveMinimum"}


def test_coverage_subsets_keep_static_negative_discovery_and_zero_execution():
    contract = single({"type": "integer", "minimum": 5, "maximum": 1})
    result = discover(contract, PolicyRegistry([policy()]), configured(contract))
    coverage = result.coverage.operation
    assert coverage.ratio.numerator == coverage.trusted_policy.numerator == 1
    assert coverage.with_normal_candidates.numerator == coverage.static_preflight.numerator == 0
    assert coverage.execution.model_dump() == {"numerator": 0, "denominator": 1}
    assert all(c.review_status == "NEEDS_REVIEW" for c in result.candidates)
    from agenttest.discovery_models import DiscoveryResult

    legacy = result.model_dump()
    for field in ("with_normal_candidates", "static_preflight", "trusted_policy", "execution"):
        legacy["coverage"]["operation"].pop(field)
    old = DiscoveryResult.model_validate(legacy)
    assert old.coverage.operation.static_preflight is None


def test_discovery_does_not_read_environment_or_construct_vault(monkeypatch):
    import os

    from agenttest.credentials import CredentialVault

    def forbidden(*args, **kwargs):
        raise AssertionError("No runtime environment or vault in discovery")

    inputs = dependency_inputs("different")
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(CredentialVault, "__init__", forbidden)
    monkeypatch.setattr(type(os.environ), "__getitem__", forbidden)
    result = discover(*inputs)
    assert result.http_requests == result.model_calls == result.tests_executed == 0
    assert not any(c.executable_status == "STATIC_VALIDATED" for c in result.candidates)


def test_policy_numeric_enum_literal_is_not_exported_as_synthetic_value():
    contract = single({"type": "integer"})
    p = policy(binding_rules=[{"location": "query", "name": "value", "literal_schema": {
        "type": "integer", "enum": [246810]}}])
    result = discover(contract, PolicyRegistry([p]), configured(contract))
    normal = next(c for c in result.candidates if c.scenario_kind == "normal")
    assert normal.executable_status == "STATIC_VALIDATED"
    assert normal.input_variations[0].numeric_value is None
    assert normal.input_variations[0].action == "approved_enum_shape"
    assert "246810" not in discovery_json(result)
