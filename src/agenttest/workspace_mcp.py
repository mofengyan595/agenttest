"""MCP SDK v2 tools proxy the normal local workspace API, never a second executor."""

import argparse
import json
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.context import CallNext, ServerRequestContext
from mcp.server.mcpserver.tools import Tool
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from agenttest.workspace_client import MUTATIONS, REQUESTS, WorkspaceClient


def result(payload):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(
        payload, ensure_ascii=False))], structured_content=payload, is_error=not payload["ok"])


def create_server(client, *, max_tool_calls=128):
    async def list_projects() -> CallToolResult:
        """查看已注册项目、可测试范围和待补充条件；不授予新权限。"""
        return result(await client.call("list_projects"))

    async def submit_test(project_id: str, user_task: str,
                          submission_id: str) -> CallToolResult:
        """安排自然语言测试。相同提交ID不会重放；返回任务ID后查询进度。"""
        return result(await client.call("submit_test", dict(project_id=project_id,
            user_task=user_task, submission_id=submission_id)))

    async def task_status(task_id: str) -> CallToolResult:
        """读取本会话拥有的任务状态、结果与保存条件。"""
        return result(await client.call("task_status", dict(task_id=task_id)))

    async def read_evidence(task_id: str, run_id: str) -> CallToolResult:
        """读取该任务的脱敏原始报告、调查、资源归属与清理记录。"""
        return result(await client.call("read_evidence", dict(task_id=task_id, run_id=run_id)))

    async def save_test(task_id: str, run_id: str) -> CallToolResult:
        """保存完整且通过检查的只读或写生命周期场景；服务端重新检查版本。"""
        return result(await client.call("save_test", dict(task_id=task_id, run_id=run_id)))

    async def list_saved_tests() -> CallToolResult:
        """查看本会话已保存测试的可运行状态和过期原因。"""
        return result(await client.call("list_saved_tests"))

    async def run_saved_test(task_id: str, run_id: str, submission_id: str) -> CallToolResult:
        """禁用模型固定复测；每次新数据，过期配置被阻止，不恢复原请求。"""
        return result(await client.call("run_saved_test", dict(task_id=task_id, run_id=run_id,
                                                              submission_id=submission_id)))

    async def cancel_test(task_id: str) -> CallToolResult:
        """停止本会话任务的后续动作；不回滚、不重放写入。"""
        return result(await client.call("cancel_test", dict(task_id=task_id)))

    async def start_test(task_id: str, plan_version: int, review_approved: bool) -> CallToolResult:
        """只在操作员已审阅并明确批准指定版本后开始；还需启动时允许此能力。"""
        return result(await client.call("start_test", dict(task_id=task_id,
            plan_version=plan_version, review_approved=review_approved)))

    calls = 0

    async def guard(ctx: ServerRequestContext, call_next: CallNext):
        nonlocal calls
        if ctx.method == "tools/call":
            calls += 1
            if calls > max_tool_calls:
                return result(dict(ok=False, error="MCP_TOOL_BUDGET_EXHAUSTED"))
            try:
                params = ctx.params or {}
                name = params.get("name")
                if (set(params) - {"name", "arguments", "_meta"} or name not in REQUESTS
                        or len(json.dumps(params.get("arguments", {})).encode()) > 12_000):
                    raise ValueError("ARGUMENTS_INVALID")
                REQUESTS[name].model_validate(params.get("arguments", {}))
            except (ValueError, TypeError, RecursionError):
                # Reject before SDK validation/logging can echo untrusted argument values.
                return result(dict(ok=False, error="MCP_ARGUMENTS_REJECTED"))
        return await call_next(ctx)

    tools = []
    for fn in (list_projects, submit_test, task_status, read_evidence, save_test,
               list_saved_tests, run_saved_test, cancel_test, start_test):
        mutation = fn.__name__ in MUTATIONS
        tool = Tool.from_function(fn, structured_output=False, annotations=ToolAnnotations(
            read_only_hint=not mutation, open_world_hint=False, destructive_hint=mutation,
            idempotent_hint=fn.__name__ in {"submit_test", "run_saved_test"}))
        tool.parameters = REQUESTS[fn.__name__].model_json_schema()
        tools.append(tool)
    return MCPServer("AgentTest Workspace", tools=tools, middleware=[guard], log_level="WARNING",
        instructions="仅操作已注册项目，权限/预算/版本由同一TaskService检查。"
        "不接受目标地址、凭据、断言、预算或文件路径。失败和未知结果不自动重试。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:18120")
    parser.add_argument("--session-file", type=Path,
                        default=Path("runs/workspace-client/session.json"))
    parser.add_argument("--allow-real-model", action="store_true")
    parser.add_argument("--allow-reviewed-start", action="store_true")
    parser.add_argument("--max-tool-calls", type=int, default=128)
    args = parser.parse_args()
    if not 1 <= args.max_tool_calls <= 1000:
        parser.error("Tool budget must be between 1 and 1000")
    client = WorkspaceClient(args.server, args.session_file, allow_model=args.allow_real_model,
                              allow_reviewed_start=args.allow_reviewed_start)
    create_server(client, max_tool_calls=args.max_tool_calls).run(transport="stdio")


if __name__ == "__main__":
    main()
