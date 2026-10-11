import json

import httpx
import pytest
from support import registry

from agenttest.contracts import ContractSummary, Operation, summarize_openapi
from agenttest.demo import PROJECT_ROOT, demo_inputs
from agenttest.dsl import ArgumentBinding, Scenario, Step
from agenttest.executor import Executor
from agenttest.mini_target import create_target
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig


@pytest.mark.parametrize("forgery", [
    {"assertions": {"reviewed": True, "source": "model", "status": {"codes": [200]}}},
    {"reviewed": True}, {"source": "model"},
    {"body": {"password": {"credential": "password"}}},
    {"auth_from": {"from_step": "login", "select": "$.access_token"}},
])
def test_plan_cannot_authorize_assertions_or_credentials(forgery):
    with pytest.raises(ValueError):
        Step.model_validate({"id": "read", "operation_id": "items", **forgery})


@pytest.mark.parametrize("override,scope", [
    ({"policy_id": "unknown"}, "default"), ({"role": "admin"}, "default"),
    ({"operation_id": "other"}, "default"), ({}, "other_business"),
])
def test_registry_checks_all_scope_dimensions(override, scope):
    step = Step(id="read", operation_id="items", policy_id="items_v1")
    step = Step.model_validate({**step.model_dump(), **override})
    with pytest.raises(NeedsReview):
        registry().resolve(step, scope)


def test_resolved_policy_is_a_copy_and_demo_bindings_are_scoped():
    policies = registry()
    step = Step(id="read", operation_id="items", policy_id="items_v1")
    policies.resolve(step, "default").assertions.status.codes.append(500)
    assert policies.resolve(step, "default").assertions.status.codes == [200]
    scenario, config = demo_inputs()
    policies = PolicyRegistry.from_json((PROJECT_ROOT / "examples/demo-policies.json").read_bytes())
    scenario.steps[4].path_parameters = {}
    with pytest.raises(NeedsReview):
        policies.resolve(scenario.steps[4], config.business_scenario)


@pytest.mark.asyncio
async def test_unknown_policy_cannot_send_any_request(tmp_path):
    calls = []
    executor = Executor(TargetConfig(allowed_operations=["items"]), ContractSummary(
        version="3.1.0", operations=[Operation(method="GET", path="/", operation_id="items")]),
        ReportStore(tmp_path), httpx.MockTransport(lambda req: calls.append(req)))
    report = await executor.run(Scenario(name="unknown", steps=[
        Step(id="read", operation_id="items", policy_id="self-approved")]))
    assert report.outcome == Outcome.NEEDS_REVIEW and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["$.access_token", "$", "$.other"])
async def test_authentication_response_is_never_cross_step_data(tmp_path, selector):
    scenario, config = demo_inputs()
    scenario.steps[2].body = {"name": ArgumentBinding(from_step="login_alice", select=selector)}
    scenario = Scenario.model_validate(scenario.model_dump())
    calls = []
    executor = Executor(config, summarize_openapi(create_target({}).openapi()),
                        ReportStore(tmp_path), httpx.MockTransport(lambda req: calls.append(req)),
                        {"DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": "private-a",
                         "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": "private-b"})
    report = await executor.run(scenario)
    assert report.outcome == Outcome.TOOL_ERROR and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", ["vault-jwt-value", "246810"])
async def test_secret_aliases_cannot_be_forwarded_or_written_to_evidence(tmp_path, secret):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"innocent_alias": secret})

    contract = ContractSummary(version="3.1.0", operations=[Operation(
        method="GET", path="/items", operation_id="items",
        parameters=[{"name": "echo", "in": "query", "schema": {"type": "string"}}],
    )])
    config = TargetConfig(allowed_operations=["items"], roles={"a": {"bearer_env": "SECRET"}})
    scenario = Scenario(name="leak", steps=[
        Step(id="first", operation_id="items", policy_id="items_v1"),
        Step(id="second", operation_id="items", policy_id="items_v1",
             query={"echo": {"from_step": "first", "select": "$.innocent_alias"}}),
    ])
    executor = Executor(config, contract, ReportStore(tmp_path), httpx.MockTransport(handler),
                        {"SECRET": secret}, registry=registry())
    report = await executor.run(scenario)
    assert report.outcome == Outcome.TOOL_ERROR and len(calls) == 1
    assert secret not in executor.store.read(report.run_id).model_dump_json()
    scenario.steps[1].query = {"echo": ArgumentBinding(value=secret)}
    scenario = Scenario.model_validate(json.loads(scenario.model_dump_json()))
    report = await executor.run(scenario)
    assert report.outcome == Outcome.TOOL_ERROR and len(calls) == 2
