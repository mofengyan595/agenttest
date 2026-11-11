"""Summarize the P1 A/B evidence into the numbers the report has to state.

Reads the eight run files plus the precondition regression, and prints one row per arm so the
comparison cannot be assembled by hand from a subset of the evidence.

    .\\.venv\\Scripts\\python.exe experiments/deepagents_p1/summarize_ab.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / ".eduwork" / "runs" / "p1"

GOALS = {
    "goal1": "帮我测一下包裹管理功能。",
    "goal2": "删除功能刚改过，帮我回归一下。",
    "goal3": "这个服务准备准备上线了，帮我测一轮。",
    "goal4": "包裹接口偶尔报错，帮我看看。",
}


def load(name: str) -> dict | None:
    path = RUNS / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def arm_a_row(payload: dict) -> dict:
    cases = payload.get("cases") or []
    outcomes = [case.get("outcome") for case in cases if case.get("outcome")]
    rejected = [case for case in cases if case.get("status") == "REJECTED"]
    duplicates = [case for case in cases if case.get("status") == "SKIPPED_DUPLICATE"]
    design = payload.get("design_records") or []
    supervision = payload.get("supervision") or {}
    models = [record.get("model_call") or {} for record in design]
    supervision_calls = [record.get("model_call") or {}
                         for record in supervision.get("decisions") or ()]
    return dict(
        terminal=payload.get("status"), complete=payload.get("complete"),
        outcome=payload.get("outcome"), cases=len(cases),
        executed=len(outcomes), passes=outcomes.count("PASS"),
        assertion_failed=outcomes.count("ASSERTION_FAILED"),
        needs_review=outcomes.count("NEEDS_REVIEW"),
        rejected=len(rejected), rejected_reasons=[case.get("reason") for case in rejected],
        duplicates=len(duplicates),
        http_requests=sum(case.get("http_requests") or 0 for case in cases),
        model_calls=sum(call.get("calls") or 0 for call in models + supervision_calls),
        tokens=sum(call.get("total_tokens") or 0 for call in models + supervision_calls),
        supervision_rounds=len(supervision.get("decisions") or ()),
        supervision_stop=supervision.get("stop_reason"),
        engines=sorted({str(record.get("planning_engine")) for record in design if record}),
        engine_reasons=sorted({str(record.get("planning_engine_reason"))
                               for record in design
                               if record.get("planning_engine_reason")}),
        elapsed_ms=payload.get("elapsed_ms"),
    )


def arm_b_row(payload: dict) -> dict:
    runs = payload.get("runs") or []
    outcomes = [run.get("outcome") for run in runs if run.get("outcome")]
    calls = payload.get("tool_calls") or []
    usage = payload.get("usage") or {}
    duplicates = [call for call in calls if call.get("skipped") == "DUPLICATE"]
    refused = [call for call in calls if call.get("refused")]
    errors = [call for call in calls if call.get("error")]
    return dict(
        terminal=payload.get("stopped"),
        # "All planned runs completed" is not the same as "the agent produced a final answer":
        # a loop that hits the recursion limit can still have executed every plan.
        runs_all_completed=bool(runs) and all(run.get("completed") for run in runs),
        final_answer=bool((payload.get("answer") or "").strip()),
        outcome=(outcomes[-1] if outcomes else None), cases=len(runs),
        executed=len(outcomes), passes=outcomes.count("PASS"),
        assertion_failed=outcomes.count("ASSERTION_FAILED"),
        needs_review=outcomes.count("NEEDS_REVIEW"),
        rejected=sum(1 for call in calls if call.get("refused")),
        rejected_reasons=sorted({call.get("refused") for call in refused if call.get("refused")}),
        duplicates=len(duplicates),
        http_requests=payload.get("requests"),
        model_calls=payload.get("model_calls"), tokens=usage.get("total_tokens"),
        supervision_rounds=0, supervision_stop="N/A (single loop)",
        engines=["DEEP_AGENTS"], engine_reasons=[],
        tool_errors=[call.get("error") for call in errors],
        elapsed_ms=payload.get("elapsed_ms"),
    )


def main() -> int:
    rows = {}
    for key in GOALS:
        a, b = load(f"a-{key}.json"), load(f"b-{key}.json")
        rows[key] = dict(goal_file=key,
                         arm_a=arm_a_row(a) if a else None,
                         arm_b=arm_b_row(b) if b else None)
    regression = load("precondition-regression.json")
    payload = dict(goals=rows,
                   precondition_regression=[
                       dict(label=record["label"], preflight=record["preflight"],
                            refusal=record.get("refusal"), outcome=record.get("outcome"),
                            http_requests=record.get("http_requests"),
                            reclassified=[item["step_id"]
                                          for item in record.get("reclassified") or ()])
                       for record in (regression or {}).get("records") or ()])
    (RUNS / "ab-summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for key, row in payload["goals"].items():
        for arm in ("arm_a", "arm_b"):
            print(key, arm, json.dumps(row[arm], ensure_ascii=False))
    for record in payload["precondition_regression"]:
        print("regression", json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
