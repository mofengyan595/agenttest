"""Pre-generation constraints must restrict combinations without supplying task answers."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from agenttest.contracts import load_openapi
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.planning_relations import planning_relations
from agenttest.portability_demo import project_inputs, task_inputs


def request(project, task_name="ownership"):
    _, document, target, registry, _ = project_inputs(project)
    task, draft = task_inputs(project, task_name)
    context = planning_context(load_openapi(document), target, registry, task.user_task, {})
    planner = OpenAICompatiblePlanner(ModelSettings(model="offline-relations",
        base_url="https://offline.invalid", api_key="synthetic-relations-key"),
        profile="generic", planning_constraints=True)
    return json.loads(planner.prepare_request(context)), draft, context


@pytest.mark.parametrize("project", ["courses", "tickets"])
def test_schema_accepts_approved_plan_rejects_cross_product(project):
    payload, draft, context = request(project)
    schema = payload["response_format"]["json_schema"]["schema"]
    validator = Draft202012Validator(schema)
    value = draft.model_dump(mode="json")
    assert validator.is_valid(value), list(validator.iter_errors(value))
    # A valid operation and valid policy individually do not form a valid pair.
    crossed = copy.deepcopy(value)
    crossed["steps"][0]["policy_id"] = value["steps"][-1]["policy_id"]
    assert not validator.is_valid(crossed)
    crossed = copy.deepcopy(value)
    crossed["steps"][-1]["role"] = None
    assert not validator.is_valid(crossed)
    public = json.loads(payload["messages"][1]["content"])
    assert public["resource_dependencies"] == planning_relations(context)["resource_dependencies"]
    private = {"requirements", "task_contract", "reference_plan", "fault", "labels"}
    assert not private & public.keys()
    assert all(d["producer_before_consumer"] for d in public["resource_dependencies"])


def test_relations_are_independent_of_task_name_and_internal_rubric():
    _, _, context = request("tickets")
    expected = planning_relations(context)
    context["user_task"] = "完全不同的新需求"
    context["requirements"] = {"hidden": "never publish"}
    assert planning_relations(context) == expected
    assert "never publish" not in json.dumps(expected)


def test_relations_keep_same_and_different_role_sources_distinct():
    _, _, context = request("tickets")
    dependencies = planning_relations(context)["resource_dependencies"]
    assert {d["role_relation"] for d in dependencies} >= {"same", "different"}
    assert all(d["producer_policy"] == "ticket_open_v1" for d in dependencies)
