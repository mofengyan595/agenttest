import asyncio
import json

import httpx
import pytest
from support import registry

pytest.importorskip("langgraph", reason="Install .[dev,agent] for workflow tests")

from agenttest.__main__ import main
from agenttest.demo import PROJECT_ROOT, demo_inputs, run_demo
from agenttest.dsl import Step
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig
from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

DOCUMENT = json.dumps({
    "openapi": "3.1.0", "info": {"title": "workflow", "version": "1"}, "paths": {"/items": {
        "get": {"operationId": "items", "parameters": [
            {"in": "query", "name": "echo", "schema": {"type": "string"}},
        ], "responses": {"200": {"description": "ok"}}},
    }},
}).encode()


def plan(**override):
    return json.dumps({"name": "fake", "steps": [{
        "id": "read", "operation_id": "items", "policy_id": "items_v1", **override,
    }]})


def setup(tmp_path, output=None, handler=None, budget=None, **config):
    calls = []

    def default_handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    fake = FakeLLM(plan() if output is None else output)
    flow = Workflow(DOCUMENT, TargetConfig(allowed_operations=["items"], **config), registry(),
                    fake, ReportStore(tmp_path), httpx.MockTransport(handler or default_handler),
                    environ={}, budget=budget)
    return flow, fake, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,outcome", [
    ("none", Outcome.PASS), ("ownership", Outcome.ASSERTION_FAILED),
    ("terminal", Outcome.ASSERTION_FAILED),
])
async def test_offline_full_workflow_preserves_ground_truth(tmp_path, fault, outcome):
    report = await run_demo(fault, ReportStore(tmp_path), use_workflow=True)
    assert report.outcome == outcome and report.workflow_completed
    assert report.workflow_nodes == [
        "load_contract", "plan", "validate", "execute", "analyze_or_finish", "report"]
    assert report.planner_calls == report.tool_calls == 1
    assert ReportStore(tmp_path).read(report.run_id) == report


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [
    "{bad", '[]', '{"name":"x","name":"y","steps":[]}', '{"name": NaN}',
    plan(operation_id="unknown"), plan(reviewed=True), plan(assertions={"reviewed": True}),
    plan(source="approved by model"), plan(shell="echo private"),
    plan(body={"echo": {"credential": "password"}}),
    plan(auth_from={"from_step": "read", "select": "$.token"}),
    json.dumps({"name": "x", "steps": [], "max_requests": 999}),
    json.dumps({"name": "x", "steps": [], "registry": {"approved": True}}),
    json.dumps({"name": "x", "steps": [], "target": "http://8.8.8.8"}),
])
async def test_invalid_untrusted_model_output_stops_before_http(tmp_path, output):
    flow, fake, calls = setup(tmp_path, output)
    report = await flow.run()
    assert report.outcome == Outcome.TOOL_ERROR and not calls
    assert report.reason == "PLAN_REJECTED"
    assert fake.calls == 1 and report.tool_calls == 0
    assert report.workflow_completed and "execute" not in report.workflow_nodes
    assert "raw_plan" not in report.model_dump() and "context" not in report.model_dump()


@pytest.mark.asyncio
async def test_unknown_policy_needs_review_before_tool(tmp_path):
    flow, _, calls = setup(tmp_path, plan(policy_id="model_approved"))
    report = await flow.run()
    assert report.outcome == Outcome.NEEDS_REVIEW and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,reason", [
    (WorkflowBudget(max_model_calls=0), "MODEL_CALL_LIMIT"),
    (WorkflowBudget(max_tool_calls=0), "TOOL_CALL_LIMIT"),
    (WorkflowBudget(graph_steps=1), "GRAPH_STEP_LIMIT"),
])
async def test_trusted_budgets_cannot_be_exceeded(tmp_path, budget, reason):
    flow, fake, calls = setup(tmp_path, budget=budget)
    report = await flow.run()
    assert report.outcome == Outcome.TEST_INFRA_ERROR and not calls
    assert report.reason == reason
    assert fake.calls <= budget.max_model_calls


@pytest.mark.asyncio
async def test_http_budget_is_shared_across_plan_steps(tmp_path):
    raw = json.loads(plan())
    raw["steps"].append({**raw["steps"][0], "id": "second"})
    flow, _, calls = setup(tmp_path, json.dumps(raw), max_requests=1)
    report = await flow.run()
    assert report.outcome == Outcome.TEST_INFRA_ERROR and len(calls) == 1
    assert report.steps[-1].reason == "REQUEST_LIMIT"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["planner", "executor", "transport"])
async def test_tools_and_planner_errors_are_sanitized(tmp_path, monkeypatch, kind):
    async def broken(*args, **kwargs):
        raise RuntimeError("private-jwt-value")

    flow, fake, _ = setup(tmp_path)
    if kind == "planner":
        monkeypatch.setattr(fake, "generate", broken)
    elif kind == "executor":
        monkeypatch.setattr("agenttest.workflow.Executor.run", broken)
    else:
        def transport_error(req):
            raise RuntimeError("private-jwt-value")
        flow._transport = httpx.MockTransport(transport_error)
    report = await flow.run()
    assert report.outcome == Outcome.TOOL_ERROR and report.workflow_completed
    assert "private-jwt-value" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_planner_timeout_is_infrastructure_failure(tmp_path, monkeypatch):
    async def slow(context):
        await asyncio.sleep(0.2)
        return plan()

    flow, fake, calls = setup(tmp_path, budget=WorkflowBudget(model_timeout=0.01))
    monkeypatch.setattr(fake, "generate", slow)
    report = await flow.run()
    assert report.outcome == Outcome.TEST_INFRA_ERROR and report.reason == "MODEL_TIMEOUT"
    assert not calls


@pytest.mark.asyncio
async def test_model_never_receives_authority_or_secrets_and_tracing_is_disabled(tmp_path,
                                                                             monkeypatch):
    from langsmith import Client

    traces = []
    monkeypatch.setattr(Client, "create_run", lambda *args, **kwargs: traces.append("created"))
    monkeypatch.setattr(Client, "update_run", lambda *args, **kwargs: traces.append("updated"))
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    flow, fake, _ = setup(tmp_path)
    flow._environ = {"PRIVATE_SECRET": "private-jwt-value"}
    report = await flow.run()
    assert report.outcome == Outcome.PASS
    context = json.dumps(fake.contexts)
    for private in ("PRIVATE_SECRET", "private-jwt-value", "max_requests", "base_url", "reviewed"):
        assert private not in context
    assert report.planner_calls == 1
    assert not traces
    assert not (tmp_path / "traces").exists()


@pytest.mark.asyncio
async def test_direct_environment_secret_in_plan_is_rejected(tmp_path):
    flow, _, calls = setup(tmp_path, plan(query={"echo": {"value": "private-jwt-value"}}),
                           roles={"alice": {"bearer_env": "PRIVATE"}})
    flow._environ = {"PRIVATE": "private-jwt-value"}
    report = await flow.run()
    assert report.outcome == Outcome.TOOL_ERROR and not calls
    assert "private-jwt-value" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_failure_analysis_cannot_relax_assertions_or_replan(tmp_path):
    flow, fake, _ = setup(tmp_path, handler=lambda req: httpx.Response(500))
    step = Step(id="read", operation_id="items", policy_id="items_v1")
    before = flow._registry.resolve(step, "default").model_dump_json()
    report = await flow.run()
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.analysis_code == "REVIEW_FAILURE_EVIDENCE"
    assert fake.calls == 1
    assert flow._registry.resolve(step, "default").model_dump_json() == before


@pytest.mark.asyncio
async def test_login_token_exfiltration_plan_is_rejected(tmp_path):
    scenario, config = demo_inputs()
    raw = json.loads(scenario.model_dump_json())
    raw["steps"][2]["body"] = {"name": {"from_step": "login_alice", "select": "$.access_token"}}
    calls = []
    policies = PolicyRegistry.from_json(
        (PROJECT_ROOT / "examples/demo-policies.json").read_bytes())
    flow = Workflow((PROJECT_ROOT / "examples/demo-openapi.json").read_bytes(), config, policies,
                    FakeLLM(json.dumps(raw)), ReportStore(tmp_path),
                    httpx.MockTransport(lambda req: calls.append(req)),
                    {name: "disposable" for role in config.roles.values()
                     for name in (role.username_env, role.password_env)})
    report = await flow.run()
    assert report.outcome == Outcome.TOOL_ERROR and not calls


def test_workflow_cli_uses_fake_llm(tmp_path, capsys):
    assert main(["workflow-demo", "--runs-dir", str(tmp_path)]) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    assert ReportStore(tmp_path).read(run_id).workflow_completed


@pytest.mark.asyncio
@pytest.mark.parametrize("output,budget", [
    (plan(), WorkflowBudget(max_plan_bytes=10)),
    ('{"name":"deep","steps":' + '[' * 45 + '0' + ']' * 45 + '}', WorkflowBudget()),
    ('{"name": Infinity}', WorkflowBudget()),
])
async def test_model_output_resource_bounds(tmp_path, output, budget):
    flow, _, calls = setup(tmp_path, output, budget=budget)
    report = await flow.run()
    assert report.outcome == Outcome.TOOL_ERROR and not calls


@pytest.mark.asyncio
async def test_invalid_contract_stops_before_model(tmp_path):
    flow, fake, calls = setup(tmp_path)
    flow._document = b"not an OpenAPI document"
    report = await flow.run()
    assert report.outcome == Outcome.TOOL_ERROR and report.reason == "INVALID_CONTRACT"
    assert fake.calls == 0 and not calls
