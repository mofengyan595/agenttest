"""Persistent product workspace; simulation requires the explicit --demo option."""

import argparse
import os
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agenttest.feedback import FakeFeedback
from agenttest.local_access import DEFAULT_ORIGINS
from agenttest.main import create_app
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.report import ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


def workspace(root: Path, *, demo=False, projects_file=None, model_env=None):
    repository = Path(__file__).resolve().parents[1]
    if not demo:
        from agenttest.goal_server import configured_task_service
        from agenttest.task_service import load_projects

        registry = projects_file or os.environ.get("AGENTTEST_PROJECTS_FILE")
        default_env = repository / ".env"
        return configured_task_service(root,
            projects=load_projects(Path(registry)) if registry else {},
            model_env=model_env or (default_env if default_env.is_file() else None),
            generic_intake=True)
    profiles = {key: repository / "examples/trusted-testing/projects" / key
                / "registered.project.json" for key in ("tickets", "courses")}
    return TaskService(profiles, TaskStore(root / "tasks.sqlite3"),
        ReportStore(root / "reports"), lambda project: CatalogFixturePlanner(),
        synthetic=True, environ={}, analyzer_factory=lambda project: FakeFeedback(),
        expose_registered=False, model_status=lambda: "SIMULATED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18012)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--demo", action="store_true", help="Explicit offline simulation")
    parser.add_argument("--projects", type=Path, help="Optional server-owned project registry")
    parser.add_argument("--model-env", type=Path, help="Server-owned model settings file")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError("LOCAL_PORT_REQUIRED")
    data = args.data or Path("runs/web-workspace-demo" if args.demo else "runs/product-workspace")
    service = workspace(data.absolute(), demo=args.demo,
                        projects_file=args.projects, model_env=args.model_env)
    origins = DEFAULT_ORIGINS | frozenset({f"http://127.0.0.1:{args.port}",
                                          f"http://localhost:{args.port}"})
    app = create_app(service.reports.root, task_service=service, allowed_origins=origins)
    web = Path(__file__).resolve().parents[1] / "web/dist"
    if web.is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/{page:path}")
        def frontend(page: str):
            # Browser input never selects a filesystem path.
            if page.startswith("api/"):
                raise HTTPException(404, "API not found")
            return FileResponse(web / "index.html", headers={"Cache-Control": "no-store"})

    print("模拟模型 / 内存模拟目标" if args.demo else
          "真实模型 / 授权测试环境；未配置模型时阻止执行，不回退模拟。", flush=True)
    print("规划配置状态：" + service.model_status(), flush=True)
    print(f"打开 http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
