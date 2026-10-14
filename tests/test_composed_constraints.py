"""Cross-field schema parity and value-free diagnostics for composition planning."""

import itertools
import json

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agenttest.composed_demo import composed_inputs
from agenttest.composed_plan import ComposedDraftStep
from agenttest.contracts import summarize_openapi
from agenttest.diagnostics import Layer, schema_diagnostic
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerFailure
from agenttest.planner_input import planning_context


def catalog(task="alice 创建课程“结构诊断甲”；核对创建者读取一致。"):
    app, target, registry, _ = composed_inputs()
    return planning_context(summarize_openapi(app.openapi()), target, registry, task, {})


def adapter(transport=None):
    return OpenAICompatiblePlanner(ModelSettings(model="offline-constraints",
        base_url="https://model.invalid/v1", api_key="synthetic-schema-credential"),
        transport, profile="mini_composed")


def step(**changes):
    return {"id": "new_upload", "operation_id": "createBatch", "role": "alice",
            "policy_id": "composed_upload_v1", "arguments": [],
            "csv_fixture_id": "mini_two_rows_v1", "poll_policy_id": None, **changes}


@pytest.mark.parametrize("changes,code", [
    ({"policy_id": "composed_read_v1"}, "MODEL_SCHEMA_COMPOSED_POLICY_MISMATCH"),
    ({"role": "bob"}, "MODEL_SCHEMA_COMPOSED_ROLE_UNAPPROVED"),
    ({"csv_fixture_id": None}, "MODEL_SCHEMA_COMPOSED_CSV_REFERENCE"),
    ({"poll_policy_id": "composed_poll_v1"}, "MODEL_SCHEMA_COMPOSED_POLL_REFERENCE"),
])
def test_cross_field_errors_have_distinct_safe_codes(changes, code):
    with pytest.raises(ValidationError) as exc:
        ComposedDraftStep.model_validate(step(**changes))
    diagnostic = schema_diagnostic(exc.value, Layer.MODEL)
    assert diagnostic.code == code and diagnostic.fields == [""]
    assert set(diagnostic.model_dump()) == {"layer", "code", "fields"}
    for raw in ("alice", "bob", "composed_read_v1", "mini_two_rows_v1"):
        assert raw not in diagnostic.model_dump_json()


def test_sent_schema_matches_all_cross_field_choices_without_task_answers():
    context = catalog()
    payload = json.loads(adapter().prepare_request(context))
    schema = payload["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    assert len(schema["$defs"]["ComposedDraftStep"]["anyOf"]) == 7
    validator = Draft202012Validator(schema)
    valid = 0
    for operation, policy, role, csv, poll in itertools.product(
        [op["id"] for op in context["operations"]],
        [p["id"] for p in context["policies"]], ["alice", "bob"],
        [None, "mini_two_rows_v1"], [None, "composed_poll_v1"],
    ):
        value = step(operation_id=operation, policy_id=policy, role=role,
                     csv_fixture_id=csv, poll_policy_id=poll)
        definition = next(p for p in context["policies"] if p["id"] == policy)
        value["arguments"] = [{"location": r["location"], "name": r["name"],
            "value": "schema matrix" if r["literal_schema"] is not None else None,
            "from_step": None if r["literal_schema"] is not None else "producer",
            "select": None if r["literal_schema"] is not None else r["source_selector"]}
            for r in definition["binding_rules"]]
        try:
            ComposedDraftStep.model_validate(value)
        except ValidationError:
            local_valid = False
        else:
            local_valid = True
        assert validator.is_valid({"name": "new matrix", "steps": [value]}) == local_valid
        valid += local_valid
    assert valid == 11
    public = json.loads(payload["messages"][1]["content"])
    other = json.loads(adapter().prepare_request(catalog(
        "bob 创建课程“结构诊断乙”；核对创建者读取一致；验证 alice 读取被拒绝。")))
    other_public = json.loads(other["messages"][1]["content"])
    assert public.pop("user_task") != other_public.pop("user_task")
    assert public == other_public and len(public["step_options"]) == 7
    assert not {"expected", "goals", "requirements", "rubric", "rejection_code"} & public.keys()
    prompt = payload["messages"][0]["content"]
    assert "Do not add goals, operations or logins" not in prompt
    assert "authentication" in prompt and "step_options" in prompt


def test_schema_choices_remain_within_the_visible_role_and_policy_catalog():
    context = catalog()
    context["roles"] = ["bob"]
    payload = json.loads(adapter().prepare_request(context))
    choices = json.loads(payload["messages"][1]["content"])["step_options"]
    assert len(choices) == 4
    assert all(choice["roles"] == ["bob"] for choice in choices)
    assert all(choice["csv_fixture_id"] is choice["poll_policy_id"] is None
               for choice in choices)
    context["policies"] = []
    with pytest.raises(PlannerFailure) as exc:
        adapter().prepare_request(context)
    assert exc.value.reason == "MODEL_INPUT_REJECTED"


@pytest.mark.asyncio
async def test_remote_schema_violation_is_not_repaired_retried_or_exposed():
    calls = []
    private = "private-generated-literal"
    draft = {"name": "diagnostic", "steps": [step(csv_fixture_id=None, arguments=[{
        "location": "query", "name": "note", "value": private,
        "from_step": None, "select": None}])]}

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(draft)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    planner = adapter(httpx.MockTransport(handle))
    with pytest.raises(PlannerFailure) as exc:
        await planner.generate(catalog())
    assert len(calls) == planner.stats.calls == 1
    assert exc.value.reason == "MODEL_INVALID_RESPONSE"
    assert exc.value.diagnostic.code == "MODEL_SCHEMA_COMPOSED_CSV_REFERENCE"
    assert exc.value.diagnostic.fields == ["steps.0"]
    assert planner.stats.total_tokens == 150
    assert private not in planner.stats.model_dump_json()
