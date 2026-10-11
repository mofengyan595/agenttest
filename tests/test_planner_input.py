import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT, demo_inputs
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.mini_target import create_target
from agenttest.planner_input import planning_context, validate_user_task
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.report import Outcome, ReportStore


def inputs():
    _, config = demo_inputs()
    config.business_scenario = "mini_target_nl_v1"
    registry = PolicyRegistry.from_json((PROJECT_ROOT / "examples/nl-policies.json").read_bytes())
    contract = load_openapi((PROJECT_ROOT / "examples/demo-openapi.json").read_bytes())
    return contract, config, registry


def scenario(cross=False, name="线性代数", creator="alice"):
    other = "bob" if creator == "alice" else "alice"
    steps = [{"id": "auth", "operation_id": "login", "role": creator,
              "policy_id": "nl_login_v1"}]
    if cross:
        steps.append({"id": "other_auth", "operation_id": "login", "role": other,
                      "policy_id": "nl_login_v1"})
    steps += [{"id": "create", "operation_id": "createCourse", "role": creator,
               "policy_id": "nl_create_v1", "body": {"name": {"value": name}}},
              {"id": "read", "operation_id": "getCourse", "role": creator,
               "policy_id": "nl_read_v1",
               "path_parameters": {"courseId": {"from_step": "create", "select": "$.courseId"}}}]
    if cross:
        steps.append({"id": "forbidden", "operation_id": "getCourse", "role": other,
                      "policy_id": "nl_forbidden_v1",
                      "path_parameters": {"courseId": {"from_step": "create",
                                                       "select": "$.courseId"}}})
    return Scenario.model_validate({"name": "natural language fixture", "steps": steps})


@pytest.mark.parametrize("value", ["", " ", "x" * 2001, "x\x00y", "x\u202ey",
                                    "访问 http://127.0.0.1", "password=private-value"])
def test_task_rejects_unsafe_content(value):
    with pytest.raises(ValueError):
        validate_user_task(value)


def test_filtered_context_contains_constraints_not_private_annotations():
    contract, config, registry = inputs()
    op = next(o for o in contract.operations if o.operation_id == "createCourse")
    op.request_body["content"]["application/json"]["schema"]["description"] = "private-value"
    context = planning_context(contract, config, registry, "创建并查询课程", {})
    raw = json.dumps(context)
    for omitted in ("private-value", "base_url", "DEMO_ALICE_PASSWORD", "reviewed", "source\""):
        assert omitted not in raw
    assert {o["id"] for o in context["operations"]} == {"login", "createCourse", "getCourse"}
    login = next(o for o in context["operations"] if o["id"] == "login")
    assert login["request_body"] is None and login["authentication_executor_managed"]
    assert "required" in next(o for o in context["operations"] if o["id"] == "createCourse")[
        "request_body"]["content"]["application/json"]


def test_unsupported_schema_is_marked_and_secret_enums_are_blocked():
    contract, config, registry = inputs()
    op = next(o for o in contract.operations if o.operation_id == "createCourse")
    schema = op.request_body["content"]["application/json"]["schema"]
    schema["properties"]["name"]["pattern"] = ".*"
    context = planning_context(contract, config, registry, "创建课程", {})
    assert next(o for o in context["operations"] if o["id"] == "createCourse")[
        "request_validation"] == "NEEDS_REVIEW"
    schema["properties"]["name"]["enum"] = ["sk-" + "syntheticsecret" * 2]
    with pytest.raises(ValueError):
        planning_context(contract, config, registry, "创建课程", {})


@pytest.mark.parametrize("creator", ["alice", "bob"])
def test_parameterized_policy_keeps_equality_and_ownership(creator):
    contract, config, registry = inputs()
    executor = Executor(config, contract, registry=registry, environ={
        "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": "synthetic-a",
        "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": "synthetic-b"})
    _, policies = executor.validate(scenario(True, creator=creator))
    assert policies["read"].assertions.status.codes == [200]
    assert policies["read"].assertions.fields[-1].expected == "线性代数"
    assert policies["forbidden"].assertions.status.codes == [403]


@pytest.mark.parametrize("mutation", ["role", "select", "literal", "name", "extra", "source"])
def test_parameter_rules_do_not_approve_illegal_paths(mutation):
    contract, config, registry = inputs()
    value = scenario(True)
    if mutation == "role":
        value.steps[-1].role = "alice"
    elif mutation == "select":
        value.steps[-1].path_parameters["courseId"].select = "$.name"
    elif mutation == "literal":
        from agenttest.dsl import ArgumentBinding
        value.steps[-1].path_parameters["courseId"] = ArgumentBinding(value=1)
    elif mutation == "name":
        value.steps[2].body["name"].value = "x" * 81
    elif mutation == "extra":
        value.steps[2].query["extra"] = value.steps[2].body["name"]
    elif mutation == "source":
        value.steps[-1].path_parameters["courseId"].from_step = "read"
    with pytest.raises((NeedsReview, ValueError)):
        Executor(config, contract, registry=registry, environ={
            "DEMO_ALICE_USERNAME": "a", "DEMO_ALICE_PASSWORD": "synthetic-a",
            "DEMO_BOB_USERNAME": "b", "DEMO_BOB_PASSWORD": "synthetic-b"}).validate(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,expected", [("none", Outcome.PASS),
                                            ("ownership", Outcome.ASSERTION_FAILED)])
async def test_parameterized_end_to_end_preserves_business_failure(tmp_path, fault, expected):
    contract, config, registry = inputs()
    app = create_target({"alice": "synthetic-a", "bob": "synthetic-b"}, fault)
    result = await Executor(config, contract, ReportStore(tmp_path), httpx.ASGITransport(app), {
        "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": "synthetic-a",
        "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": "synthetic-b"}, registry).run(
            scenario(True))
    assert result.outcome == expected
    assert result.steps[-1].checks[0].expected == [403]


@pytest.mark.asyncio
async def test_wrong_returned_course_name_is_not_hidden(tmp_path):
    contract, config, registry = inputs()
    responses = iter([{"access_token": "synthetic-token"}, {"courseId": 1, "name": "线性代数"},
                      {"courseId": 1, "name": "incorrect"}])
    statuses = iter([200, 201, 200])
    result = await Executor(config, contract, ReportStore(tmp_path), httpx.MockTransport(
        lambda request: httpx.Response(next(statuses), json=next(responses))), {
            "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": "synthetic-a"},
        registry).run(scenario())
    assert result.outcome == Outcome.ASSERTION_FAILED
    assert any(not check.passed for check in result.steps[-1].checks)
