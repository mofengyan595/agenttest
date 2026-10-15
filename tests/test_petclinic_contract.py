"""Offline validation of the exact configured third-party read-only acceptance inputs."""

import json

import pytest

from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.diagnostics import ValidationFailure
from agenttest.dsl import ArgumentBinding, Scenario
from agenttest.executor import Executor
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract, task_validator

EXAMPLES = PROJECT_ROOT / "examples/portability/petclinic"


@pytest.mark.parametrize("name", ["present", "missing"])
def test_third_party_config_is_bound_closed_and_valid_without_network_or_agent(name):
    contract = load_openapi((EXAMPLES / "openapi.json").read_bytes())
    target = TargetConfig.model_validate_json((EXAMPLES / "target.json").read_bytes())
    registry = PolicyRegistry.from_json((EXAMPLES / "policies.json").read_bytes())
    task = TaskContract.model_validate_json((EXAMPLES / (name + ".task.json")).read_bytes())
    plan = Scenario.model_validate_json((EXAMPLES / (name + ".plan.json")).read_bytes())
    validate = task_validator(task, contract, target, registry)
    validate(task.user_task, plan)
    Executor(target, contract, environ={}, registry=registry).validate(plan)
    assert not target.allow_mutations and target.base_url == "http://127.0.0.1:18886"
    assert len(plan.steps) == 1 and plan.steps[0].role is None
    context = planning_context(contract, target, registry, task.user_task, {})
    assert {op["id"] for op in context["operations"]} == {"getPetType"}
    assert context["roles"] == context["authentication"] == []
    planner = OpenAICompatiblePlanner(ModelSettings(model="offline-petclinic",
        base_url="https://model.invalid/v1", api_key="synthetic-petclinic-key"), profile="generic")
    payload = json.loads(planner.prepare_request(context))
    public = json.loads(payload["messages"][1]["content"])
    assert "requirements" not in public and "expected" not in public
    changed = plan.model_copy(deep=True)
    changed.steps[0].path_parameters["petTypeId"] = ArgumentBinding(value=2)
    with pytest.raises(NeedsReview, match="INTENT_TASK_CONTRACT_PLAN_MISMATCH"):
        validate(task.user_task, changed)
    with pytest.raises(ValidationFailure) as exc:
        Executor(target, contract, environ={}, registry=registry).validate(changed)
    assert exc.value.diagnostic.code == "OPENAPI_VALUE_ENUM"
