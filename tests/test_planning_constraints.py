"""Offline catalog constraints narrow choices without changing trusted approvals."""

import json

import httpx
import pytest
from test_llm_planner import completion
from test_planner_input import inputs, scenario

from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context


@pytest.mark.parametrize("creator,cross", [("alice", False), ("bob", False),
                                          ("alice", True), ("bob", True)])
def test_catalog_scope_and_authentication_edges(creator, cross):
    contract, config, registry = inputs()
    before = registry.digest()
    other = "bob" if creator == "alice" else "alice"
    task = f'{creator} 创建“本轮新合成课程”并查询核对 ID 和名称。'
    if cross:
        task += f'再让 {other} 查询同一课程被拒绝。'
    context = planning_context(contract, config, registry, task, {})
    roles = {creator, other} if cross else {creator}
    assert set(context["roles"]) == roles
    assert {p["id"] for p in context["policies"]} == {
        "nl_login_v1", "nl_create_v1", "nl_read_v1",
        *(["nl_forbidden_v1"] if cross else [])}
    assert all(set(p["roles"]) <= roles for p in context["policies"])
    assert next(p for p in context["policies"] if p["id"] == "nl_create_v1")[
        "roles"] == [creator]
    assert next(p for p in context["policies"] if p["id"] == "nl_read_v1")[
        "roles"] == [creator]
    if cross:
        assert next(p for p in context["policies"] if p["id"] == "nl_forbidden_v1")[
            "roles"] == [other]
    assert {a["role"] for a in context["authentication"]} == roles
    for auth in context["authentication"]:
        assert auth["login_operation_id"] == "login"
        assert set(auth["before_operation_ids"]) == (
            {"createCourse", "getCourse"} if auth["role"] == creator else {"getCourse"})
    read = next(p for p in context["policies"] if p["id"] == "nl_read_v1")
    assert read["binding_rules"][0]["source_policy_id"] == "nl_create_v1"
    assert read["binding_rules"][0]["source_selector"] == "$.courseId"
    assert registry.digest() == before


def test_unknown_goal_does_not_guess_roles_or_add_policies():
    contract, config, registry = inputs()
    context = planning_context(contract, config, registry, '验证“课程”的收费与退款', {})
    assert context["roles"] == list(config.roles)
    assert len(context["policies"]) == 4
    assert {p["id"] for p in context["policies"]} == {
        p["id"] for p in registry.catalog(config.business_scenario)}


def test_catalog_never_adds_mutation_authorization():
    contract, config, registry = inputs()
    config.allow_mutations = False
    context = planning_context(contract, config, registry, 'alice 创建“甲”并查询', {})
    assert {op["id"] for op in context["operations"]} == {"getCourse"}
    assert {p["id"] for p in context["policies"]} == {"nl_read_v1"}
    assert context["authentication"] == []


@pytest.mark.asyncio
async def test_structured_request_uses_narrowed_enums_and_auth_edges():
    contract, config, registry = inputs()
    task = 'bob 创建“本轮新课程”并查询核对名称和 ID。'
    calls = []

    def handler(request):
        calls.append(request)
        plan = scenario(name="本轮新课程", creator="bob")
        draft = {"name": plan.name, "steps": [
            {"id": s.id, "operation_id": s.operation_id, "role": s.role,
             "policy_id": s.policy_id, "arguments": [
                 {"location": loc, "name": key, "value": b.value,
                  "from_step": b.from_step, "select": b.select if b.from_step else None}
                 for loc in ("body", "path_parameters", "query")
                 for key, b in getattr(s, loc).items()]}
            for s in plan.steps]}
        return httpx.Response(200, json=completion(json.dumps(draft)))

    planner = OpenAICompatiblePlanner(ModelSettings(
        model="mock-response", base_url="https://model.invalid/v1", api_key="synthetic-key"),
        httpx.MockTransport(handler))
    raw = await planner.generate(planning_context(contract, config, registry, task, {}))
    assert len(json.loads(raw)["steps"]) == 3 and planner.stats.calls == 1
    payload = json.loads(calls[0].content)
    properties = payload["response_format"]["json_schema"]["schema"]["$defs"][
        "DraftStep"]["properties"]
    assert properties["role"]["enum"] == ["bob", None]
    assert "nl_forbidden_v1" not in properties["policy_id"]["enum"]
    public = json.loads(payload["messages"][1]["content"])
    assert public["authentication"][0]["login_operation_id"] == "login"
    assert "DEMO_BOB" not in calls[0].content.decode()
