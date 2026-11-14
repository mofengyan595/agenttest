"""Turn the P3-A.2 run directories into the acceptance table the milestone reports.

The runner (``scripts/run_p3a2.py``) executes the tasks; this script reads the archived
``run.json`` files and derives the comparison the documentation needs. Everything it reports is
read from the run: strict completion from ``task_completion``, coverage from the scope ledger,
cost from the usage ledger *and* the agent runtime, stop reasons from the execution budget.

Fault ground truth is declared here, not inferred from the run: the frozen lab answers
``/availability`` with 503 for every kind and the ``--delete-noop`` variant answers 204 while
keeping the record. A run that reports an anomaly outside this set is a false positive; a run
that misses one is a false negative. Both are published, never smoothed away.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

AVAILABILITY_503 = "AVAILABILITY_503"
DELETE_NOOP = "DELETE_NOOP"

# The deterministic lab injects /availability -> 503 for every kind, so every task that reads it
# may legitimately report that anomaly.
LAB_FAULTS = {AVAILABILITY_503}

BATCH_A = {
    "functional": LAB_FAULTS,
    "regression": LAB_FAULTS,
    "investigation": LAB_FAULTS,
    "prerelease": LAB_FAULTS,
}
BATCH_B = {
    "crud-functional": LAB_FAULTS,
    "delete-then-state": {DELETE_NOOP},
    "fault-investigation": LAB_FAULTS,
    "pre-release": LAB_FAULTS,
    "boundary-negative": LAB_FAULTS,
    "ambiguous": set(),
}


def _cases(summary):
    return [case for case in summary.get("cases") or () if case.get("status") == "FINISHED"]


def _anomalies(summary):
    """Real anomalies from executed cases: an assertion failure is a business finding."""
    found, detail = set(), []
    for case in _cases(summary):
        for step in case.get("evidence") or ():
            if not step.get("executed") or step.get("outcome") != "ASSERTION_FAILED":
                continue
            status = step.get("status")
            if status is None and isinstance(step.get("attempt"), dict):
                status = step["attempt"].get("status_code")
            if case.get("investigations") or (status or 0) >= 500:
                found.add(AVAILABILITY_503 if status == 503 else f"HTTP_{status}")
            detail.append(dict(operation_id=step.get("operation_id"),
                               policy_id=step.get("policy_id"), status=status))
    delete_noop = [case for case in _cases(summary) if case.get("absence_unproven")]
    if delete_noop:
        found.add(DELETE_NOOP)
    return found, detail


def _usage(summary):
    """Model calls and tokens, split into the agent loop and the deterministic planner.

    Three records exist and none may be silently preferred: the run's ``usage`` block (Focus and
    design), the agent runtime's own ``model`` ledger, and the shared budget ledger that both
    read. The budget ledger is the authoritative total; the split is reported so a reader can
    see how much of the cost the autonomous loop spent.
    """
    run_usage = summary.get("_run_usage") or {}
    agent = ((summary.get("agent_runtime") or {}).get("model") or {})
    ledger = (summary.get("budget") or {}).get("ledger") or {}
    agent_calls = int(agent.get("calls") or 0)
    calls = int(ledger.get("model_calls")
                if ledger.get("model_calls") is not None
                else int(run_usage.get("calls") or 0) + agent_calls)
    tokens = int(ledger.get("total_tokens")
                 if ledger.get("total_tokens") is not None
                 else int(run_usage.get("total_tokens") or 0)
                 + int(agent.get("total_tokens") or 0))
    known = bool(ledger.get("usage_known", True)) and not int(
        agent.get("unknown_usage_calls") or 0) and bool(run_usage.get("known", True))
    return dict(calls=calls, agent_calls=agent_calls,
                planner_calls=max(0, calls - agent_calls), total_calls=calls,
                tokens=tokens, usage_known=known)


def classify_run(run: dict, ground_truth, mode: str | None = None) -> dict:
    """One archived run as the acceptance table reports it."""
    summary = run.get("summary") or {}
    completion = summary.get("task_completion") or {}
    ledger = summary.get("scope_ledger") or {}
    coverage = ledger.get("coverage") or {}
    effects = (summary.get("write_effects") or {}).get("counts") or {}
    budget = summary.get("execution_budget") or {}
    runtime = summary.get("agent_runtime") or {}
    metrics = run.get("metrics") or {}
    anomalies, anomaly_rows = _anomalies(summary)
    expected = set(ground_truth or ())
    usage = _usage(summary)
    mode = budget.get("mode") or mode
    started, finished = run.get("started_at"), run.get("finished_at")
    return dict(
        complete=bool(completion.get("complete")),
        unmet=list(completion.get("unmet") or ()),
        criteria={row["id"]: row["status"] for row in completion.get("criteria") or ()},
        mode=mode,
        stop_reason=budget.get("stop_reason") or runtime.get("stop_reason"),
        growths=[dict(dimension=item.get("dimension"), previous=item.get("previous"),
                      limit=item.get("limit"), trigger=item.get("trigger"))
                 for item in budget.get("growths") or ()],
        no_progress_rounds=budget.get("no_progress_rounds"),
        authority=(budget.get("authority") or {}).get("code"),
        limit={key: (value or {}).get("limit") if isinstance(value, dict) else value
               for key, value in (budget.get("dimensions") or {}).items()},
        estimate=(budget.get("estimate") or None),
        requests=dict(attempts=metrics.get("http_attempt_count"),
                      okay=metrics.get("http_ok_count"),
                      target_events=metrics.get("target_event_count")),
        usage=usage,
        tool_calls=len(runtime.get("tool_calls") or ()),
        elapsed_seconds=(round(finished - started, 1)
                         if isinstance(started, (int, float)) and isinstance(finished, (int, float))
                         else None),
        scope=dict(selected=coverage.get("selected"), approved=coverage.get("approved"),
                   requested=coverage.get("requested"),
                   excluded=coverage.get("excluded_by_focus"),
                   deferred_negatives=coverage.get("negative_variants_deferred"),
                   unexplained=coverage.get("unexplained")),
        cleanup=dict(created_and_cleaned=effects.get("CREATED_AND_CLEANED", 0),
                     no_side_effect=effects.get("NO_SIDE_EFFECT_PROVEN", 0),
                     creation_unproven=effects.get("CREATION_UNPROVEN", 0),
                     cleanup_failed=effects.get("CREATED_CLEANUP_FAILED", 0),
                     cleanup_unproven=effects.get("CLEANUP_UNPROVEN", 0),
                     outcome_unknown=effects.get("WRITE_OUTCOME_UNKNOWN", 0),
                     replayed=int(metrics.get("write_replays") or 0)),
        anomalies=sorted(anomalies), anomaly_rows=anomaly_rows,
        false_positives=sorted(anomalies - expected),
        false_negatives=sorted(expected - anomalies),
        questions=list(summary.get("questions") or ()),
        focus=dict(mode=(summary.get("focus") or {}).get("mode"),
                   operations=len((summary.get("focus") or {}).get("operation_ids") or ())))


def _task_of(name):
    """``functional-fixed-r2`` -> (``functional``, ``fixed``, 2)."""
    parts = name.rsplit("-r", 1)
    repeat = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 1
    rest = parts[0] if repeat != 1 or len(parts) == 2 else name
    for mode in ("fixed", "adaptive"):
        if rest.endswith(f"-{mode}"):
            return rest[: -len(mode) - 1], mode, repeat
    return rest, None, repeat


def collect(root: Path) -> list[dict]:
    rows = []
    for path in sorted(root.rglob("run.json")):
        name = path.parent.name
        batch = path.parent.parent.name
        task, mode, repeat = _task_of(name)
        ground = (BATCH_A if batch == "A" else BATCH_B).get(task, set())
        run = json.loads(path.read_text(encoding="utf-8"))
        # The run's own usage block (Focus and design calls) is not part of ``summary``.
        run.setdefault("summary", {})["_run_usage"] = run.get("usage") or {}
        row = dict(id=name, batch=batch, task=task, mode=mode, repeat=repeat,
                   ground_truth=sorted(ground), run=str(path.parent.as_posix()))
        row.update(classify_run(run, ground, mode))
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    """Aggregate per (batch, task, mode): completion, cost, and anomaly accuracy."""
    groups = {}
    for row in rows:
        key = (row["batch"], row["task"], row["mode"])
        groups.setdefault(key, []).append(row)
    table = []
    for (batch, task, mode), items in sorted(groups.items(),
                                             key=lambda item: [str(part) for part in item[0]]):
        complete = sum(1 for item in items if item["complete"])
        tokens = [item["usage"]["tokens"] for item in items]
        table.append(dict(
            batch=batch, task=task, mode=mode, runs=len(items), complete=complete,
            strict_rate=f"{complete}/{len(items)}",
            calls=[item["usage"]["total_calls"] for item in items],
            agent_calls=[item["usage"]["agent_calls"] for item in items],
            tokens=tokens,
            tokens_range=[min(tokens), max(tokens)],
            requests=[item["requests"]["attempts"] for item in items],
            tool_calls=[item["tool_calls"] for item in items],
            seconds=[item["elapsed_seconds"] for item in items],
            stops=[item["stop_reason"] for item in items],
            unmet=sorted({reason for item in items for reason in item["unmet"]}),
            selected=[None if not item["scope"]["selected"] else
                      item["scope"]["selected"]["ratio"] for item in items],
            approved=[None if not item["scope"]["approved"] else
                      item["scope"]["approved"]["ratio"] for item in items],
            cleanup_created=sum(item["cleanup"]["created_and_cleaned"] for item in items),
            cleanup_unproven=sum(item["cleanup"]["cleanup_unproven"]
                                 + item["cleanup"]["creation_unproven"] for item in items),
            write_unknown=sum(item["cleanup"]["outcome_unknown"] for item in items),
            replayed=sum(item["cleanup"]["replayed"] for item in items),
            false_positives=sorted({name for item in items for name in item["false_positives"]}),
            false_negatives=sorted({name for item in items for name in item["false_negatives"]}),
            usage_known=all(item["usage"]["usage_known"] for item in items)))
    totals = dict(runs=len(rows),
                  complete=sum(1 for row in rows if row["complete"]),
                  tokens=sum(row["usage"]["tokens"] for row in rows),
                  usage_unknown=sum(1 for row in rows if not row["usage"]["usage_known"]),
                  replayed=sum(row["cleanup"]["replayed"] for row in rows),
                  write_unknown=sum(row["cleanup"]["outcome_unknown"] for row in rows))
    return dict(schema="agenttest.p3a2-analysis.v1", rows=rows, table=table, totals=totals)


def markdown(report: dict) -> str:
    """The acceptance table as Markdown, so the documentation quotes one source."""
    lines = ["| 批次 | 任务 | 模式 | 严格完成 | 模型调用 | tokens | HTTP | 工具 | 秒 | 停止 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["table"]:
        lines.append(
            f"| {row['batch']} | {row['task']} | {row['mode'] or '-'} | {row['strict_rate']} "
            f"| {row['calls']} | {row['tokens_range'][0]}-{row['tokens_range'][1]} "
            f"| {row['requests']} | {row['tool_calls']} | {row['seconds']} "
            f"| {', '.join(str(stop) for stop in row['stops'])} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="runs/p3a2/final")
    parser.add_argument("--out", default="runs/p3a2/analysis.json")
    parser.add_argument("--markdown", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root)
    report = summarize(collect(root))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = markdown(report)
    if args.markdown:
        Path(args.markdown).write_text(text + "\n", encoding="utf-8")
    totals = report["totals"]
    print(json.dumps(dict(runs=totals["runs"], complete=totals["complete"],
                          usage_unknown=totals["usage_unknown"], tokens=totals["tokens"],
                          replayed=totals["replayed"], write_unknown=totals["write_unknown"]),
                     ensure_ascii=False))
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
