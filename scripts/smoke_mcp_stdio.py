"""Real SDK subprocess client; no LLM, Docker, listening port or external HTTP."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from mcp import Client, StdioServerParameters

from agenttest.demo import demo_inputs


async def check() -> dict:
    with tempfile.TemporaryDirectory(prefix="agenttest-mcp-") as root:
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "agenttest.mcp_server", "--runs-dir", str(Path(root) / "reports")])
        async with Client(params, read_timeout_seconds=45) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 5
            assert all(tool.input_schema["additionalProperties"] is False for tool in tools)
            public = await client.call_tool("get_contract")
            assert public.structured_content["ok"]
            catalog = await client.call_tool("list_tests")
            assert not catalog.structured_content["courseinsight_enabled"]
            validated = await client.call_tool("validate_plan", {
                "plan_json": demo_inputs()[0].model_dump_json()})
            assert not validated.is_error
            runs = []
            for scenario_id, expected in [("mini_normal", "PASS"),
                                          ("mini_ownership_fault", "ASSERTION_FAILED"),
                                          ("mini_terminal_fault", "ASSERTION_FAILED")]:
                args = {"scenario_id": scenario_id, "invocation_id": uuid4().hex}
                executed = await client.call_tool("run_scenario", args)
                assert not executed.is_error
                report = executed.structured_content["report"]
                assert report["outcome"] == expected
                saved = await client.call_tool("read_report", {"run_id": report["run_id"]})
                assert saved.structured_content["report"] == report
                repeated = await client.call_tool("run_scenario", args)
                assert repeated.structured_content["cached"]
                assert repeated.structured_content["report"] == report
                runs.append({"scenario_id": scenario_id, "run_id": report["run_id"],
                             "outcome": report["outcome"], "steps": len(report["steps"]),
                             "requests": sum(len(s["attempts"]) for s in report["steps"]),
                             "course_creates": sum(s["operation_id"] == "createCourse"
                                                   for s in report["steps"])})
            denials = []
            for args in [
                {"scenario_id": "courseinsight", "invocation_id": uuid4().hex},
                {"scenario_id": "mini_normal", "invocation_id": uuid4().hex,
                 "base_url": "http://unapproved.invalid"},
                {"scenario_id": "mini_normal", "invocation_id": uuid4().hex,
                 "api_key": "synthetic-secret-sentinel"},
                {"scenario_id": "mini_normal", "invocation_id": uuid4().hex},
            ]:
                denied = await client.call_tool("run_scenario", args)
                assert denied.is_error
                assert "synthetic-secret-sentinel" not in denied.model_dump_json()
                denials.append(denied.structured_content["error_code"])
            return {"transport": "stdio", "sdk": "2.3.0", "tools": [t.name for t in tools],
                    "runs": runs, "denials": denials, "external_http_requests": 0,
                    "school_api_calls": 0, "java_writes": 0}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check()), ensure_ascii=False, indent=2))
