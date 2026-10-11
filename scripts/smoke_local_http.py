"""Explicit opt-in loopback smoke; default pytest suite never opens network sockets."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

from agenttest.demo import PROJECT_ROOT, demo_inputs
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", action="store_true")
    args = parser.parse_args()
    _, config = demo_inputs()
    environ = {
        "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": secrets.token_urlsafe(24),
        "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": secrets.token_urlsafe(24),
    }
    target = create_target({"alice": environ["DEMO_ALICE_PASSWORD"],
                            "bob": environ["DEMO_BOB_PASSWORD"]})
    store = ReportStore(Path("runs"))
    with serve(target) as target_url, serve(create_app(store.root)) as platform_url:
        config = TargetConfig.model_validate({**config.model_dump(), "base_url": target_url})
        with tempfile.TemporaryDirectory(dir=store.root) as temporary:
            target_file = Path(temporary) / "target.json"
            target_file.write_text(config.model_dump_json(), encoding="utf-8")
            command = [
                sys.executable, "-m", "agenttest", "workflow" if args.workflow else "run",
                "--contract", str(PROJECT_ROOT / "examples/demo-openapi.json"),
                "--policies", str(PROJECT_ROOT / "examples/demo-policies.json"),
                "--plan", str(PROJECT_ROOT / "examples/demo-scenario.json"),
                "--target", str(target_file), "--runs-dir", str(store.root),
            ]
            completed = subprocess.run(
                command, env={**os.environ, **environ}, capture_output=True, text=True, timeout=70,
            )
            assert completed.returncode == 0, "Local CLI execution failed"
            report = store.read(json.loads(completed.stdout)["run_id"])
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
            "workflow_nodes": report.workflow_nodes,
        }
    print(json.dumps(summary_result))


if __name__ == "__main__":
    main()
