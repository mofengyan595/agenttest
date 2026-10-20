"""Disposable reports and loopback API for reproducible browser acceptance."""

import asyncio
import json
import tempfile
from pathlib import Path

import uvicorn

from agenttest.main import create_app
from agenttest.report import ReportStore
from agenttest.showcase import showcase
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "runs" / "console-e2e"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="session-", dir=root) as directory:
        store = ReportStore(Path(directory))
        for case in ("normal", "fault", "investigation", "java-mock", *("normal",) * 9):
            result = asyncio.run(showcase(case, store))
            if not result["verified"]:
                raise RuntimeError("OFFLINE_FIXTURE_FAILED")
        # Real catalog edge cases in the disposable namespace only.
        for run_id, content in (("d" * 32, '{"invalid":'), ("e" * 32, None)):
            folder = store.root / run_id
            folder.mkdir()
            if content:
                (folder / "report.json").write_text(content, encoding="utf-8")
        legacy_path = Path(result["cases"][0]["report"])  # Last normal fixture.
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        for field in ("workflow_completed", "dsl_valid", "intent_verified", "task_satisfied"):
            legacy.pop(field, None)
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        from agenttest.portability_demo import EXAMPLES
        from agenttest.workflow import FakeLLM

        # Fixed untrusted reply for browser plumbing only, never real-model quality evidence.
        proposal = json.dumps(dict(name="browser fixture", steps=[dict(id="open",
            operation_id="openTicket", role="owner", policy_id="ticket_open_v1",
            body={"subject": {"value": "a"}, "priority": {"value": "low"}})]))
        tasks = TaskService({"tickets": EXAMPLES / "tickets/registered.project.json"},
            TaskStore(store.root / "tasks.sqlite3"), ReportStore(store.root / "task-reports"),
            lambda project: FakeLLM(proposal),
            synthetic=True, environ={})
        uvicorn.run(create_app(store.root, task_service=tasks), host="127.0.0.1", port=18000,
                    proxy_headers=False)


if __name__ == "__main__":
    main()
