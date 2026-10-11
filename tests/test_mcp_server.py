"""SDK client exercises protocol discovery, strict schemas and the redaction boundary."""

import json
from uuid import uuid4

import pytest

pytest.importorskip("mcp", reason="Install optional MCP SDK to test the protocol")

from mcp import Client  # noqa: E402

from agenttest.demo import demo_inputs  # noqa: E402
from agenttest.mcp_server import create_server  # noqa: E402
from agenttest.mcp_service import REQUESTS, MCPService, MCPSettings  # noqa: E402


@pytest.fixture
def server(tmp_path):
    return create_server(MCPService(tmp_path))


@pytest.mark.asyncio
async def test_sdk_discovery_schema_and_execution(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == set(REQUESTS)
        for tool in tools:
            assert tool.input_schema["additionalProperties"] is False
            assert not tool.annotations.open_world_hint
            assert tool.annotations.read_only_hint == (tool.name != "run_scenario")
        validation = await client.call_tool("validate_plan", {
            "plan_json": demo_inputs()[0].model_dump_json()})
        assert not validation.is_error
        assert not validation.structured_content["execution_authorized"]
        assert (await client.call_tool("get_contract")).structured_content["operations"]
        assert len((await client.call_tool("list_tests")).structured_content["scenarios"]) == 3
        args = {"scenario_id": "mini_normal", "invocation_id": uuid4().hex}
        run = await client.call_tool("run_scenario", args)
        assert not run.is_error
        report = run.structured_content["report"]
        assert report["outcome"] == "PASS"
        assert len(report["steps"]) == 8
        assert sum(len(step["attempts"]) for step in report["steps"]) == 9
        saved = await client.call_tool("read_report", {"run_id": report["run_id"]})
        assert saved.structured_content["report"] == report
        assert (await client.call_tool("run_scenario", args)).structured_content["cached"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("run_scenario", {"scenario_id": "courseinsight", "invocation_id": uuid4().hex}),
    ("run_scenario", {"scenario_id": "mini_normal", "invocation_id": uuid4().hex,
                      "url": "http://production.invalid"}),
    ("run_scenario", {"scenario_id": "mini_normal", "invocation_id": uuid4().hex,
                      "Authorization": "Bearer private-jwt"}),
    ("run_scenario", {"scenario_id": "mini_normal", "invocation_id": uuid4().hex,
                      "max_requests": 100000}),
    ("validate_plan", {"plan_json": {"api_key": "private-key"}}),
    ("validate_plan", {"plan_json": '{"reviewed":true,"source":"private-secret"}'}),
    ("read_report", {"run_id": "../../private-secret"}),
    ("get_contract", {"private-secret-field": "private-secret"}),
    ("list_tests", {"method": "DELETE"}),
])
async def test_sdk_denial_does_not_echo_sensitive_input(server, caplog, tool, args):
    async with Client(server) as client:
        result = await client.call_tool(tool, args)
        assert result.is_error
        assert not result.structured_content["ok"]
        serialized = result.model_dump_json()
        for private in ("private-jwt", "private-key", "private-secret", "production.invalid"):
            assert private not in serialized
            assert private not in caplog.text


@pytest.mark.asyncio
async def test_sdk_failed_business_assertion_is_valid_tool_result(server):
    async with Client(server) as client:
        result = await client.call_tool("run_scenario", {
            "scenario_id": "mini_ownership_fault", "invocation_id": uuid4().hex})
        assert not result.is_error  # Tool completed; the tested business behavior failed.
        assert result.structured_content["report"]["outcome"] == "ASSERTION_FAILED"


@pytest.mark.asyncio
async def test_sdk_operator_budget_cannot_be_increased(tmp_path):
    server = create_server(MCPService(tmp_path, MCPSettings(max_runs=0)))
    async with Client(server) as client:
        result = await client.call_tool("run_scenario", {
            "scenario_id": "mini_normal", "invocation_id": uuid4().hex})
        assert result.is_error
        assert result.structured_content["error_code"] == "MCP_RUN_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_sdk_unknown_tool_with_untrusted_name_is_redacted(server, caplog):
    # Disable client caching/preflight to send an unknown name through the protocol.
    async with Client(server, cache=None) as client:
        result = await client.session.call_tool("private-secret-tool", {"api_key": "private-key"})
        assert result.is_error
        assert "private-secret" not in json.dumps(result.model_dump(mode="json"))
        assert "private-secret" not in caplog.text


@pytest.mark.asyncio
async def test_sdk_rejected_calls_also_consume_tool_budget(tmp_path):
    server = create_server(MCPService(tmp_path, MCPSettings(max_tool_calls=1)))
    async with Client(server) as client:
        rejected = await client.call_tool("get_contract", {"api_key": "private-key"})
        assert rejected.is_error
        exhausted = await client.call_tool("get_contract")
        assert exhausted.structured_content["error_code"] == "MCP_TOOL_BUDGET_EXHAUSTED"
