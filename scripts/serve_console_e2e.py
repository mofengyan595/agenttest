"""Disposable reports and loopback API for reproducible browser acceptance."""

import asyncio
import tempfile
from pathlib import Path

import uvicorn

from agenttest.main import create_app
from agenttest.report import ReportStore
from agenttest.showcase import showcase


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "runs" / "console-e2e"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="session-", dir=root) as directory:
        store = ReportStore(Path(directory))
        for case in ("normal", "fault", "investigation", "java-mock"):
            result = asyncio.run(showcase(case, store))
            if not result["verified"]:
                raise RuntimeError("OFFLINE_FIXTURE_FAILED")
        uvicorn.run(create_app(store.root), host="127.0.0.1", port=18000)


if __name__ == "__main__":
    main()
