"""Explicit operator-only Java demo acceptance; never invoked by default pytest or CI."""

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx

from agenttest.courseinsight import execute, inputs
from agenttest.courseinsight_env import PreflightError, preflight
from agenttest.report import Outcome, ReportStore, fingerprint

ORIGIN = "http://127.0.0.1:18080"
JAVA_ARGS = [
    "--spring.profiles.active=demo", "--server.address=127.0.0.1", "--server.port=18080",
    "--spring.config.location=classpath:/application.yaml,classpath:/application-demo.yaml",
    "--spring.datasource.url=jdbc:mysql://127.0.0.1:3307/courseinsight_demo"
    "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=Asia/Shanghai",
    "--spring.data.redis.host=127.0.0.1", "--spring.data.redis.port=6380",
    "--rocketmq.name-server=127.0.0.1:9876",
    "--courseinsight.ai-service.base-url=http://127.0.0.1:8002",
    "--spring.docker.compose.enabled=false", "--courseinsight.outbox.enabled=false",
    "--courseinsight.analysis.lease-recovery-enabled=false",
]


def launch(root: Path, settings: dict) -> subprocess.Popen:
    """Own the process and pin its effective configuration; never adopt an unknown listener."""
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", 18080))
        except OSError:
            raise PreflightError("TARGET_PORT_OCCUPIED") from None
    # Exclude inherited Spring/Java/Maven overrides, model keys, proxies and business credentials.
    allowed = {"PATH", "JAVA_HOME", "USERPROFILE", "HOME", "SYSTEMROOT", "WINDIR", "COMSPEC",
               "PATHEXT", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(settings)
    wrapper = str(root / "courseinsight-server" / ("mvnw.cmd" if os.name == "nt" else "mvnw"))
    args = [wrapper, "-o", "spring-boot:run", "-Dspring-boot.run.profiles=demo",
            "-Dspring-boot.run.arguments=" + " ".join(JAVA_ARGS)]
    # Maven may generate ignored target/ files, but no source or tracked file is changed.
    return subprocess.Popen(
        args, cwd=root / "courseinsight-server", env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Terminate only the process tree this invocation owns, never all Java processes.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        raise PreflightError("OWNED_PROCESS_STOP_TIMEOUT") from None


async def wait_health(process: subprocess.Popen) -> int:
    # Infrastructure readiness only. All three business requests go through Executor.
    try:
        async with asyncio.timeout(120), httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=2.0,
        ) as client:
            for attempt in range(1, 61):
                if process.poll() is not None:
                    raise PreflightError("JAVA_START_FAILED")
                try:
                    async with client.stream("GET", ORIGIN + "/actuator/health") as response:
                        if 300 <= response.status_code < 400:
                            raise PreflightError("JAVA_HEALTH_REDIRECT_BLOCKED")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > 65536:
                                raise PreflightError("JAVA_HEALTH_BODY_LIMIT")
                            body.extend(chunk)
                        if response.status_code == 200:
                            data = json.loads(body)
                            if not isinstance(data, dict):
                                raise PreflightError("JAVA_HEALTH_INVALID_RESPONSE")
                            if data.get("status") == "UP":
                                return attempt
                except (httpx.TransportError, ValueError) as error:
                    if isinstance(error, PreflightError):
                        raise
                await asyncio.sleep(2)
    except TimeoutError:
        raise PreflightError("JAVA_HEALTH_TIMEOUT") from None
    raise PreflightError("JAVA_HEALTH_TIMEOUT")


async def live(root: Path, store: ReportStore) -> int:
    evidence, settings = preflight(root)
    scenario, target, contract, registry = inputs()
    if target.base_url != ORIGIN or target.environment != "isolated":
        raise PreflightError("TARGET_SCOPE_MISMATCH")
    audit_dir = store.root / ("courseinsight-" + uuid4().hex)
    audit_dir.mkdir(parents=True)
    audit = audit_dir / "isolation.json"
    evidence.update({"scenario_sha256": fingerprint(scenario.model_dump()),
                     "launch_arguments_sha256": fingerprint(JAVA_ARGS),
                     "business_execution": "NOT_STARTED", "cleanup": "No volumes/data deleted"})
    audit.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    process = None
    try:
        process = launch(root, settings)
        evidence["health_attempts"] = await wait_health(process)
        # Recheck infrastructure immediately before authorizing the first business request.
        current, _ = preflight(root)
        if current != {key: evidence[key] for key in current}:
            raise PreflightError("ISOLATION_CHANGED")
        target.allow_mutations = True
        evidence["business_execution"] = "STARTING_ONCE_NO_RESUME"
        audit.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        result = await execute(scenario, target, contract, registry, {
            "COURSEINSIGHT_DEMO_USERNAME": settings["BOOTSTRAP_ADMIN_USERNAME"],
            "COURSEINSIGHT_DEMO_PASSWORD": settings["BOOTSTRAP_ADMIN_PASSWORD"],
        }, store)
        evidence.update({"run_id": result.run_id, "outcome": result.outcome.value,
                         "business_execution": "FINISHED", "requests": sum(
                             len(step.attempts) for step in result.steps)})
        print(json.dumps({"run_id": result.run_id, "outcome": result.outcome.value,
                          "http_statuses": [a.status_code for s in result.steps
                                            for a in s.attempts],
                          "report": str(store.root / result.run_id / "report.json"),
                          "audit": str(audit)}))
        return 0 if result.outcome == Outcome.PASS else 1
    finally:
        settings.clear()
        if process is not None:
            stop(process)
        audit.write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--execute", action="store_true",
                        help="Explicit single demo write acceptance")
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    args = parser.parse_args()
    try:
        root = args.source.resolve(strict=True)
        if args.execute:
            return asyncio.run(live(root, ReportStore(args.runs)))
        evidence, settings = preflight(root)
        settings.clear()
        print(json.dumps(evidence))
        return 0
    except PreflightError as error:
        print(json.dumps({"outcome": "BLOCKED", "code": str(error)}))
    except (OSError, ValueError, KeyError):
        print(json.dumps({"outcome": "BLOCKED", "code": "PREFLIGHT_OR_REPORT_IO_ERROR"}))
    except KeyboardInterrupt:
        print(json.dumps({"outcome": "BLOCKED", "code": "INTERRUPTED_NO_AUTO_RESUME"}))
    return 2


if __name__ == "__main__":
    if shutil.which("docker") is None:
        print(json.dumps({"outcome": "BLOCKED", "code": "DOCKER_UNAVAILABLE"}))
        sys.exit(2)
    raise SystemExit(main())
