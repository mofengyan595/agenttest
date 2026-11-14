"""Run the P3-A.2 real-model batches and collect the comparable per-run metrics.

Batch A is the four frozen P3-A.1 goals, executed once with ``FIXED`` (the historical
16 model calls / 30 business requests / 420 s) and once with ``ADAPTIVE``; the two arms
differ only in the execution-budget mechanism, so they are a same-caliber A/B.

Batch B is an independent acceptance set that was not used while implementing the
mechanism: six frozen natural-language goals, each repeated ``--repeats`` times on a
fresh lab instance. Each goal names its own injected lab fault (or none), which is the
ground truth used to score anomalies, false positives and false negatives. The goals,
targets and acceptance rules are frozen here; no per-task hints reach the product.

Every invocation starts a disposable lab, prepares a fresh workspace and goes through the
same ``TaskService``/``ProductTasks`` chain as the product. Usage, scope, write effects
and budget decisions are read back from the run's own ``run.json``; nothing is inferred.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BATCH_A = [
    dict(key="functional", text="帮我测一下包裹管理功能。", kind="parcels"),
    dict(key="regression", text="删除功能刚改过，帮我回归一下。", kind="parcels"),
    dict(key="investigation", text="包裹接口偶尔报错，帮我看看。", kind="parcels"),
    dict(key="prerelease", text="这个服务准备上线了，帮我测一轮。", kind="parcels"),
]

BATCH_B = [
    dict(key="crud-functional", kind="parcels",
         text="帮我验收一下包裹服务的增删改查是否都正常。", fault=None),
    dict(key="delete-then-state", kind="parcels", delete_noop=True,
         text="包裹删除后应该查不到，帮我确认删除已经真的生效。", fault="DELETE_NOOP"),
    dict(key="fault-investigation", kind="directory",
         text="目录服务有个接口状态不对劲，帮我查清楚原因。",
         fault="AVAILABILITY_503"),
    dict(key="pre-release", kind="parcels",
         text="这批改动下周上线，帮我做一次上线前的完整检查。", fault="AVAILABILITY_503"),
    dict(key="boundary-negative", kind="parcels",
         text="帮我测一下创建包裹时数量的边界值和非法输入。", fault="AVAILABILITY_503"),
    dict(key="ambiguous", kind="directory",
         text="这个目录服务不太对劲，你看着弄吧。", fault="AVAILABILITY_503"),
]

FIXED = "FIXED"
ADAPTIVE = "ADAPTIVE"


def command(task, attempt, run_dir, *, mode, port):
    args = [sys.executable, str(ROOT / "scripts/run_development_agent.py"),
            "--text", task["text"], "--attempt", attempt, "--run-dir", str(run_dir),
            "--project-kind", task["kind"], "--port", str(port),
            "--execution-mode", mode, "--loop-engine", "DEEP_AGENTS",
            "--model-input-bytes", "60000", "--max-output-tokens", "8192",
            "--max-input-tokens", "20000", "--model-timeout", "60"]
    if task.get("delete_noop"):
        args.append("--delete-noop")
    if task.get("delete_fault_status"):
        args += ["--delete-fault-status", str(task["delete_fault_status"])]
    if mode == FIXED:
        # Exactly the historical numbers: this arm must be able to reproduce P3-A.1.
        args += ["--max-requests", "30", "--max-model-calls", "16",
                 "--agent-max-turns", "16", "--agent-max-tool-calls", "24",
                 "--max-seconds", "420", "--execution-model-ceiling", "16",
                 "--execution-token-ceiling", "120000", "--execution-seconds-ceiling", "420"]
    else:
        # The autonomous arm: the operator granted an abundant model-call budget for real
        # tasks, so the ceiling is the product's safety cap, not a rerun of the fixed one. The
        # 120-request permission is this arm's *explicit* operator grant: the pre-release
        # estimate (31 obligations, ~41 requests with cleanup) does not fit P3-A.1's 30.
        args += ["--max-requests", "120", "--max-model-calls", "96",
                 "--agent-max-turns", "80", "--agent-max-tool-calls", "200",
                 "--max-seconds", "900", "--execution-model-ceiling", "96",
                 "--execution-token-ceiling", "1000000",
                 "--execution-seconds-ceiling", "900", "--no-progress-limit", "3"]
    return args


def read_run(run_dir):
    path = run_dir / "run.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


FAULT_OPS = {
    "AVAILABILITY_503": {"readAvailability"},
    # The delete itself answers 204; the defect is visible only on the read-back.
    "DELETE_NOOP": {"deleteParcel", "readParcel", "deleteReservation", "readReservation"},
}


def executed_and_anomalies(run):
    """Operations that really ran, and the assertions that failed, from the reports."""
    measured, found = set(), []
    for report in (run.get("reports") or {}).values():
        failed = report.get("outcome") in {"ASSERTION_FAILED", "FAILED"}
        for step in report.get("steps") or ():
            if step.get("attempts"):
                measured.add(step.get("operation_id"))
            if failed and step.get("outcome") in {"ASSERTION_FAILED", "FAILED"}:
                found.append(dict(operation_id=step.get("operation_id"),
                                  policy_id=step.get("policy_id"),
                                  step_id=step.get("step_id"),
                                  statuses=[attempt.get("status_code")
                                            for attempt in step.get("attempts") or ()]))
    return sorted(measured), found


def metrics(run):
    summary = run.get("summary") or {}
    completion = summary.get("task_completion") or {}
    criteria = {row.get("id"): row for row in completion.get("criteria") or ()}
    ledger = summary.get("scope_ledger") or {}
    coverage = ledger.get("coverage") or {}
    effects = summary.get("write_effects") or {}
    budget = summary.get("execution_budget") or {}
    runtime = summary.get("agent_runtime") or {}
    usage = run.get("usage") or {}
    measured, found = executed_and_anomalies(run)
    expected_ops = FAULT_OPS.get(run.get("_fault") or "", set())
    found_ops = {row["operation_id"] for row in found}
    expected = bool(expected_ops & set(measured))
    explained = found_ops & expected_ops
    metrics_view = run.get("metrics") or {}
    attempts = int(metrics_view.get("http_attempt_count") or 0)
    events = int(metrics_view.get("target_event_count") or 0)
    return dict(
        task_status=run.get("task_status"), reason=run.get("task_reason"),
        complete=completion.get("complete"), satisfied=completion.get("task_satisfied"),
        criteria={key: value.get("status") for key, value in criteria.items()},
        scope=dict(selected=(coverage.get("selected") or {}).get("ratio"),
                   approved=(coverage.get("approved") or {}).get("ratio"),
                   selected_count=(ledger.get("selected") or {}).get("count"),
                   approved_count=(ledger.get("approved") or {}).get("count"),
                   excluded=[item.get("policy_id") for item in ledger.get("excluded") or ()][:40],
                   unexplained=[item.get("policy_id") for item in ledger.get("unexplained") or ()]),
        requests=attempts, target_events=events,
        unattributed_requests=max(0, events - attempts),
        model_calls=metrics_view.get("model_calls"),
        tokens=metrics_view.get("model_tokens"),
        input_tokens=metrics_view.get("model_input_tokens"),
        output_tokens=metrics_view.get("model_output_tokens"),
        usage_known=bool(usage) and all(value is not None for value in (
            usage.get("input_tokens"), usage.get("output_tokens"))),
        tool_calls=len(runtime.get("tool_calls") or ()),
        enforcement=runtime.get("enforcement"),
        stop_reason=(runtime.get("stop_reason") or summary.get("reason")),
        elapsed=round(float(run.get("finished_at") or 0) - float(run.get("started_at") or 0), 1),
        budget=dict(mode=budget.get("mode"),
                    dimensions={key: dict(limit=value.get("limit"), ceiling=value.get("ceiling"),
                                          source=value.get("source"))
                                for key, value in (budget.get("dimensions") or {}).items()},
                    growths=budget.get("growths") or [],
                    authority=budget.get("authority"),
                    stop=budget.get("stop_reason"),
                    no_progress=budget.get("no_progress_rounds")),
        cleanup=dict(counts=effects.get("counts") or {},
                     pending=[row.get("step_id") for row in effects.get("pending") or ()],
                     unknown=[row.get("step_id") for row in effects.get("unresolved") or ()],
                     replayed=effects.get("replayed") or []),
        anomalies=found, measured_operations=measured,
        fault=run.get("_fault"), expected_operations=sorted(expected_ops),
        anomaly_expected=expected, anomaly_observed=bool(explained),
        false_positive=bool(found_ops - expected_ops),
        false_negative=expected and not explained,
        questions=len(summary.get("questions") or ()),
        focus=dict(mode=(summary.get("focus") or {}).get("mode"),
                   fallback=summary.get("focus_fallback")))


def summarize(rows):
    def ratio(items):
        values = [item for item in items if item is not None]
        return round(sum(values) / len(values), 3) if values else None

    def total(key):
        return sum(int((row.get("metrics") or {}).get(key) or 0) for row in rows)

    stops: dict[str, int] = {}
    for row in rows:
        reason = (row.get("metrics") or {}).get("stop_reason") or "UNKNOWN"
        stops[reason] = stops.get(reason, 0) + 1
    complete = [row for row in rows if (row.get("metrics") or {}).get("complete")]
    return dict(
        runs=len(rows),
        strict_completion=dict(passed=len(complete), total=len(rows)),
        selected_coverage=ratio([(row.get("metrics") or {}).get("scope", {}).get("selected")
                                 for row in rows]),
        approved_coverage=ratio([(row.get("metrics") or {}).get("scope", {}).get("approved")
                                 for row in rows]),
        anomalies=sum(len((row.get("metrics") or {}).get("anomalies") or ()) for row in rows),
        false_positives=sum(1 for row in rows if (row.get("metrics") or {}).get("false_positive")),
        false_negatives=sum(1 for row in rows if (row.get("metrics") or {}).get("false_negative")),
        unknown_usage=sum(1 for row in rows if not (row.get("metrics") or {}).get("usage_known")),
        model_calls=total("model_calls"), tokens=total("tokens"),
        input_tokens=total("input_tokens"), output_tokens=total("output_tokens"),
        requests=total("requests"), unattributed_requests=total("unattributed_requests"),
        seconds=round(sum(float((row.get("metrics") or {}).get("elapsed") or 0)
                          for row in rows), 1),
        cleanup_pending=sum(len((row.get("metrics") or {}).get("cleanup", {}).get("pending") or ())
                            for row in rows),
        write_unknown=sum(len((row.get("metrics") or {}).get("cleanup", {}).get("unknown") or ())
                          for row in rows),
        replayed=sum(len((row.get("metrics") or {}).get("cleanup", {}).get("replayed") or ())
                     for row in rows),
        stops=stops)


def run_batch(tasks, *, batch, out, repeats, start_port, resume):
    rows = []
    for index, task in enumerate(tasks):
        for repeat in range(repeats):
            mode = task.get("mode") or ADAPTIVE
            key = task["key"] if batch == "B" else f"{task['key']}-{mode.lower()}"
            run_dir = out / f"{key}-r{repeat + 1}"
            attempt = f"{batch}:{key}:{repeat + 1}"
            row = dict(batch=batch, key=key, repeat=repeat + 1, mode=mode,
                       text=task["text"], kind=task["kind"], run_dir=str(run_dir))
            existing = read_run(run_dir) if resume else None
            if existing is not None:
                existing["_fault"] = task.get("fault")
                row["metrics"] = metrics(existing)
                rows.append(row)
                print(f"resume {key} r{repeat + 1} {row['metrics'].get('complete')}",
                      flush=True)
                continue
            port = start_port + (index * repeats + repeat) * 8
            started = time.monotonic()
            process = subprocess.run(command(task | dict(mode=mode), attempt, run_dir,
                                             mode=mode, port=port),
                                     cwd=ROOT, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace")
            row["exit_code"] = process.returncode
            row["seconds"] = round(time.monotonic() - started, 1)
            row["stderr"] = (process.stderr or "")[-2000:]
            run = read_run(run_dir)
            if run is not None:
                run["_fault"] = task.get("fault")
                row["metrics"] = metrics(run)
            else:
                row["metrics"] = dict(error="NO_RUN_JSON")
            rows.append(row)
            print(f"{key} r{repeat + 1} exit={row['exit_code']} "
                  f"complete={(row['metrics'] or {}).get('complete')} "
                  f"calls={(row['metrics'] or {}).get('model_calls')} "
                  f"{row['seconds']}s", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", choices=["A", "B", "all"], default="all")
    parser.add_argument("--out", type=Path, default=ROOT / "runs/p3a2")
    parser.add_argument("--repeats", type=int, default=3,
                        help="independent-set repetitions per goal (batch B)")
    parser.add_argument("--start-port", type=int, default=18400)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    if args.batch in {"A", "all"}:
        tasks = [task | dict(mode=FIXED) for task in BATCH_A]
        tasks += [task | dict(mode=ADAPTIVE) for task in BATCH_A]
        rows += run_batch(tasks, batch="A", out=args.out / "A", repeats=1,
                          start_port=args.start_port, resume=args.resume)
    if args.batch in {"B", "all"}:
        rows += run_batch(BATCH_B, batch="B", out=args.out / "B", repeats=args.repeats,
                          start_port=args.start_port + 2_000, resume=args.resume)
    summary = dict(schema="agenttest.p3a2-acceptance.v2", head=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        started_at=time.time(), batch=args.batch, repeats=args.repeats,
        tasks=dict(A=BATCH_A, B=BATCH_B), rows=rows, aggregate=summarize(rows))
    (args.out / f"summary-{args.batch}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["aggregate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
