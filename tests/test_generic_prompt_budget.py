"""Old developer demand only: reduce prompt overhead without raising the request budget."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agenttest.goal_derivation import derive_goal
from agenttest.goal_runtime import GoalPlanner
from agenttest.llm_planner import GenericPlannerDraft, ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.project_goals import load_goal_project


@pytest.mark.asyncio
async def test_old_multi_producer_flow_fits_original_input_limit_without_losing_schema():
    pytest.importorskip("langgraph")
    from agenttest.nl_entry_fixture import CatalogFixturePlanner

    project = load_goal_project(Path(__file__).resolve().parents[1] /
        "examples/trusted-testing/laboratory/registered.project.json")
    text = "先登记容器，再登记样本，然后装载、查验、跨角色拒绝"
    adapter = OpenAICompatiblePlanner(ModelSettings(model="ecnu-plus", protocol="chatecnu",
        base_url="https://offline.invalid", api_key="offline-input-check",
        max_input_tokens=20000, max_output_tokens=4096, max_total_tokens=100000),
        profile="generic", planning_constraints=True)
    public = []

    class Probe:
        async def generate(self, context):
            public.append(context)
            return adapter.prepare_request(context)

    encoded = await GoalPlanner(Probe(), project, derive_goal(project, text)).generate(
        planning_context(project.contract, project.target, project.registry, text, {}))
    assert adapter.stats.input_token_upper_bound <= 20000
    assert adapter.stats.calls == 0
    payload = json.loads(encoded)
    schema = payload["response_format"]["json_schema"]["schema"]
    draft = json.loads(await CatalogFixturePlanner().generate(public[0]))
    # The fixture's Scenario is converted to the same wire Draft, not sent to a model.
    steps = []
    for step in draft["steps"]:
        args = []
        for location in ("body", "path_parameters", "query"):
            for name, binding in step.get(location, {}).items():
                args.append(dict(location=location, name=name,
                    value=binding.get("value"), from_step=binding.get("from_step"),
                    select=binding.get("select") if binding.get("from_step") else None))
        steps.append(dict(id=step["id"], operation_id=step["operation_id"], role=step["role"],
                          policy_id=step["policy_id"], arguments=args))
    wire = dict(name=draft["name"], steps=steps)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(wire), list(validator.iter_errors(wire))
    assert len(GenericPlannerDraft.model_validate(wire).scenario().steps) == 5
    original = json.dumps(wire)
    for field, value in (("role", "unapproved"), ("operation_id", "unapproved"),
                         ("policy_id", "unapproved")):
        changed = json.loads(original)
        changed["steps"][0][field] = value
        assert not validator.is_valid(changed)
    assert len(public[0]["policies"]) == 5 and len(public[0]["operations"]) == 4
    assert "reference_plan" not in json.loads(payload["messages"][1]["content"])
