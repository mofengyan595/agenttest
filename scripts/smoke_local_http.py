"""Explicit opt-in loopback smoke; default pytest suite never opens network sockets."""

import asyncio
import json
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

from agenttest.contracts import summarize_openapi
from agenttest.demo import demo_inputs
from agenttest.executor import Executor
from agenttest.main import create_app
from agenttest.mini_target import create_target
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig


@contextmanager
def serve(app: FastAPI):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local server startup failed")
                time.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Local server shutdown failed")


def main() -> None:
    scenario, config = demo_inputs()
    environ = {
        "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": secrets.token_urlsafe(24),
        "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": secrets.token_urlsafe(24),
    }
    target = create_target({"alice": environ["DEMO_ALICE_PASSWORD"],
                            "bob": environ["DEMO_BOB_PASSWORD"]})
    store = ReportStore(Path("runs"))
    with serve(target) as target_url, serve(create_app(store.root)) as platform_url:
        config = TargetConfig.model_validate({**config.model_dump(), "base_url": target_url})
        report = asyncio.run(Executor(
            config, summarize_openapi(target.openapi()), store, environ=environ,
        ).run(scenario))
        with httpx.Client(trust_env=False, timeout=5.0) as client:
            health = client.get(platform_url + "/health")
            summary = client.post(platform_url + "/api/spec/summary", json=target.openapi())
            query = client.get(platform_url + "/api/runs/" + report.run_id)
        assert report.outcome == Outcome.PASS, report.model_dump_json()
        assert health.status_code == summary.status_code == query.status_code == 200
        assert query.json()["run_id"] == report.run_id
        summary_result = {
            "outcome": report.outcome.value, "steps": len(report.steps),
            "requests": sum(len(step.attempts) for step in report.steps),
            "health": health.status_code, "spec_import": summary.status_code,
            "report_query": query.status_code, "run_id": report.run_id,
            "servers_stopped_on_exit": True,
        }
    print(json.dumps(summary_result))


if __name__ == "__main__":
    main()
