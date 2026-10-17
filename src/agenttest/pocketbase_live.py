"""Operator-only Windows acceptance against one pinned, disposable PocketBase instance."""

import asyncio
import hashlib
import os
import socket
import subprocess
import time
import zipfile
from pathlib import Path

import httpx

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.pocketbase_acceptance import (
    CASE_IDS,
    DIRECTORY,
    ORIGIN,
    credentials,
    finish,
    inputs,
    run_trial,
    schedule,
    snapshot,
)
from agenttest.report import ReportStore

EXE_SHA256 = "a6d3fb3555b64655e22c797aab7f7c396e9a2c48d7de89135d868b1758734860"
MAX_CALLS = 18
RESERVATION = 24_096


def executable_bytes(archive):
    """Accept only the pinned official archive and its single known Windows executable."""
    expected = read_json(DIRECTORY / "provenance.json")["asset_sha256"]
    if archive.stat().st_size != 12_833_751:
        raise ValueError("PocketBase archive size changed")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("PocketBase archive hash changed")
    with zipfile.ZipFile(archive) as source:
        if source.getinfo("pocketbase.exe").file_size != 33_470_976:
            raise ValueError("PocketBase executable size changed")
        data = source.read("pocketbase.exe")
    if hashlib.sha256(data).hexdigest() != EXE_SHA256:
        raise ValueError("PocketBase executable hash changed")
    return data


def unattempted(slot, reason):
    return {**slot, "run_id": None, "report_sha256": None, "plan": None,
        "grade": {"verified": False, "outcome": "NOT_ATTEMPTED", "requests": 0,
                  "task_satisfied": False}, "outcome": "NOT_ATTEMPTED", "model_calls": 0,
        "total_tokens": None, "workflow_elapsed_ms": None, "reason": reason}


async def wait_ready(process, state):
    """Probe only the fixed loopback health route, with no redirects or proxy environment."""
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=0.5) as client:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                state["health_http_requests"] += 1
                response = await client.get(ORIGIN + "/api/health")
                if response.status_code == 200 and process.poll() is None:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.25)
    raise ValueError("PocketBase did not become ready")


def stop(process):
    """Reap only this owned, single Go process; never stop a listener by port/name."""
    if process is None:
        return True
    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return process.poll() is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


async def execute(output: Path, archive: Path, model_env: Path, *, isolated_approved=False):
    """Six fixed baselines gate 18 paid calls; no retry, assertion repair, or target options."""
    if isolated_approved is not True:
        raise ValueError("Explicit isolated PocketBase write approval is required")
    if os.name != "nt":
        raise ValueError("This pinned runtime is Windows only")
    from agenttest.workflow import FakeLLM
    native = executable_bytes(archive)
    settings = ModelSettings.from_env_file(model_env)
    if settings.model != "ecnu-plus" or settings.protocol != "chatecnu":
        raise ValueError("This proposal only permits the existing ecnu-plus provider")
    settings.timeout = 60.0
    if (settings.max_input_tokens + settings.max_output_tokens != RESERVATION
            or settings.max_total_tokens != 25_000):
        raise ValueError("Model reservation changed")
    identity = checkout_identity(PROJECT_ROOT)
    if identity["worktree_status"]:
        raise ValueError("Live acceptance requires a clean, committed checkout")
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    environ = credentials()
    frozen = {"schema_version": "agenttest.pocketbase.freeze.v1", "source": "real",
        "schedule": schedule(), "cases": {key: snapshot(key, environ) for key in CASE_IDS},
        "git": identity, "model": public_model_config(settings),
        "max_calls": MAX_CALLS, "reserved_tokens": MAX_CALLS * RESERVATION,
        "upstream": read_json(DIRECTORY / "provenance.json")}
    save_evaluation(frozen, output / "freeze.json")
    rows = [unattempted(slot, "NOT_STARTED") for slot in schedule()]
    state = {"origin": ORIGIN, "pid": None, "native_exit_confirmed": None,
        "health_http_requests": 0, "slots_reserved": 0, "tokens_reserved": 0,
        "native_executable_sha256": EXE_SHA256, "database_retained": True,
        "stop_reason": None, "configured_api_logs_disabled": True, "inflight": None}
    store, process, checkpoint_index = ReportStore(output / "reports"), None, 0

    def guard():
        if checkout_identity(PROJECT_ROOT) != identity:
            raise ValueError("PocketBase source changed after freeze")
        if process is not None and process.poll() is not None:
            raise ValueError("Owned PocketBase process exited")

    def checkpoint():
        nonlocal checkpoint_index
        save_evaluation({"runtime": state, "results": rows},
                        output / f"checkpoint-{checkpoint_index:04}.json")
        checkpoint_index += 1

    try:
        # Refuse an occupied port before initialization; never reuse an existing service.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 18095))
        service = output / "service"
        service.mkdir()
        for name in ("data", "migrations", "hooks", "public"):
            (service / name).mkdir()
        binary = service / "pocketbase.exe"
        binary.write_bytes(native)
        migration = "1792195200_acceptance.js"
        (service / "migrations" / migration).write_bytes(
            (DIRECTORY / "pb_migrations" / migration).read_bytes())
        child_env = {key: value for key, value in os.environ.items()
                     if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        child_env.update(environ)
        guard()
        # Native console output can include an installer token. Do not persist it.
        process = subprocess.Popen([str(binary.absolute()), "serve", "--http=127.0.0.1:18095",
            "--dir=" + str((service / "data").absolute()), "--automigrate=false",
            "--migrationsDir=" + str((service / "migrations").absolute()),
            "--hooksDir=" + str((service / "hooks").absolute()),
            "--publicDir=" + str((service / "public").absolute())],
            cwd=service, env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        state["pid"] = process.pid
        checkpoint()
        await wait_ready(process, state)
        for index, slot in enumerate(schedule()):
            guard()
            if slot["phase"] == "baseline":
                _, fixed = inputs(slot["case_id"], environ)
                delegate = FakeLLM(fixed.model_dump_json())
            else:
                if not all(r["grade"]["verified"] for r in rows[:6]):
                    state["stop_reason"] = "FIXED_BASELINE_NOT_COMPLETED"
                    break
                if state["slots_reserved"] >= MAX_CALLS:
                    state["stop_reason"] = "MODEL_BUDGET_EXHAUSTED"
                    break
                state["slots_reserved"] += 1
                state["tokens_reserved"] += RESERVATION
                delegate = OpenAICompatiblePlanner(settings, profile="generic")
            state["inflight"] = slot
            checkpoint()  # Persist reservation before invocation; never refund an unknown slot.
            rows[index] = await run_trial(slot, delegate, store, environ, None, guard)
            state["inflight"] = None
            checkpoint()
            row = rows[index]
            if slot["phase"] == "baseline" and not row["grade"]["verified"]:
                state["stop_reason"] = "FIXED_BASELINE_NOT_COMPLETED"
                break
            if row["model_calls"] and row["total_tokens"] is None:
                state["stop_reason"] = "MODEL_USAGE_UNKNOWN"
                break
            if row["outcome"] in {"TEST_INFRA_ERROR", "TOOL_ERROR"}:
                state["stop_reason"] = "EXECUTION_INFRASTRUCTURE_FAILURE"
                break
        guard()
    except (OSError, ValueError, zipfile.BadZipFile):
        state["stop_reason"] = "POCKETBASE_RUNTIME_OR_FREEZE_REJECTED"
    finally:
        state["native_exit_confirmed"] = stop(process)
        if not state["native_exit_confirmed"]:
            state["stop_reason"] = "POCKETBASE_CLEANUP_UNCONFIRMED"
        for i, row in enumerate(rows):
            if row["run_id"] is None:
                rows[i] = unattempted(schedule()[i], state["stop_reason"] or "NOT_COMPLETED")
                if state["inflight"] == schedule()[i]:
                    rows[i].update(outcome="ATTEMPT_OUTCOME_UNKNOWN", model_calls=None)
                    rows[i]["grade"].update(outcome="ATTEMPT_OUTCOME_UNKNOWN", requests=None)
        checkpoint()
        save_evaluation(state, output / "runtime.json")
        environ.clear()
        result = finish(output, frozen, rows)
    return {**result, "native_exit_confirmed": state["native_exit_confirmed"],
            "stop_reason": state["stop_reason"]}
