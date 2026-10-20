"""Official SDK v2 stdio entry. Protocol tools wrap the existing executor facade."""

import argparse
import json
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.context import CallNext, ServerRequestContext
from mcp.server.mcpserver.tools import Tool
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError

from agenttest.demo import PROJECT_ROOT
from agenttest.mcp_service import REQUESTS, MCPService, MCPSettings
from agenttest.tool_registry import TOOLS


def result(payload: dict) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))],
                          structured_content=payload, is_error=not payload["ok"])


def create_server(service: MCPService) -> MCPServer:
    async def list_tests() -> CallToolResult:
        """查询操作员批准的独立 mini target 场景、操作和预算。"""
        return result(await service.call("list_tests"))

    async def get_contract() -> CallToolResult:
        """查看筛选后的公共契约、角色别名、可信策略范围及 DSL Schema。"""
        return result(await service.call("get_contract"))

    async def validate_plan(plan_json: str) -> CallToolResult:
        """只校验不可信 DSL；不发送业务请求，不授予计划执行权限。"""
        return result(await service.call("validate_plan", {"plan_json": plan_json}))

    async def run_scenario(scenario_id: str, invocation_id: str) -> CallToolResult:
        """显式运行批准场景。32 位小写 UUID 防重放；同 ID 不重新执行 HTTP。"""
        return result(await service.call("run_scenario", {
            "scenario_id": scenario_id, "invocation_id": invocation_id}))

    async def read_report(run_id: str) -> CallToolResult:
        """按 run_id 读取本 MCP 目录内已完成的脱敏报告；不能恢复执行。"""
        return result(await service.call("read_report", {"run_id": run_id}))

    async def guard(ctx: ServerRequestContext, call_next: CallNext):
        # SDK argument errors include Pydantic input values. Reject here BEFORE its
        # validator/logger; the same strict schemas are advertised in tools/list.
        if ctx.method == "tools/call":
            try:
                params = ctx.params or {}
                if (set(params) - {"name", "arguments", "_meta"}
                        or type(params.get("name")) is not str):
                    return result(service.reject("MCP_ARGUMENTS_INVALID"))
                name = params["name"]
                if name not in REQUESTS:
                    return result(service.reject("MCP_TOOL_NOT_AUTHORIZED"))
                arguments = params.get("arguments", {})
                if len(json.dumps(arguments).encode()) > 131_072:
                    return result(service.reject("MCP_INPUT_TOO_LARGE"))
                REQUESTS[name].model_validate(arguments)
            except (ValidationError, ValueError, TypeError, RecursionError):
                return result(service.reject("MCP_ARGUMENTS_INVALID"))
        return await call_next(ctx)

    tools = []
    for fn in (list_tests, get_contract, validate_plan, run_scenario, read_report):
        mutates = TOOLS[fn.__name__].permission == "isolated_execution"
        tool = Tool.from_function(fn, structured_output=False, annotations=ToolAnnotations(
            read_only_hint=not mutates, open_world_hint=False,
            destructive_hint=False, idempotent_hint=mutates))
        tool.parameters = REQUESTS[fn.__name__].model_json_schema()
        tools.append(tool)
    return MCPServer("AgentTest", version="0.1.0", tools=tools, middleware=[guard],
                     log_level="WARNING", instructions=(
                         "仅限独立 mini target；验证通过不等于授权执行。"
                         "禁止提供目标、凭据、断言或扩大预算。失败/中断不自动重试。"))


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentTest MCP stdio（独立 mini target）")
    parser.add_argument("--runs-dir", type=Path, default=PROJECT_ROOT / "runs/mcp")
    parser.add_argument("--max-runs", type=int, default=3)
    parser.add_argument("--max-tool-calls", type=int, default=100)
    args = parser.parse_args()
    settings = MCPSettings(max_runs=args.max_runs, max_tool_calls=args.max_tool_calls)
    create_server(MCPService(args.runs_dir, settings)).run(transport="stdio")


if __name__ == "__main__":
    main()
