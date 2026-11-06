"""Cold-score the planning-stability comparison from the frozen HTTP reports."""

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from audit_requirement_witness_comparison import audit_position, read, request_matches, step_matches


def distinct_witnesses(primary, expected):
    """Match obligations to distinct steps, allowing one request to prove multiple field checks."""
    candidates = [[j for j, step in enumerate(primary) if step_matches(step, check)]
                  for check in expected]
    assigned = {}

    def match(index, visited):
        for step in candidates[index]:
            if step in visited:
                continue
            visited.add(step)
            if step not in assigned or match(assigned[step], visited):
                assigned[step] = index
                return True
        return False

    for index in range(len(expected)):
        match(index, set())
    return [index in assigned.values() for index in range(len(expected))]


def score_position(root, entry, item, config):
    result = audit_position(root, entry, item, config)
    slot = root / entry["id"]
    if not (slot / "terminal.json").exists():
        return result
    summary = read(slot / "terminal.json")["summary"]
    reports = [read(slot / f"run-{case['run_id']}.json")
               for case in summary.get("cases", []) if case.get("run_id")]
    primary = [step for report in reports for step in report["steps"] if step["attempts"]]
    expected = item["rubric"]["steps"]
    witnesses = distinct_witnesses(primary, expected)
    verified = [old and new for old, new in zip(result["verified_steps"], witnesses, strict=True)]
    result["verified_steps"] = verified
    result["full_execution"] &= all(verified)
    result["correct_hold"] &= all(verified)
    records = summary.get("design_records", [])
    result["model_diagnostics"] = [dict(group=record["group_id"], reason=record.get("reason"),
        diagnostic=record.get("diagnostic") or (record.get("model_call") or {}).get("diagnostic"))
        for record in records if record["status"] == "REJECTED"]
    result["joined_read_cases"] = sum(plan.get("joined_read_cases", 0)
        for record in records for plan in record.get("plan_construction", []))
    result["coalesced_read_cases"] = sum(record.get("coalesced_read_cases", 0)
                                        for record in records)

    # A repeated read can disappear even when its first occurrence has a successful witness.
    if item["category"] == "explicit_repeat" and len(primary) < len(expected):
        exact_sent = sum(request_matches(step, expected[0]) for step in primary)
        if exact_sent and summary.get("complete") and not all(verified):
            result["silent_obligations"] += len(expected) - exact_sent

    gap = item["rubric"]["gap"]
    if (gap and gap["reason"] == "UNAPPROVED_INPUT" and not result["correct_hold"]
            and not any(p["operation_id"] == gap["operation"]
                        and p["requested_value"] == gap["value"]
                        for p in summary.get("pending_negative_values", []))
            and gap["operation"] in summary.get("input_blocked_operations", [])
            and any(q.get("operation_id") == gap["operation"]
                    for q in summary.get("questions", []))
            and any(item["value"] == gap["value"] and item["status"] == "NEEDS_REVIEW"
                    and gap["operation"] in item["candidate_operations"]
                    and gap["source"] in item["source_quote"]
                    for item in summary.get("requirement_ledger", []))):
        result["silent_obligations"] -= 1
        result["correct_hold"] = result["held_requested_scope"] and all(verified)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root
    digest = hashlib.sha256((root / "freeze.json").read_bytes()).hexdigest()
    freeze = read(root / "freeze.json")
    schedule = read(root / "schedule-terminal.json")
    assert digest == (root / "freeze.sha256").read_text().strip() == schedule["digest"]
    tasks = {item["id"]: item for item in freeze["tasks"]}
    rows = {entry["id"]: entry for entry in schedule["records"]}
    assert len(rows) == len(schedule["records"]) == len(tasks) * 2 * freeze["rounds"]
    aggregate, positions = {}, []
    for arm in ("baseline", "candidate"):
        results = []
        for task in tasks.values():
            for turn in range(1, freeze["rounds"] + 1):
                results.append(score_position(root, rows[f"{task['id']}_r{turn}_{arm}"],
                                              task, freeze["configs"][task["config"]]))
        positions.extend(results)
        attempts = [result for result in results if "elapsed_ms" in result]
        auditable = [result for result in results if result["silent_obligations"] is not None]
        eligible = sum(t["rubric"]["gap"] is None for t in tasks.values()) * freeze["rounds"]
        aggregate[arm] = dict(statuses=Counter(r["status"] for r in results),
            execution_denominator=eligible, hold_denominator=len(results) - eligible,
            audited_positions=len(auditable),
            audited_obligations=sum(r["obligation_denominator"] for r in auditable),
            planned_obligations=sum(r["obligation_denominator"] for r in results),
            complete=sum(r["full_execution"] for r in results),
            correct_holds=sum(r["correct_hold"] for r in results),
            false_refusals=sum(r.get("false_refusal", False) for r in results),
            silent_obligations=sum(r["silent_obligations"] for r in auditable),
            unauthorized_attempts=sum(r["unauthorized_attempts"] for r in results),
            target_attempts=sum(r.get("requests", 0) for r in results),
            target_retries=sum(r["target_retries"] for r in results),
            infra_attempts=sum(r["infra_attempts"] for r in results),
            model_calls=sum(r.get("model_calls", 0) for r in results),
            known_tokens=sum(r.get("usage", {}).get("known_tokens", 0) for r in results),
            unknown_positions=sum(r.get("usage_known") is False and r.get("model_calls", 0) > 0
                                  for r in results),
            median_elapsed_ms=statistics.median(r["elapsed_ms"] for r in attempts)
            if attempts else None,
            coalesced_read_cases=sum(r.get("coalesced_read_cases", 0) for r in results),
            joined_read_cases=sum(r.get("joined_read_cases", 0) for r in results))
    print(json.dumps(dict(freeze_sha256=digest,baseline=freeze["baseline"],
        candidate=freeze["candidate"],stop=schedule.get("stop"),aggregate=aggregate,
        positions=positions),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
