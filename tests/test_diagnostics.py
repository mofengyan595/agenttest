import json

import httpx
import pytest
from pydantic import ValidationError
from test_llm_planner import completion, context, draft, planner
from test_planner_input import inputs, scenario

from agenttest.diagnostics import Layer, ValidationFailure, schema_diagnostic
from agenttest.dsl import Scenario, validate_plan
from agenttest.plan_json import bounded_json
from agenttest.policies import NeedsReview
from agenttest.request_validation import validate_value


@pytest.mark.parametrize("raw,code", [
    ("{bad", "JSON_SYNTAX"), ('{"x":1,"x":2}', "JSON_DUPLICATE_KEY"),
    ("NaN", "JSON_NONFINITE_NUMBER"), ("1e309", "JSON_STRUCTURE_LIMIT"),
])
def test_fixed_json_codes(raw, code):
    with pytest.raises(ValidationFailure) as caught:
        bounded_json(raw, 1000)
    assert caught.value.diagnostic.code == code


def test_pydantic_diagnostic_masks_keys_and_never_includes_input_or_context():
    value = {"name": "private-password", "steps": [], "jwt-private-key": "private-jwt"}
    with pytest.raises(ValidationError) as caught:
        Scenario.model_validate(value)
    diagnostic = schema_diagnostic(caught.value, Layer.DSL)
    raw = diagnostic.model_dump_json()
    assert "private" not in raw and "*" in diagnostic.fields
    assert set(diagnostic.model_dump()) == {"layer", "code", "fields"}


@pytest.mark.parametrize("mutation,code", [
    ("operation", "DSL_UNKNOWN_OPERATION"), ("duplicate", "DSL_DUPLICATE_STEP_ID"),
    ("dependency", "DEPENDENCY_FORWARD_OR_UNKNOWN"),
    ("auth", "DEPENDENCY_AUTH_RESPONSE"), ("order", "DSL_AUTHENTICATION_ORDER"),
    ("body", "OPENAPI_UNKNOWN_BODY_FIELD"),
])
def test_preflight_layers_are_distinct(mutation, code):
    contract, config, _ = inputs()
    plan = scenario()
    if mutation == "operation":
        plan.steps[-1].operation_id = "unknown"
    elif mutation == "duplicate":
        plan.steps[-1].id = plan.steps[0].id
    elif mutation == "dependency":
        plan.steps[-1].path_parameters["courseId"].from_step = "future"
    elif mutation == "auth":
        plan.steps[-1].path_parameters["courseId"].from_step = "auth"
    elif mutation == "order":
        plan.steps = plan.steps[1:]
    else:
        plan.steps[1].body["private-key"] = plan.steps[1].body["name"]
    with pytest.raises(ValidationFailure) as caught:
        validate_plan(plan, contract, config, {"DEMO_ALICE_USERNAME": "alice",
                                              "DEMO_ALICE_PASSWORD": "synthetic"})
    assert caught.value.diagnostic.code == code


@pytest.mark.parametrize("value,schema,code", [
    (1, {"type": "string"}, "OPENAPI_VALUE_TYPE"),
    ("x", {"type": "string", "enum": ["y"]}, "OPENAPI_VALUE_ENUM"),
    ("x", {"type": "string", "minLength": 2}, "OPENAPI_VALUE_LENGTH"),
    (1, {"type": "integer", "minimum": 2}, "OPENAPI_VALUE_BOUND"),
])
def test_schema_constraint_codes(value, schema, code):
    with pytest.raises(ValidationFailure) as caught:
        validate_value(value, schema)
    assert caught.value.diagnostic.code == code


def test_policy_and_dependency_review_codes_preserved():
    _, config, registry = inputs()
    plan = scenario()
    plan.steps[-1].policy_id = "forged"
    with pytest.raises(NeedsReview) as caught:
        registry.resolve(plan.steps[-1], config.business_scenario)
    assert caught.value.diagnostic.code == "POLICY_NOT_APPROVED"
    plan = scenario()
    plan.steps[-1].path_parameters["courseId"].select = "$.wrong"
    with pytest.raises(NeedsReview) as caught:
        registry.resolve(plan.steps[-1], config.business_scenario,
                         {s.id: s for s in plan.steps[:-1]})
    assert caught.value.diagnostic.layer == Layer.DEPENDENCY


@pytest.mark.asyncio
@pytest.mark.parametrize("content,code", [
    ("{bad", "JSON_SYNTAX"),
    (json.dumps({**draft(), "jwt-private-field": "private-token"}), "MODEL_SCHEMA_EXTRA_FIELD"),
])
async def test_model_output_has_safe_diagnostic(content, code):
    from agenttest.llm_planner import PlannerFailure

    llm = planner(lambda req: httpx.Response(200, json=completion(content)))
    with pytest.raises(PlannerFailure) as caught:
        await llm.generate(context())
    assert caught.value.diagnostic.code == code
    assert "private" not in llm.stats.model_dump_json()
