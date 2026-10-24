"""Human experience entry: existing workbench, mock models, isolated in-memory targets."""

import argparse
from pathlib import Path
from uuid import uuid4

import uvicorn
from trusted_validation_support import PROFILES, mock_models

from agenttest.main import create_app
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.report import ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18010)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError("LOCAL_PORT_REQUIRED")
    output = Path(__file__).resolve().parents[1] / "runs/plan-reuse-demo" / uuid4().hex
    service = TaskService({key: PROFILES[key] / "registered.project.json"
        for key in ("tickets", "courses")}, TaskStore(output / "tasks.sqlite3"),
        ReportStore(output / "reports"), lambda project: CatalogFixturePlanner(),
        synthetic=True, environ={}, analyzer_factory=lambda project: mock_models()[1],
        semantic_factory=lambda project: mock_models()[2])
    print("SIMULATED models; in-memory targets; no real user review recorded.", flush=True)
    uvicorn.run(create_app(service.reports.root, task_service=service),
                host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
