"""Upload/body ambiguity is rejected before any business request or plan repair."""

import json

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_composed_constraints import adapter, catalog, step

from agenttest.composed_demo import composed_inputs
from agenttest.composed_evaluation import CompositionSuite, reference_draft
from agenttest.composed_plan import ComposedPlannerDraft, validate_composed_arguments
from agenttest.contracts import summarize_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import Scenario
from agenttest.llm_planner import PlannerFailure
from agenttest.planner_input import planning_context


def reference(**changes):
    return {"location": "path_parameters", "name": "courseId", "value": None,
            "from_step": "producer", "select": "$.courseId", **changes}


@pytest.mark.parametrize("arguments", [
    [], [reference(location="body")], [reference(location="query")],
    [reference(name="file")], [reference(select="$.id")],
    [reference(from_step="")], [reference(value=1, from_step=None, select=None)],
    [reference(), reference()],
    [reference(), {"location": "body", "name": "file", "value": "synthetic-private-text",
                   "from_step": None, "select": None}],
])
@pytest.mark.asyncio
async def test_sent_and_local_upload_constraints_reject_unapproved_arguments(arguments):
    value = {"name": "upload constraints", "steps": [step(arguments=arguments)]}
    payload = json.loads(adapter().prepare_request(catalog()))
    validator = Draft202012Validator(payload["response_format"]["json_schema"]["schema"])
    assert not validator.is_valid(value)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(value)}}], "usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    planner = adapter(httpx.MockTransport(handle))
    with pytest.raises(PlannerFailure) as exc:
        await planner.generate(catalog())
    assert len(calls) == planner.stats.calls == 1
    assert exc.value.reason == "MODEL_INVALID_RESPONSE"
    assert exc.value.diagnostic.code == "MODEL_SCHEMA_COMPOSED_ARGUMENTS"
    assert "synthetic-private-text" not in planner.stats.model_dump_json()
    assert planner.stats.total_tokens == 150


def test_approved_new_plans_pass_wire_and_local_checks_with_bounded_payload():
    context = catalog()
    encoded = adapter().prepare_request(context)
    assert len(encoded) <= adapter()._settings.max_input_tokens
    schema = json.loads(encoded)["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    suite = CompositionSuite.model_validate_json(
        (PROJECT_ROOT / "examples/composed-arguments-development.json").read_bytes())
    for case in suite.cases:
        app, target, registry, _ = composed_inputs()
        full_context = planning_context(summarize_openapi(app.openapi()), target, registry,
                                        case.user_task, Scenario.model_json_schema())
        assert adapter().prepare_request(full_context)
        if case.expected is None:
            continue
        draft = reference_draft(case.expected)
        assert validator.is_valid(draft.model_dump(mode="json"))
        validate_composed_arguments(draft, context)


def test_login_empty_and_create_literal_follow_trusted_catalog_not_task_answers():
    context = catalog()
    schema = json.loads(adapter().prepare_request(context))["response_format"]["json_schema"][
        "schema"]
    validator = Draft202012Validator(schema)
    login = step(operation_id="login", policy_id="composed_login_v1", csv_fixture_id=None)
    assert validator.is_valid({"name": "login", "steps": [login]})
    login["arguments"] = [reference()]
    assert not validator.is_valid({"name": "login", "steps": [login]})
    policy = next(p for p in context["policies"] if p["id"] == "composed_create_v1")
    policy["binding_rules"][0]["literal_schema"]["maxLength"] = 2
    updated = json.loads(adapter().prepare_request(context))["response_format"]["json_schema"][
        "schema"]
    create = step(operation_id="createCourse", policy_id=policy["id"], csv_fixture_id=None,
                  arguments=[reference(location="body", name="name", value="long",
                                       from_step=None, select=None)])
    assert not Draft202012Validator(updated).is_valid({"name": "create", "steps": [create]})
    with pytest.raises(ValueError):
        validate_composed_arguments(ComposedPlannerDraft.model_validate(
            {"name": "create", "steps": [create]}), context)


def test_open_policy_has_no_invented_closed_argument_restrictions():
    context = catalog()
    policy = next(p for p in context["policies"] if p["id"] == "composed_upload_v1")
    policy["closed_arguments"] = False
    value = {"name": "open", "steps": [step(arguments=[])]}
    schema = json.loads(adapter().prepare_request(context))["response_format"]["json_schema"][
        "schema"]
    assert Draft202012Validator(schema).is_valid(value)
    validate_composed_arguments(ComposedPlannerDraft.model_validate(value), context)
