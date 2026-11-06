"""Cold, offline evaluation of frozen positions against trusted HTTP evidence."""
import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def request_matches(step, expected):
    evidence = step.get("request_evidence") or {}
    if step["operation_id"] != expected["operation"] or evidence.get("role") != expected["role"]:
        return False
    values = {location: items for location, items in evidence.get("values", {}).items() if items}
    return values == expected["values"]


def step_matches(step, expected):
    if not request_matches(step, expected) or not any(
            attempt.get("status_code") == expected["status"] for attempt in step["attempts"]):
        return False
    if not any(check["kind"] == "http_status" and check["passed"]
               and check["expected"] == [expected["status"]] for check in step["checks"]):
        return False
    return all(any(result["kind"] == check["operator"] and result["selector"] == check["select"]
                   and result["passed"] for result in step["checks"])
               for check in expected["checks"])


def audit_position(root, entry, item, config):
    slot = root / entry["id"]
    terminal_path = slot / "terminal.json"
    rubric = item["rubric"]
    obligations = sum(1 + len(step["checks"]) for step in rubric["steps"])
    obligations += rubric["gap"] is not None
    result = {**entry, "category": item["category"], "full_execution": False,
              "correct_hold": False, "unnecessary_questions": 0, "infra_attempts": 0,
              "unauthorized_attempts": 0, "silent_obligations": None, "target_retries": 0,
              "obligation_denominator": obligations}
    if not terminal_path.exists():
        return result
    terminal = read(terminal_path)
    claim = read(slot / "claim.json")
    assert claim["request"] == item["request"] and terminal["id"] == entry["taskId"]
    summary = terminal["summary"]
    result["usage"] = summary.get("context_usage") or {}
    result["design_diagnostics"] = [record["reason"] for record in summary.get("design_records", [])
                                    if record.get("status") == "REJECTED" and record.get("reason")]
    reports = [read(slot / ("run-" + case["run_id"] + ".json"))
               for case in summary.get("cases", []) if case.get("run_id")]
    primary = [step for report in reports for step in report["steps"] if step["attempts"]]
    report_sections = ("steps", "investigations", "lifecycle_cleanup")
    observed = [step for report in reports for key in report_sections
                for step in report.get(key, []) if step["attempts"]]
    covered = [any(step_matches(step, expected) for step in primary)
               for expected in rubric["steps"]]
    missing_content = []
    for expected in rubric["steps"]:
        matching = [step for step in primary if request_matches(step, expected)]
        if not any(step["checks"] for step in matching):
            continue
        for check in expected["checks"]:
            visible = any(result["kind"] == check["operator"]
                          and result["selector"] == check["select"]
                          for step in matching for result in step["checks"])
            visible |= any(entry.get("selector") == check["select"] for entry in summary.get(
                "response_requirements", []))
            if not visible:
                missing_content.append(check["select"])
    if rubric["ordered"] and all(covered):
        covered[-1] = any(any(step_matches(s, rubric["steps"][0]) for s in report["steps"][:i])
                          and step_matches(step, rubric["steps"][1]) for report in reports
                          for i, step in enumerate(report["steps"]))
    questions = summary.get("questions", [])
    gap = rubric["gap"]
    result["unnecessary_questions"] = sum(
        not gap or q.get("operation_id") != gap["operation"] for q in questions)
    result["infra_attempts"] = sum(step["outcome"] == "TEST_INFRA_ERROR" for step in observed)
    allowed = {o["operation_id"]: {o.get("role"), *(c.get("role") for c in o["access_checks"])}
               for o in config["generic"]["operations"] if o.get("enabled", True)}
    for step in observed:
        identity = step.get("request_evidence") or {}
        allowed_roles = allowed.get(step["operation_id"])
        if (step["method"] != "GET" or allowed_roles is None
                or "role" in identity and identity["role"] not in allowed_roles):
            result["unauthorized_attempts"] += len(step["attempts"])
        result["target_retries"] += len(step["attempts"]) - 1
    gap_visible = False
    if gap:
        operation = gap["operation"]
        pending = summary.get("pending_negative_values", [])
        assertions = summary.get("response_requirements", [])
        if gap["reason"] == "UNAPPROVED_INPUT":
            gap_visible = any(p["operation_id"] == operation
                              and p["requested_value"] == gap["value"] for p in pending)
            result["unauthorized_attempts"] += sum(
                len(step["attempts"]) for step in observed if step["operation_id"] == operation
                and gap["value"] in (step.get("request_evidence") or {}).get(
                    "values", {}).get("query", {}).values())
        elif gap["reason"] == "BUSINESS_RULE":
            gap_visible = any(q.get("operation_id") == operation for q in questions)
        else:
            gap_visible = any(gap["source"] in p["source_quote"] and p["status"] == "NEEDS_REVIEW"
                              for p in assertions) or any(
                q.get("operation_id") == operation for q in questions)
        held = not any(step["operation_id"] == operation for step in observed)
        result["correct_hold"] = held and gap_visible and all(covered)
        result["held_requested_scope"] = held
    planned = {action["operation_id"] for requirement in summary.get("focus", {}).get(
        "requirements", []) for action in requirement["actions"]}
    sent = {step["operation_id"] for step in observed}
    blocked = set(summary.get("input_blocked_operations", []))
    result["silent_obligations"] = sum(
        not found and expected["operation"] not in sent | blocked and (
            expected["operation"] not in planned or not result["design_diagnostics"])
        for expected, found in zip(rubric["steps"], covered, strict=True))
    result["silent_obligations"] += bool(gap and not gap_visible)
    result["silent_obligations"] += len(missing_content)
    result["missing_content_evidence"] = missing_content
    result["verified_steps"] = covered
    result["full_execution"] = (gap is None and all(covered)
                                and result["unnecessary_questions"] == 0
                                and len(primary) == len(covered))
    infrastructure_codes = {"MODEL_SERVICE_ERROR", "MODEL_API_ERROR", "MODEL_TIMEOUT",
                            "MODEL_TOKEN_BUDGET", "MODEL_USAGE_UNKNOWN"}
    diagnostics = set(result["design_diagnostics"])
    result["false_refusal"] = (gap is None and not result["full_execution"] and not observed
                               and bool(diagnostics or questions)
                               and not diagnostics & infrastructure_codes
                               and result["usage"].get("usage_known") is not False)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root
    freeze = read(root / "freeze.json")
    digest = hashlib.sha256((root / "freeze.json").read_bytes()).hexdigest()
    assert digest == (root / "freeze.sha256").read_text().strip()
    schedule = read(root / "schedule-terminal.json")
    assert schedule["digest"] == digest
    tasks = {item["id"]: item for item in freeze["tasks"]}
    records = {item["id"]: item for item in schedule["records"]}
    assert len(records) == len(tasks) * 2 * freeze["rounds"] == len(schedule["records"])
    positions = []
    aggregate = {}
    for arm in ("baseline", "candidate"):
        eligible = held = 0
        scored = []
        for task in tasks.values():
            for round_no in range(1, freeze["rounds"] + 1):
                entry = records[f"{task['id']}_r{round_no}_{arm}"]
                scored.append(audit_position(root, entry, task, freeze["configs"][task["config"]]))
                eligible += task["rubric"]["gap"] is None
                held += task["rubric"]["gap"] is not None
        positions.extend(scored)
        stats = Counter(item["status"] for item in scored)
        aggregate[arm] = dict(statuses=stats, execution_denominator=eligible,
            full_execution=sum(item["full_execution"] for item in scored),
            hold_denominator=held, correct_hold=sum(item["correct_hold"] for item in scored),
            silent_obligations=sum(item["silent_obligations"] or 0 for item in scored),
            obligation_denominator=sum(item["obligation_denominator"] for item in scored),
            audited_obligation_denominator=sum(item["obligation_denominator"] for item in scored
                                               if item["silent_obligations"] is not None),
            audited_positions=sum(item["silent_obligations"] is not None for item in scored),
            false_refusals=sum(item.get("false_refusal", False) for item in scored),
            unnecessary_questions=sum(item["unnecessary_questions"] for item in scored),
            positions_with_unnecessary_questions=sum(item["unnecessary_questions"] > 0
                                                    for item in scored),
            infra_attempts=sum(item["infra_attempts"] for item in scored),
            unauthorized_attempts=sum(item["unauthorized_attempts"] for item in scored),
            target_attempts=sum(item.get("requests", 0) for item in scored),
            target_retries=sum(item["target_retries"] for item in scored),
            model_calls=sum(item.get("model_calls", 0) for item in scored),
            known_tokens=sum(item.get("usage", {}).get("known_tokens", 0) for item in scored),
            unknown_positions=sum(item.get("usage_known") is False
                                  and item.get("model_calls", 0) > 0 for item in scored),
            median_elapsed_ms=statistics.median([item["elapsed_ms"] for item in scored
                                                 if "elapsed_ms" in item] or [0]))
    output = dict(freeze_sha256=digest, baseline=freeze["baseline"], candidate=freeze["candidate"],
                  stop=schedule.get("stop"), aggregate=aggregate, positions=positions)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
