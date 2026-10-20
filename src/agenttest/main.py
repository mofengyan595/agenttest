"""Local contract import and read-only run queries. Execution is operator-owned CLI."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request

from agenttest.contracts import MAX_DOCUMENT_BYTES, ContractSummary, load_openapi
from agenttest.local_access import DEFAULT_ORIGINS, LocalAccessMiddleware
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, ReportStore, RunReport
from agenttest.report_queries import CatalogLimit, RunPage, list_runs, public_report

MAX_CONCURRENT_IMPORTS = 2
_IMPORT_WORKERS = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_IMPORTS,
                                     thread_name_prefix="agenttest-contract")
_IMPORT_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_IMPORTS)


def create_app(runs_dir: Path = Path("runs"), *,
               public_policies: PolicyRegistry | None = None,
               allowed_origins: frozenset[str] = DEFAULT_ORIGINS, task_service=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        yield
        if task_service is not None:
            await task_service.close()

    app = FastAPI(title="AgentTest", version="0.1.0", lifespan=lifespan)
    app.add_middleware(LocalAccessMiddleware, allowed_origins=allowed_origins)
    registry = public_policies.snapshot() if public_policies else None
    store = ReportStore(runs_dir)
    from agenttest.task_api import install_task_api

    install_task_api(app, task_service)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/spec/summary", response_model=ContractSummary)
    async def spec_summary(request: Request) -> ContractSummary:
        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type not in {"application/json", "application/yaml", "text/yaml", "text/plain"}:
            raise HTTPException(415, "Use JSON or YAML")
        if not _IMPORT_SLOTS.acquire(blocking=False):
            raise HTTPException(429, "Too many contract imports", headers={"Retry-After": "1"})
        pending = None
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_DOCUMENT_BYTES:
                    raise HTTPException(413, "Contract exceeds size limit")
                raw.extend(chunk)
            pending = _IMPORT_WORKERS.submit(load_openapi, bytes(raw), unwrap=True)
            # A running parser cannot be cancelled. Its actual completion owns the slot,
            # even if the awaiting request is cancelled or its event loop closes.
            pending.add_done_callback(lambda completed: _IMPORT_SLOTS.release())
            return await asyncio.wrap_future(pending)
        except (ValueError, TypeError, RecursionError):
            # Never echo the untrusted document (which may contain credentials).
            raise HTTPException(422, "Invalid or unsupported OpenAPI contract") from None
        finally:
            if pending is None:
                _IMPORT_SLOTS.release()

    @app.get("/api/runs", response_model=RunPage)
    def run_list(limit: int = Query(20, ge=1, le=100),
                 offset: int = Query(0, ge=0, le=2000), outcome: Outcome | None = None,
                 order: Literal["asc", "desc"] = "desc") -> RunPage:
        try:
            return list_runs(store, limit=limit, offset=offset, outcome=outcome, order=order)
        except (CatalogLimit, OSError):
            raise HTTPException(503, "Report catalog unavailable or over budget") from None

    @app.get("/api/runs/{run_id}", response_model=RunReport, response_model_exclude_unset=True)
    def run_result(run_id: str) -> RunReport:
        try:
            return public_report(store.read(run_id), registry)
        except (ValueError, OSError):
            if task_service is not None and task_service.reports.root != store.root:
                try:
                    return public_report(task_service.reports.read(run_id), registry)
                except (ValueError, OSError):
                    pass
            raise HTTPException(404, "Run not found") from None

    return app


def configured_app() -> FastAPI:
    policy_path = os.environ.get("AGENTTEST_PUBLIC_POLICIES")
    registry = (PolicyRegistry.from_json(Path(policy_path).read_bytes()) if policy_path else None)
    runs_dir = Path(os.environ.get("AGENTTEST_RUNS_DIR", "runs"))
    task_service = None
    if os.environ.get("AGENTTEST_PROJECTS_FILE"):
        from agenttest.goal_server import configured_task_service

        task_service = configured_task_service(runs_dir)
    return create_app(runs_dir, public_policies=registry, task_service=task_service)


app = configured_app()
