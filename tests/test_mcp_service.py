import asyncio
import json
import secrets
from uuid import uuid4

import httpx
import pytest

from agenttest.demo import demo_inputs
from agenttest.executor import Executor
from agenttest.mcp_service import MCPService, MCPSettings
from agenttest.report import ReportStore


@pytest.fixture
def service(tmp_path):
    return MCPService(tmp_path / "mcp")


@pytest.mark.asyncio
async def test_catalog_and_filtered_contract(service, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-school-key")
    monkeypatch.setenv("DEMO_ALICE_PASSWORD", "private-real-password")
    catalog = await service.call("list_tests")
    assert catalog["scenarios"] == ["mini_normal", "mini_ownership_fault", "mini_terminal_fault"]
    assert not catalog["courseinsight_enabled"]
    public = await service.call("get_contract")
    serialized = json.dumps(public)
    for secret in ("private-school-key", "private-real-password", "http://", "username_env",
                   "password_env", "examples"):
        assert secret not in serialized
    assert '"default"' not in json.dumps(public["operations"])
    assert {op["id"] for op in public["operations"]} == {
        "login", "createCourse", "getCourse", "createBatch", "getBatch", "getBatchResults"}
    assert next(op for op in public["operations"] if op["id"] == "login")["request_body"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,outcome,steps,requests", [
    ("mini_normal", "PASS", 8, 9),
    ("mini_ownership_fault", "ASSERTION_FAILED", 5, 5),
    ("mini_terminal_fault", "ASSERTION_FAILED", 7, 8),
])
async def test_approved_scenarios(service, scenario, outcome, steps, requests):
    run_id = uuid4().hex
    result = await service.call("run_scenario", {"scenario_id": scenario, "invocation_id": run_id})
    assert result["ok"]
    report = result["report"]
    assert report["run_id"] == run_id
    assert report["outcome"] == outcome
    assert len(report["steps"]) == steps
    assert sum(len(s["attempts"]) for s in report["steps"]) == requests
    assert sum(s["operation_id"] == "createCourse" for s in report["steps"]) == 1
    assert report["investigations"] == []
    assert (await service.call("read_report", {"run_id": run_id}))["report"] == report
    for step in report["steps"]:
        assert step["attempts"]
        assert all(a["attempt_id"] for a in step["attempts"])
    for forbidden in ("Authorization", "password", "Bearer ", "csv_text"):
        assert forbidden not in json.dumps(report)
    for step in report["steps"][:2]:
        check = next(c for c in step["checks"] if c["selector"] == "$.access_token")
        assert check["actual"] == check["expected"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_validation_never_executes_or_saves(service, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Validation must not execute HTTP")

    monkeypatch.setattr(Executor, "run", forbidden)
    scenario, _ = demo_inputs()
    result = await service.call("validate_plan", {"plan_json": scenario.model_dump_json()})
    assert result["ok"] and result["valid"] and not result["execution_authorized"]
    assert not service._store.root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reviewed", "assertions", "policy", "operation", "env",
                                      "role", "resource", "url"])
async def test_untrusted_plans_rejected(service, change):
    plan = demo_inputs()[0].model_dump()
    if change == "reviewed":
        plan["steps"][2]["reviewed"] = True
    elif change == "assertions":
        plan["steps"][2]["assertions"] = {"status": [200]}
    elif change == "policy":
        plan["steps"][2]["policy_id"] = "forged"
    elif change == "operation":
        plan["steps"][2]["operation_id"] = "deleteEverything"
    elif change == "env":
        plan["steps"][2]["body"]["name"] = {"env": "OPENAI_API_KEY"}
    elif change == "role":
        plan["steps"][2]["role"] = "admin"
    elif change == "resource":
        plan["steps"][3]["path_parameters"]["courseId"] = {"value": 999}
    else:
        plan["base_url"] = "http://production.invalid"
    result = await service.call("validate_plan", {"plan_json": json.dumps(plan)})
    assert not result["ok"]
    assert not service._store.root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{", '{"steps":[],"steps":[]}', "[1]", "null",
                                   '{"private-key-name":"private-secret-value"}'])
async def test_json_and_dsl_errors_are_redacted(service, raw):
    result = await service.call("validate_plan", {"plan_json": raw})
    assert not result["ok"]
    assert "private-secret-value" not in json.dumps(result)
    assert "private-key-name" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("arbitrary_http", {"url": "http://production.invalid"}),
    ("run_scenario", {"scenario_id": "courseinsight", "invocation_id": uuid4().hex}),
    ("run_scenario", {"scenario_id": "mini_normal", "invocation_id": uuid4().hex,
                      "api_key": "private-secret"}),
    ("run_scenario", {"scenario_id": "mini_normal", "invocation_id": "../../outside"}),
    ("get_contract", {"target_url": "http://other.invalid"}),
    ("list_tests", {"max_runs": 1000}),
    ("read_report", {"run_id": "../report.json"}),
    ("validate_plan", {"plan_json": 123}),
])
async def test_no_client_authority(service, name, args):
    result = await service.call(name, args)
    assert not result["ok"]
    assert "private-secret" not in json.dumps(result)
    assert not service._store.root.exists()


@pytest.mark.asyncio
async def test_repeat_and_restart_do_not_replay(service, monkeypatch):
    args = {"scenario_id": "mini_normal", "invocation_id": uuid4().hex}
    result = await service.call("run_scenario", args)

    async def forbidden(*args, **kwargs):
        pytest.fail("Repeated invocation must never replay HTTP")

    monkeypatch.setattr(Executor, "run", forbidden)
    repeat = await service.call("run_scenario", args)
    assert repeat["cached"] and repeat["report"] == result["report"]
    restarted = MCPService(service._store.root)
    assert (await restarted.call("run_scenario", args))["cached"]
    args["scenario_id"] = "mini_ownership_fault"
    assert (await restarted.call("run_scenario", args))["error_code"] == "MCP_INVOCATION_CONFLICT"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["crash", "cancel", "report"])
async def test_claim_precedes_execution_and_failure_is_not_resumable(service, monkeypatch, failure):
    args = {"scenario_id": "mini_normal", "invocation_id": uuid4().hex}
    calls = []
    original = Executor.run

    async def fail(executor, scenario, *, report):
        calls.append(report.run_id)
        assert (service._store.root / report.run_id / "mcp-claim.json").is_file()
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "report":
            return await original(executor, scenario, report=report)
        raise RuntimeError("private-sensitive-exception")

    monkeypatch.setattr(Executor, "run", fail)
    if failure == "report":
        def failed_save(*args):
            raise OSError("private-sensitive-path")
        monkeypatch.setattr(ReportStore, "save", failed_save)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await service.call("run_scenario", args)
    else:
        assert (await service.call("run_scenario", args))["error_code"] == "MCP_INTERNAL_ERROR"
    restarted = MCPService(service._store.root)
    assert (await restarted.call("run_scenario", args))["error_code"] == "MCP_RUN_NOT_RESUMABLE"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_invocation_only_runs_once(service, monkeypatch):
    original = Executor.run
    calls = []

    async def count(executor, scenario, **kwargs):
        calls.append(1)
        return await original(executor, scenario, **kwargs)

    monkeypatch.setattr(Executor, "run", count)
    args = {"scenario_id": "mini_normal", "invocation_id": uuid4().hex}
    results = await asyncio.gather(*(service.call("run_scenario", args) for _ in range(3)))
    assert len(calls) == 1
    assert sum(result["cached"] for result in results) == 2


@pytest.mark.asyncio
async def test_zero_run_budget_and_tool_budget(tmp_path):
    service = MCPService(tmp_path, MCPSettings(max_runs=0, max_tool_calls=2))
    args = {"scenario_id": "mini_normal", "invocation_id": uuid4().hex}
    assert (await service.call("run_scenario", args))["error_code"] == "MCP_RUN_BUDGET_EXHAUSTED"
    assert (await service.call("get_contract"))["ok"]
    assert (await service.call("get_contract"))["error_code"] == "MCP_TOOL_BUDGET_EXHAUSTED"
    assert not (tmp_path / args["invocation_id"]).exists()


@pytest.mark.asyncio
async def test_report_namespace_excludes_other_reports(service):
    assert (await service.call("read_report", {"run_id": uuid4().hex}))["error_code"] == (
        "MCP_RUN_NOT_FOUND")
    run_id = uuid4().hex
    directory = service._store.root / run_id
    directory.mkdir(parents=True)
    (directory / "report.json").write_text('{"private":"private-secret"}')
    assert (await service.call("read_report", {"run_id": run_id}))["error_code"] == (
        "MCP_RUN_NOT_FOUND")


@pytest.mark.asyncio
async def test_auth_response_cannot_bind_to_business_data(service):
    plan = demo_inputs()[0].model_dump()
    plan["steps"][2]["body"]["name"] = {"from_step": "login_alice", "select": "$.access_token"}
    result = await service.call("validate_plan", {"plan_json": json.dumps(plan)})
    assert result["diagnostic"]["code"] == "DEPENDENCY_AUTH_RESPONSE"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "transport", "report_after_create"])
async def test_http_failures_do_not_duplicate_creation(service, monkeypatch, failure):
    sent = []
    original_send = httpx.AsyncClient.send
    original_save = ReportStore.save

    async def send(client, request, **kwargs):
        sent.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/api/courses":
            if failure == "timeout":
                raise httpx.ReadTimeout("private-sensitive-timeout")
            if failure == "transport":
                raise RuntimeError("private-sensitive-transport")
        return await original_send(client, request, **kwargs)

    def save(store, report):
        if failure == "report_after_create" and any(
            s.operation_id == "createCourse" and any(a.status_code == 201 for a in s.attempts)
            for s in report.steps
        ):
            raise OSError("private-sensitive-report-path")
        return original_save(store, report)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(ReportStore, "save", save)
    args = {"scenario_id": "mini_normal", "invocation_id": uuid4().hex}
    first = await service.call("run_scenario", args)
    assert not first["ok"] or first["report"]["outcome"] != "PASS"
    await service.call("run_scenario", args)
    await MCPService(service._store.root).call("run_scenario", args)
    assert sent.count(("POST", "/api/courses")) == 1
    assert "private-sensitive" not in json.dumps(first)


@pytest.mark.asyncio
async def test_generated_credentials_never_persist(service, monkeypatch):
    generated = []
    original = secrets.token_urlsafe

    def token(*args):
        value = original(*args)
        generated.append(value)
        return value

    monkeypatch.setattr(secrets, "token_urlsafe", token)
    result = await service.call("run_scenario", {"scenario_id": "mini_normal",
                                                "invocation_id": uuid4().hex})
    assert result["report"]["outcome"] == "PASS"
    assert len(generated) >= 4
    persisted = "".join(p.read_text() for p in service._store.root.rglob("*.json"))
    for value in generated:
        assert value not in persisted and value not in json.dumps(result)


@pytest.mark.asyncio
async def test_symlink_directory_rejected(service, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    service._store.root.mkdir()
    run_id = uuid4().hex
    try:
        (service._store.root / run_id).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    assert not (await service.call("read_report", {"run_id": run_id}))["ok"]
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("kwargs", [{"max_runs": -1}, {"max_runs": True},
                                     {"max_tool_calls": 0}, {"max_runs": 21}])
def test_operator_budget_bounds(kwargs):
    with pytest.raises(ValueError):
        MCPSettings(**kwargs)
