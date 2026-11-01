"""Disposable reports and loopback API for reproducible browser acceptance."""

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn

from agenttest.local_access import DEFAULT_ORIGINS
from agenttest.main import create_app
from agenttest.report import ReportStore
from agenttest.showcase import showcase
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "runs" / "console-e2e"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="session-", dir=root) as directory:
        store = ReportStore(Path(directory))
        normal = None
        for case in ("normal", "fault", "investigation", "java-mock"):
            result = asyncio.run(showcase(case, store))
            if not result["verified"]:
                raise RuntimeError("OFFLINE_FIXTURE_FAILED")
            if case == "normal":
                normal = store.read(result["cases"][0]["run_id"])
        # Extra rows exercise pagination, not additional execution scenarios.
        for _ in range(9):
            duplicate = normal.model_copy(deep=True, update={"run_id": uuid4().hex, "revision": 0})
            legacy_path = store.save(duplicate)
        # Real catalog edge cases in the disposable namespace only.
        for run_id, content in (("d" * 32, '{"invalid":'), ("e" * 32, None)):
            folder = store.root / run_id
            folder.mkdir()
            if content:
                (folder / "report.json").write_text(content, encoding="utf-8")
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        for field in ("workflow_completed", "dsl_valid", "intent_verified", "task_satisfied"):
            legacy.pop(field, None)
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        from agenttest.nl_entry_fixture import CatalogFixturePlanner

        profiles = Path(__file__).resolve().parents[1] / "examples/trusted-testing/projects"
        review = store.root / "review-profile"
        shutil.copytree(profiles / "tickets", review)
        registration = json.loads((review / "registered.project.json").read_bytes())
        registration.update(id="review_tickets", require_plan_review=True)
        (review / "registered.project.json").write_text(json.dumps(registration), encoding="utf-8")
        goals = json.loads((review / "goals.json").read_bytes())
        goals["resource_aliases"] = ["审阅工单", "工单"]
        (review / "goals.json").write_text(json.dumps(goals, ensure_ascii=False), encoding="utf-8")
        projects = {key: profiles / key / "registered.project.json"
                    for key in ("tickets", "courses")}
        projects["review_tickets"] = review / "registered.project.json"
        limited = store.root / "limited-profile"
        shutil.copytree(profiles / "tickets_limited", limited)
        registration = json.loads((limited / "registered.project.json").read_bytes())
        registration["id"] = "limited_tickets"
        (limited / "registered.project.json").write_text(json.dumps(registration), encoding="utf-8")
        goals = json.loads((limited / "goals.json").read_bytes())
        goals["resource_aliases"] = ["限额工单", "工单"]
        (limited / "goals.json").write_text(json.dumps(goals, ensure_ascii=False), encoding="utf-8")
        projects["limited_tickets"] = limited / "registered.project.json"
        tasks = TaskService(projects,
            TaskStore(store.root / "tasks.sqlite3"), ReportStore(store.root / "task-reports"),
            lambda project: CatalogFixturePlanner(),
            synthetic=True, environ={}, model_status=lambda: "SIMULATED")

        async def error_reports():
            from agenttest.goal_cli import synthetic_runtime
            from agenttest.goal_runtime import run_goal
            from agenttest.workflow import FakeLLM

            project = tasks.project("tickets")
            env, _ = synthetic_runtime(project)

            def timeout(request):
                raise httpx.ReadTimeout("Synthetic timeout", request=request)

            result = {}
            for name, planner, transport in (
                    ("infra", CatalogFixturePlanner(), httpx.MockTransport(timeout)),
                    ("tool", FakeLLM("{"), httpx.MockTransport(timeout))):
                _, report = await run_goal(project, "创建工单", planner, tasks.reports,
                                           environ=env, transport=transport)
                result[name] = report.run_id
            return result

        fixtures = asyncio.run(error_reports())
        web_port = int(os.environ.get("CONSOLE_WEB_PORT", "5173"))
        origins = DEFAULT_ORIGINS | frozenset({f"http://127.0.0.1:{web_port}"})
        app = create_app(store.root, task_service=tasks, allowed_origins=origins)

        @app.get("/api/acceptance-fixtures")
        def acceptance_fixtures():
            """Test harness only: IDs of actual offline error reports, never product API."""
            return fixtures

        uvicorn.run(app,
                    host="127.0.0.1", port=args.port,
                    proxy_headers=False)


if __name__ == "__main__":
    main()
