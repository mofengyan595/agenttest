"""One offline demonstration entry; every business request uses the existing executor."""

import json
import sys
from uuid import uuid4

import httpx

from agenttest.courseinsight_nl import DEFAULT_TASK, approved_inputs, run
from agenttest.demo import demo_inputs, run_demo
from agenttest.report import Outcome, ReportStore


def summary(report) -> dict:
    return {"run_id": report.run_id, "outcome": report.outcome.value,
            "steps": len(report.steps),
            "requests": sum(len(s.attempts) for s in report.steps),
            "course_creates": sum(len(s.attempts) for s in report.steps
                                  if s.operation_id in {"createCourse", "ciCreateCourse"}),
            "checks": sum(len(s.checks) for s in report.steps),
            "investigations": len(report.investigations)}


async def java_mock(store: ReportStore):
    from agenttest.workflow import FakeLLM

    bundle = approved_inputs(DEFAULT_TASK)
    body = {}

    def respond(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"code": 0, "data": {
                "accessToken": "synthetic-mock-only", "tokenType": "Bearer", "role": "ADMIN"}})
        if request.method == "POST":
            body.update(json.loads(request.content))
            return httpx.Response(201, json={"code": 0, "data": 71})
        return httpx.Response(200, json={"code": 0, "data": {"id": 71, **body}})

    return await run(DEFAULT_TASK, FakeLLM(bundle[0].model_dump_json()), store, bundle=bundle,
                     environ={"COURSEINSIGHT_DEMO_USERNAME": "mock-admin",
                              "COURSEINSIGHT_DEMO_PASSWORD": "mock-password"},
                     transport=httpx.MockTransport(respond), authorized=True)


async def mcp_demo(store: ReportStore) -> dict:
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "agenttest.mcp_server", "--runs-dir", str(store.root / "mcp")])
    async with Client(params, read_timeout_seconds=45) as client:
        tools = (await client.list_tools()).tools
        catalog = await client.call_tool("list_tests")
        contract = await client.call_tool("get_contract")
        validated = await client.call_tool("validate_plan", {
            "plan_json": demo_inputs()[0].model_dump_json()})
        arguments = {"scenario_id": "mini_normal", "invocation_id": uuid4().hex}
        result = await client.call_tool("run_scenario", arguments)
        report = result.structured_content["report"]
        read = await client.call_tool("read_report", {"run_id": report["run_id"]})
        repeated = await client.call_tool("run_scenario", arguments)
        denied = await client.call_tool("run_scenario", {
            "scenario_id": "courseinsight", "invocation_id": uuid4().hex})
        ok = (len(tools) == 5 and catalog.structured_content["ok"]
              and contract.structured_content["ok"] and not validated.is_error
              and read.structured_content["report"] == report
              and repeated.structured_content["cached"] and denied.is_error
              and report["outcome"] == "PASS")
        return {"case": "mcp", "verified": ok, "transport": "stdio",
                "tools": [t.name for t in tools], "run_id": report["run_id"],
                "outcome": report["outcome"], "requests": sum(len(s["attempts"])
                                                              for s in report["steps"]),
                "cached_without_replay": repeated.structured_content["cached"],
                "java_denial": denied.structured_content["error_code"],
                "reports_root": str(store.root / "mcp")}


async def showcase(case: str, store: ReportStore) -> dict:
    cases = ["normal", "fault", "investigation", "java-mock", "mcp"] if case == "all" else [case]
    rows = []
    for selected in cases:
        if selected == "mcp":
            rows.append(await mcp_demo(store))
            continue
        if selected == "normal":
            report = await run_demo("none", store)
        elif selected == "fault":
            report = await run_demo("response_name", store)
        elif selected == "investigation":
            from agenttest.investigation_demo import run_investigation_demo

            report = await run_investigation_demo(store)
        elif selected == "java-mock":
            report = await java_mock(store)
        else:
            raise ValueError("Unknown showcase")
        expected = Outcome.ASSERTION_FAILED if selected in {"fault", "investigation"} \
            else Outcome.PASS
        ok = (report.outcome == expected and summary(report)["course_creates"] == 1)
        if selected == "investigation":
            ok &= len(report.investigations) == 1
        rows.append({"case": selected, "expected": expected.value, "verified": ok,
                     **summary(report), "report": str(store.root / report.run_id / "report.json")})
    return {"verified": all(row["verified"] for row in rows), "cases": rows,
            "school_api_calls": 0, "real_java_requests": 0}
