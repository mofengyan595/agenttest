"""Root response meaning is explicit; the same deterministic authorization remains final."""

import copy
import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from agenttest.argument_slot_check import (
    ROOT_IDS,
    check_slots,
    reference_inputs,
    root_inputs,
    verify_slots,
)
from agenttest.demo import PROJECT_ROOT
from agenttest.generic_references import root_response_bindings, validate_generic_references
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner


@pytest.mark.parametrize("key", ROOT_IDS)
def test_root_semantics_are_derived_from_trusted_binding_without_changing_input(key):
    *_, draft, _, context = root_inputs(key)
    original = copy.deepcopy(context)
    model = OpenAICompatiblePlanner(ModelSettings(model="offline-roots",
        base_url="https://offline.invalid/v1", api_key="synthetic-root-only"), profile="generic")
    wire = json.loads(model.prepare_request(context))
    public = json.loads(wire["messages"][1]["content"])
    rules = public["root_response_bindings"]
    assert rules == [{"consumer_policy_id": context["policies"][1]["id"],
        "location": "path_parameters", "name": draft.steps[1].arguments[0].name,
        "source_policy_id": context["policies"][0]["id"],
        "source_operation_id": draft.steps[0].operation_id, "select": "$"}]
    assert "entire response" in wire["messages"][0]["content"]
    schema = wire["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    checker = Draft202012Validator(schema)
    data = draft.model_dump(mode="json")
    assert checker.is_valid(data)
    data["steps"][1]["arguments"][0]["select"] = "$.key"
    assert not checker.is_valid(data)
    validate_generic_references(draft, context)
    assert context == original and model.stats.calls == 0


def test_nested_and_array_bindings_are_not_inferred_as_whole_response():
    for key in ("GR1101", "GR1102"):
        *_, context = reference_inputs(key)
        assert root_response_bindings(context) == []


@pytest.mark.asyncio
async def test_fresh_root_controls_and_simulated_trials_are_frozen_and_cold_verified():
    pytest.importorskip("agenttest.workflow")
    output = PROJECT_ROOT / "runs" / ("pytest-root-meaning-" + uuid4().hex)
    result = await check_slots(output, root_responses=True)
    assert result["controls_passed"] == 3 and result["generation_successes"] == 9
    assert result["mock_business_requests"] == 24 and result["real_model_calls"] == 0
    assert verify_slots(output)["rescored_runs"] == 12
