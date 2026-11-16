"""Run one ecnu-max task across disposable local worker processes."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from prepare_colleague_workspace import prepare
from run_development_agent import (
    ADAPTIVE_INPUT_BYTES,
    ADAPTIVE_INPUT_TOKENS,
    ROOT,
    RecordingModel,
    collect_reports,
    free_port_block,
    limited_project,
    model_settings,
    wait_health,
)
from run_p3a3 import source_fingerprint
from run_p3b_gate import FRESH

from agenttest.execution_budget import ADAPTIVE, ExecutionBudget
from agenttest.human_review import ReviewDecision
from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.product_design import TestDesignModel
from agenttest.product_durability import PAUSED
from agenttest.product_focus import TestFocusModel
from agenttest.report import ReportStore
from agenttest.step_recovery import RESPONSE, StepJournal, tool_attempts
from agenttest.task_service import TaskService
from agenttest.task_store import TERMINAL, TaskStore

OWNER = "dev_operator"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


async def worker(out, kind, step, task_id, pause):
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("fault") or manifest.get("hitl")) and (
            manifest["source_sha256"] != source_fingerprint()):
        raise RuntimeError("RECOVERY_ACCEPTANCE_SOURCE_CHANGED")
    fault = manifest.get("fault")
    if fault and not (out / "interrupted.json").exists():
        def stop_after_response(journal, phase, result):
            if (phase == fault.get("phase", RESPONSE) and result.method == fault["method"]
                    and result.attempts[-1].status_code == fault["status"]):
                os._exit(89)

        StepJournal.boundary = stop_after_response
    health = wait_health(manifest["port"])
    if health.get("instance_id") != manifest["target_epoch"] or not health.get("disposable"):
        raise RuntimeError("LAB_INSTANCE_CHANGED")
    settings = model_settings(ROOT / ".env", 60, ADAPTIVE_INPUT_BYTES, 8192, max_total=650000)
    if settings.model != "ecnu-max":
        raise ValueError("Expected ecnu-max")
    store = out / "lab/workspace"
    service = TaskService({}, TaskStore(store / "tasks.sqlite3"), ReportStore(store / "reports"),
        lambda _: OpenAICompatiblePlanner(settings, profile="generic", planning_constraints=True),
        generic_intake=True, model_status=lambda: "CONFIGURED_MODEL",
        environ=dict(AGENTTEST_DURABLE_EXECUTION="1",
                     AGENTTEST_DURABLE_TARGET_EPOCH=manifest["target_epoch"],
                     AGENTTEST_HITL="1" if manifest.get("hitl") else "0",
                     AGENTTEST_HITL_OPERATIONS="createParcel",
                     AGENTTEST_DURABLE_PAUSE_AFTER_TOOLS="1" if pause else "0"),
        focus_factory=lambda _: RecordingModel(TestFocusModel(settings, profile="generic"),
                                               "focus", out / "model-outputs.jsonl"),
        designer_factory=lambda _: RecordingModel(TestDesignModel(settings, profile="generic"),
                                                  "design", out / "model-outputs.jsonl"),
        agent_model_factory=lambda _: settings)
    project_id, = [record["id"] for record in service.intake.list() if
        record.get("generic", {}).get("base_url") == f"http://127.0.0.1:{manifest['port']}"]
    lookup = service.project
    service.project = lambda key: limited_project(lookup(key), max_requests=120,
        max_model_calls=64, max_input=ADAPTIVE_INPUT_TOKENS, max_output=8192, max_seconds=900,
        supervisor_rounds=0, loop_engine="DEEP_AGENTS", agent_max_turns=40,
        agent_max_tool_calls=60, agent_max_model_calls=0, execution=ExecutionBudget(
            mode=ADAPTIVE, model_calls=16, total_tokens=180000, model_calls_ceiling=64,
            total_tokens_ceiling=650000, agent_turns_ceiling=40, agent_tool_calls_ceiling=60,
            seconds_ceiling=900, no_progress_limit=3))
    service.dev_max_seconds = 900
    start = time.perf_counter()
    try:
        if task_id:
            row = service.db.get(task_id, OWNER)
            reviews = service.review_records(task_id, OWNER)
            waiting = [review for review in reviews if review["status"] == "WAITING_APPROVAL"]
            for review in waiting:
                decision = ReviewDecision(decision=manifest["hitl"], version=review["version"])
                await service.decide_review(task_id, OWNER, review["review_id"], decision)
            if not any(review["status"] == "REVIEW_REQUIRED" for review in reviews):
                row = service.db.get(task_id, OWNER)
                if row["status"] == "INTERRUPTED":
                    row = await service.resume(task_id, OWNER)
        else:
            row = await service.submit(OWNER, project_id, FRESH[kind], manifest["submission"])
        task_id = row["id"]
        (out / "task-id.txt").write_text(task_id, encoding="ascii")
        while row["status"] not in TERMINAL:
            await asyncio.sleep(0.15)
            row = service.db.get(task_id, OWNER)
        marker = service.db.durable_state(task_id, OWNER)
        summary = row["summary"]
        reports = collect_reports(service, summary)
        recovery = await service.recovery_status(task_id, OWNER)
        path = store / "tasks.checkpoints.sqlite3"
        result = dict(task_id=task_id, step=step, resumed=step > 0,
            elapsed_ms=round(1000 * (time.perf_counter() - start), 3),
            checkpoint_bytes=path.stat().st_size if path.exists() else 0,
            status=row["status"], summary=summary, reports=reports, recovery=recovery,
            reviews=service.review_records(task_id, OWNER),
            checkpoint_id=marker.get("checkpoint_id") if marker else None,
            target_health=wait_health(manifest["port"]))
        (out / f"phase-{step:02}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        await service.close()


async def run(out, kind, port, *, fault=None, fake_delete=False, hitl=None):
    out.mkdir(parents=True)
    base = free_port_block(port)
    epoch = uuid4().hex
    manifest = dict(port=base + 1, target_epoch=epoch, submission=uuid4().hex,
                    kind=kind, text=FRESH[kind], fault=fault, fake_delete=fake_delete, hitl=hitl,
                    source_sha256=source_fingerprint(),
                    head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, AGENTTEST_LAB_INSTANCE_ID=epoch, PYTHONUTF8="1")
    with (out / "target.log").open("wb") as target_log:
        target = subprocess.Popen([sys.executable, str(ROOT / "scripts/serve_colleague_targets.py"),
            "--project", "parcels", "--port", str(base + 1), "--events",
            str(out / "target-events.jsonl"),
            *(["--delete-noop"] if fake_delete else []),
            *(["--observe-negative-posts"] if kind == "release" else [])],
            env=env, stdout=target_log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        try:
            wait_health(base + 1)
            await prepare(out / "lab", base, env, observe_negative_posts=kind == "release")
            task_id, pause = "", fault is None and not hitl
            for step in range(18):
                phase_started = time.perf_counter()
                with (out / f"phase-{step:02}.log").open("wb") as log:
                    child = subprocess.run(
                        [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                        "--out", str(out), "--kind", kind, "--worker", str(step),
                        "--task-id", task_id, *(["--pause"] if pause else [])],
                        check=False, timeout=930, env=env, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=NO_WINDOW)
                if fault and child.returncode == 89 and not (out / "interrupted.json").exists():
                    task_id = (out / "task-id.txt").read_text(encoding="ascii").strip()
                    db = TaskStore(out / "lab/workspace/tasks.sqlite3")
                    marker = db.durable_state(task_id, OWNER)
                    row = db.get(task_id, OWNER)
                    reports = ReportStore(out / "lab/workspace/reports")
                    attempts = tool_attempts(db, task_id, marker["state"]["tool_key"])
                    checkpoint = out / "lab/workspace/tasks.checkpoints.sqlite3"
                    evidence = dict(row=row, attempts=attempts,
                        checkpoint_id=marker["checkpoint_id"],
                        checkpoint_bytes=checkpoint.stat().st_size,
                        worker_elapsed_ms=round(1000 * (time.perf_counter() - phase_started), 3),
                        tools_before_interrupted_call=len(marker["state"]["session"]["tool_calls"]),
                        target_health=wait_health(base + 1),
                        model_usage=marker["state"]["usage"], report_ids=sorted({
                            r["run_id"] for r in attempts if reports.read(r["run_id"])}))
                    (out / "interrupted.json").write_text(
                        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                    continue
                child.check_returncode()
                result = json.loads((out / f"phase-{step:02}.json").read_text(encoding="utf-8"))
                task_id = result["task_id"]
                summary = result["summary"]
                print(json.dumps(dict(step=step, status=result["status"],
                    recovery=result["recovery"], usage=summary.get("context_usage"),
                    reports=len(result["reports"])),
                    ensure_ascii=False), flush=True)
                if hitl and any(review["status"] == "WAITING_APPROVAL"
                                for review in result["reviews"]):
                    continue
                if not result["recovery"]["resumable"] or (
                    summary.get("agent_runtime", {}).get("stop_reason") != PAUSED
                ):
                    return
                if kind == "delete" and result["reports"] or kind == "release" and any(
                    batch.get("cases") for batch in summary.get("obligation_batches", [])
                ):
                    pause = False
        finally:
            target.terminate()
            target.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--kind", required=True, choices=("delete", "release"))
    parser.add_argument("--port", type=int, default=30600)
    parser.add_argument("--worker", type=int)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--break-after-method", choices=("POST", "DELETE"))
    parser.add_argument("--break-after-status", type=int)
    parser.add_argument("--fake-delete", action="store_true")
    parser.add_argument("--hitl", choices=("approve", "reject"))
    parser.add_argument("--break-before-receipt", action="store_true")
    args = parser.parse_args()
    if args.worker is not None:
        asyncio.run(worker(args.out.resolve(), args.kind, args.worker, args.task_id, args.pause))
    else:
        if bool(args.break_after_method) != bool(args.break_after_status):
            parser.error("a crash position requires both method and HTTP status")
        fault = (dict(method=args.break_after_method, status=args.break_after_status,
                      phase="RESPONSE_RECEIVED" if args.break_before_receipt else RESPONSE)
                 if args.break_after_method else None)
        asyncio.run(run(args.out.resolve(), args.kind, args.port,
                        fault=fault, fake_delete=args.fake_delete, hitl=args.hitl))


if __name__ == "__main__":
    main()
