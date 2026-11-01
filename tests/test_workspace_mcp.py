"""Official MCP v2 protocol and strict facade argument/privacy boundaries."""

import asyncio
import sys
from uuid import uuid4

import pytest

pytest.importorskip("mcp")
from mcp import Client, StdioServerParameters  # noqa: E402

from agenttest.workspace_client import REQUESTS  # noqa: E402
from agenttest.workspace_mcp import create_server  # noqa: E402


class ClientFixture:
    def __init__(self):
        self.calls = []

    async def call(self, name, arguments=None):
        data = REQUESTS[name].model_validate(arguments or {}).model_dump()
        self.calls.append((name, data))
        return dict(ok=True, data=dict(action=name, model_source="DISABLED"))


@pytest.mark.asyncio
async def test_sdk_tools_forward_all_common_actions_to_the_same_client():
    facade = ClientFixture()
    task, run, submission = uuid4().hex, uuid4().hex, uuid4().hex
    inputs = dict(list_projects={}, list_saved_tests={},
        submit_test=dict(project_id="web_project", user_task="安排测试", submission_id=submission),
        task_status=dict(task_id=task), read_evidence=dict(task_id=task, run_id=run),
        save_test=dict(task_id=task, run_id=run),
        run_saved_test=dict(task_id=task, run_id=run, submission_id=submission),
        cancel_test=dict(task_id=task),
        start_test=dict(task_id=task, plan_version=1, review_approved=True))
    async with Client(create_server(facade)) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == set(REQUESTS)
        assert all(t.input_schema["additionalProperties"] is False for t in tools)
        for name, args in inputs.items():
            result = await client.call_tool(name, args)
            assert not result.is_error, result
            assert result.structured_content["data"]["action"] == name
    assert len(facade.calls) == len(REQUESTS)


@pytest.mark.asyncio
async def test_sdk_rejects_permission_expansion_without_echoing_credentials(caplog):
    facade = ClientFixture()
    async with Client(create_server(facade, max_tool_calls=3), cache=None) as client:
        for name, args in [("list_projects", dict(api_key="private-credential")),
                           ("run_saved_test", dict(task_id=uuid4().hex, run_id=uuid4().hex,
                            submission_id=uuid4().hex, target="http://production.invalid")),
                           ("private-credential", dict(secret="private-credential"))]:
            result = await client.session.call_tool(name, args)
            assert result.is_error
            assert "private-credential" not in result.model_dump_json()
            assert "private-credential" not in caplog.text
        exhausted = await client.call_tool("list_projects")
        assert exhausted.structured_content["error"] == "MCP_TOOL_BUDGET_EXHAUSTED"
    assert not facade.calls


@pytest.mark.asyncio
async def test_actual_sdk_stdio_process_advertises_workspace_tools_without_model_or_network(tmp_path):
    parameters = StdioServerParameters(command=sys.executable, args=[
        "-m", "agenttest.workspace_mcp", "--session-file", str(tmp_path / "session.json")])
    async with asyncio.timeout(45):
        async with Client(parameters) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == set(REQUESTS)
            rejected = await client.call_tool("task_status", dict(task_id="private-credential"))
            assert rejected.is_error and "private-credential" not in rejected.model_dump_json()
    assert not (tmp_path / "session.json").exists()
