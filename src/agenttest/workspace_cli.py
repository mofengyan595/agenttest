"""Headless commands for the normal workspace; no second execution engine."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from agenttest.workspace_client import WorkspaceClient, task_exit_code


def add_parser(subcommands):
    workspace = subcommands.add_parser("workspace", help="操作网页工作区的通用已注册项目")
    workspace.add_argument("--server", default="http://127.0.0.1:18120")
    workspace.add_argument("--session-file", type=Path,
                           default=Path("runs/workspace-client/.env"))
    commands = workspace.add_subparsers(dest="workspace_action", required=True)
    for name in ("projects", "saved", "status", "evidence", "submit", "save", "regression",
                 "cancel", "start"):
        command = commands.add_parser(name)
        if name in {"status", "evidence", "save", "regression", "cancel", "start"}:
            command.add_argument("--task-id", required=True)
        if name in {"evidence", "save", "regression"}:
            command.add_argument("--run-id", required=True)
        if name in {"submit", "regression"}:
            command.add_argument("--submission-id", default=None)
            command.add_argument("--wait-seconds", type=int, default=610)
        if name == "submit":
            command.add_argument("--project-id", required=True)
            command.add_argument("--user-task", required=True)
            command.add_argument("--real", action="store_true", required=True)
        if name == "regression":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--review-approved", action="store_true")
        if name == "start":
            command.add_argument("--plan-version", type=int, required=True)
            command.add_argument("--review-approved", action="store_true", required=True)
    return workspace


async def execute(args, client=None):
    client = client or WorkspaceClient(args.server, args.session_file,
        allow_model=getattr(args, "real", False),
        allow_reviewed_start=getattr(args, "review_approved", False))
    action = args.workspace_action
    name = dict(projects="list_projects", saved="list_saved_tests", status="task_status",
                evidence="read_evidence", submit="submit_test", save="save_test",
                regression="run_saved_test", cancel="cancel_test", start="start_test")[action]
    data = {key: getattr(args, key) for key in ("task_id", "run_id", "project_id", "user_task",
                                             "plan_version", "review_approved")
            if hasattr(args, key)}
    if action == "regression":
        data.pop("review_approved", None)
    if action in {"submit", "regression"}:
        if not 1 <= args.wait_seconds <= 900:
            raise ValueError("WAIT_LIMIT_INVALID")
        if action == "regression" and args.output.exists():
            raise ValueError("REPORT_OUTPUT_EXISTS")
        data["submission_id"] = args.submission_id or uuid4().hex
        # Print only the replay key BEFORE dispatch, so an unknown response is traceable.
        print(json.dumps(dict(submission_id=data["submission_id"])), flush=True)
    result = await client.call(name, data)
    if (result["ok"] and action == "regression" and getattr(args, "review_approved", False)
            and result["data"]["status"] == "WAITING_REVIEW"):
        result = await client.call("start_test", dict(task_id=result["data"]["id"],
            plan_version=result["data"]["plan_version"], review_approved=True))
    if result["ok"] and action in {"submit", "regression"}:
        task_id = result["data"]["id"]
        print(json.dumps(dict(task_id=task_id)), flush=True)
        try:
            result = await client.wait(task_id, args.wait_seconds)
        except asyncio.CancelledError:
            await client.call("cancel_test", dict(task_id=task_id))
            raise
        except TimeoutError:
            # Waiting timeout does not mean the task failed; never submit again automatically.
            result = dict(ok=False, error="WAIT_TIMEOUT_QUERY_STATUS", task_id=task_id)
    if action == "regression":
        reports = []
        if result["ok"] and result["data"]["summary"].get("run_id"):
            reports.append(await client.call("read_evidence", dict(task_id=result["data"]["id"],
                run_id=result["data"]["summary"]["run_id"])))
        artifact = dict(task=result, reports=reports, model_source="DISABLED")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as destination:
            json.dump(artifact, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if action == "regression" and any(not r["ok"] for r in reports):
        return 2
    return task_exit_code(result["data"]) if result["ok"] and action in {
        "submit", "regression", "status"} else 0 if result["ok"] else 2


def command(args):
    try:
        return asyncio.run(execute(args))
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError):
        print(json.dumps(dict(ok=False, error="WORKSPACE_CONFIGURATION_OR_OUTPUT_INVALID")))
        return 2
