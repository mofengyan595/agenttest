"""Audit fault observation and actual dispatch independently of task completion."""

import argparse
import json
from pathlib import Path


def audit(run, extra_reports=()):
    summary = run.get("summary") or {}
    cases = summary.get("cases") or []
    steps = [step for case in cases
             for step in [*case.get("evidence", []), *case.get("cleanup_evidence", [])]
             if step.get("executed")]
    events = [event for event in (run.get("metrics") or {}).get("target_events", [])
              if event.get("path") != "/health"]
    ledger = summary.get("context_usage") or {}
    focus = (summary.get("focus") or {}).get("mode")
    runtime = summary.get("agent_runtime") or {}
    rejected = [(record["group_id"], (record.get("diagnostic") or {}).get("code")
                 or record.get("reason")) for record in summary.get("design_records", [])
                if record.get("status") == "REJECTED"]
    requests = ledger.get("requests")
    if requests == 0 and not events:
        first_code = (rejected or [(None, "")])[0][1] or ""
        if focus == "needs_information":
            zero = "CLARIFICATION"
        elif "BUDGET" in first_code or (runtime.get("stop_reason") or "").endswith("LIMIT"):
            zero = "BUDGET"
        elif first_code.startswith(("CONSTRUCTION_", "PLANNING_", "DESIGN_REQUIRED_ORDER_")):
            zero = "PLANNING_IR_OR_COMPILER"
        elif first_code.startswith("FOCUS_"):
            zero = "FOCUS_REJECTED"
        elif rejected:
            zero = "MODEL_PLANNING"
        elif runtime.get("stop_reason") == "ANSWER":
            zero = "AGENT_ENDED"
        else:
            zero = "OTHER"
    else:
        zero = None

    mismatches = []
    for step in steps:
        if step.get("outcome") != "ASSERTION_FAILED":
            continue
        for check in step.get("checks") or ():
            if check.get("passed") is False:
                mismatches.append(dict(operation_id=step.get("operation_id"),
                    step_id=step.get("step_id"), policy_id=step.get("policy_id"), **check))
    for report in extra_reports:
        for step in report.get("steps") or ():
            if not step.get("attempts"):
                continue
            steps.append(step)
            if step.get("outcome") == "ASSERTION_FAILED":
                mismatches.extend(dict(operation_id=step.get("operation_id"),
                    step_id=step.get("step_id"), run_id=report.get("run_id"), **check)
                    for check in step.get("checks") or () if check.get("passed") is False)
    # Historical probes were omitted from cases. Recover the actual status assertion from
    # a structured investigation result where its original report was not exported.
    investigated = []
    for turn in runtime.get("transcript") or ():
        if turn.get("kind") != "TOOL_RESULT" or turn.get("name") != "investigate_failure":
            continue
        try:
            view, _ = json.JSONDecoder().raw_decode(turn.get("preview") or "")
        except ValueError:
            continue
        if view.get("outcome") == "ASSERTION_FAILED":
            investigated.extend(check for check in view.get("failing_checks") or ()
                                if check.get("kind") == "http_status")
    availability_observed = any(event.get("path") == "/availability" for event in events)
    availability_detected = availability_observed and (
        any(row["operation_id"] == "readAvailability" and row["kind"] == "http_status"
            and row["actual"] == 503 for row in mismatches)
        or any(row.get("actual") == 503 and row.get("passed") is False
               for row in investigated))
    delete_noop = any(item in str(run.get("attempt")) for item in (
        "delete-then-state", "fresh-delete"))
    absences = {(step.get("operation_id"), step.get("step_id")) for case in cases
                for step in case.get("absence_expectations", [])
                if step.get("direction") == "absence" and step.get("deletions")}
    delete_observed = any((step.get("operation_id"), step.get("step_id")) in absences
                          for step in steps)
    delete_detected = delete_observed and any(
        (row["operation_id"], row["step_id"]) in absences and row["kind"] == "http_status"
        and row["actual"] == 200 for row in mismatches)
    faults = dict(AVAILABILITY_503=dict(observed=availability_observed,
                                       detected=availability_detected))
    if delete_noop:
        faults["DELETE_NOOP"] = dict(observed=delete_observed, detected=delete_detected)
    return dict(attempt=run.get("attempt"), requests=requests, target_events=len(events),
                reported_attempts=(run.get("metrics") or {}).get("http_attempt_count"),
                zero_reason=zero, first_rejection=rejected[0] if rejected else None,
                focus=focus, stop=runtime.get("stop_reason"), faults=faults,
                complete=(summary.get("task_completion") or {}).get("complete", False),
                coverage=(summary.get("scope_ledger") or {}).get("coverage", {}),
                matched_operations=sorted({step["operation_id"] for step in steps}),
                assertion_mismatches=mismatches, usage=ledger,
                write_effects=(summary.get("write_effects") or {}).get("counts"),
                unconfirmed=(summary.get("task_completion") or {}).get("anomalies", {}).get(
                    "unconfirmed", []), remaining_records=(run.get("target_health") or {}).get(
                    "record_count"), error=run.get("error"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.root.rglob("run.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        exported = set((run.get("reports") or {}).keys())
        reports = (json.loads(report.read_text(encoding="utf-8")) for report in
                   sorted((path.parent / "lab/workspace/reports").glob("*/report.json")))
        rows.append(audit(run, (report for report in reports if report["run_id"] not in exported)))
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
