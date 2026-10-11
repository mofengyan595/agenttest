"""Mechanism-level paired controls; planning and deterministic detection stay separate."""

import json
from collections import Counter

from agenttest.demo import PROJECT_ROOT, run_demo
from agenttest.evaluation import TaskCase, intent_satisfied, manual_plan, policy_selection_correct
from agenttest.llm_planner import PlannerFailure
from agenttest.natural_demo import run_natural_demo
from agenttest.report import Outcome, ReportStore, RunReport, fingerprint

TASKS = PROJECT_ROOT / "examples/stage4b-tasks.json"
FAULT_CHECKS = {"response_name": ("getCourse", "$.name"),
                "response_id": ("getCourse", "$.courseId"),
                "create_status": ("createCourse", None),
                "ownership": ("getCourse", None),
                "terminal": ("getBatch", "$.state"),
                "result_count": ("getBatchResults", "$.items")}


def annotation(case: dict) -> TaskCase:
    return TaskCase(id="T" + case["id"][1:], category=case["category"],
                    creator=case["creator"], course_name=case["course_name"],
                    user_task=case["user_task"])


def evidence(report: RunReport) -> dict:
    return {"run_id": report.run_id, "outcome": report.outcome.value, "reason": report.reason,
            "plan_valid": report.plan_valid, "intent_verified": report.intent_verified,
            "diagnostic": report.diagnostic.model_dump(mode="json") if report.diagnostic else None,
            "requests": sum(len(s.attempts) for s in report.steps),
            "course_creates": sum(len(s.attempts) for s in report.steps
                                  if s.operation_id == "createCourse"),
            "model_call": report.model_call.model_dump() if report.model_call else None,
            "failed_checks": [{"operation_id": s.operation_id, "kind": c.kind,
                               "selector": c.selector} for s in report.steps
                              for c in s.checks if not c.passed],
            "step_errors": [s.reason for s in report.steps if s.outcome != Outcome.PASS],
            "scenario_sha256": report.scenario_sha256}


def detected(fault: str, report: RunReport) -> bool:
    operation, selector = FAULT_CHECKS[fault]
    return report.outcome == Outcome.ASSERTION_FAILED and any(
        s.operation_id == operation and not check.passed and check.selector == selector
        for s in report.steps for check in s.checks)


def detection_metrics(pairs: list[dict]) -> dict:
    faults = [p for p in pairs if p["fault"] != "none"]
    controls = [p for p in pairs if p["fault"] == "none"]
    counts = {"fault_cases": len(faults), "controls": len(controls),
              "detected": sum(p["detected"] for p in faults),
              "false_negatives": sum(p["outcome"] == "PASS" for p in faults),
              "false_positives": sum(p["outcome"] == "ASSERTION_FAILED" for p in controls),
              "inconclusive_faults": sum(not p["detected"] and p["outcome"] != "PASS"
                                         for p in faults)}
    return {**counts,
            "defect_detection_rate": counts["detected"] / len(faults) if faults else None,
            "false_negative_rate": counts["false_negatives"] / len(faults) if faults else None,
            "false_positive_rate": counts["false_positives"] / len(controls) if controls else None}


class CapturingPlanner:
    """Keep a single plan in volatile memory for paired runs, never persist raw output."""
    def __init__(self, delegate):
        self.delegate, self.output = delegate, None

    @property
    def stats(self):
        return getattr(self.delegate, "stats", None)

    async def generate(self, context):
        self.output = await self.delegate.generate(context)
        return self.output


class Abstain:
    async def generate(self, context):
        raise PlannerFailure(Outcome.NEEDS_REVIEW, "MODEL_NO_APPROVED_PLAN")


async def evaluate(baseline: str, store: ReportStore, *, planner=None,
                   max_calls: int = 0, max_tokens: int = 0, before_call=None,
                   record=None) -> dict:
    from agenttest.workflow import FakeLLM, WorkflowBudget

    if baseline not in {"manual", "fake", "real"}:
        raise ValueError("Unknown baseline")
    if baseline == "real" and (planner is None or not 1 <= max_calls <= 4 or max_tokens < 1):
        raise ValueError("Explicit live model and budgets required")
    suite = json.loads(TASKS.read_bytes())
    fixed = manual_plan(annotation(suite["cases"][0])).model_dump_json()
    tasks, pairs, calls, tokens, stop = [], [], 0, 0, None
    for case in suite["cases"]:
        supported = case["category"] != "review"
        if baseline == "real":
            if (stop or calls >= max_calls or tokens + planner.token_reservation > max_tokens):
                stop = stop or "EVALUATION_BUDGET"
                continue
            if before_call:
                before_call()
            selected = planner
        elif baseline == "manual":
            selected = FakeLLM(manual_plan(annotation(case)).model_dump_json()) if supported \
                else Abstain()
        else:
            selected = FakeLLM(fixed)
        captured = CapturingPlanner(selected)
        report = await run_natural_demo(case["user_task"], captured, store,
                                       budget=WorkflowBudget(model_timeout=65))
        row = {"task_id": case["id"], "category": case["category"], **evidence(report)}
        row.update({"policy_selection_correct": policy_selection_correct(annotation(case), report)
                    if supported else None,
                    "intent_satisfied": intent_satisfied(annotation(case), report)
                    if supported else None,
                    "end_to_end_success": supported and report.outcome == Outcome.PASS
                    and report.workflow_completed is True and report.intent_verified,
                    "safely_rejected": not supported and not report.steps
                    and report.outcome in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}})
        if supported:
            row["end_to_end_success"] &= bool(row["intent_satisfied"]
                                              and row["policy_selection_correct"])
        tasks.append(row)
        if baseline == "real":
            stats = report.model_call
            calls += stats.calls if stats else 1
            if stats and stats.total_tokens is not None:
                tokens += stats.total_tokens
            elif stats and stats.calls:
                stop = "MODEL_USAGE_UNAVAILABLE"
            if stats and stats.http_status in {401, 403, 404}:
                stop = "CHECK_MODEL_CONFIGURATION"
        if record:
            record({"kind": "task", **row})
        if supported and row["end_to_end_success"] and captured.output is not None:
            # The SAME plan is tested against a fresh normal control for EVERY fault.
            # Fault switches and labels are never sent to the planner.
            for fault in case["faults"]:
                for switch in ("none", fault):
                    result = await run_natural_demo(case["user_task"],
                        FakeLLM(captured.output), store, fault=switch)
                    pair = {"task_id": case["id"], "mechanism": fault, "fault": switch,
                            "baseline": baseline, **evidence(result),
                            "detected": switch != "none" and detected(switch, result)}
                    pairs.append(pair)
                    if record:
                        record({"kind": "pair", **pair})
        captured.output = None
    # CSV/terminal expectations are existing human-approved fixtures. These are an
    # executor-only baseline, explicitly excluded from natural-language/model metrics.
    async_pairs = []
    for fault in ("terminal", "result_count"):
        for switch in ("none", fault):
            report = await run_demo(switch, store)
            async_pairs.append({"mechanism": fault, "fault": switch, **evidence(report),
                                "detected": switch != "none" and detected(switch, report)})
    supported = [r for r in tasks if r["category"] != "review"]
    return {"baseline": baseline, "suite_sha256": fingerprint(suite),
            "task_review_status": suite["review_status"],
            "requested_tasks": 4, "attempted_tasks": len(tasks), "stop_reason": stop,
            "requested_supported_tasks": 2, "supported_task_count": len(supported),
            # Unattempted supported tasks remain in the denominator; budget stops cannot
            # inflate success by silently dropping failed/unexecuted tasks.
            **{key + "_rate": sum(r[key] for r in supported) / 2
               for key in ("plan_valid", "intent_satisfied",
                "policy_selection_correct", "end_to_end_success")},
            "safe_rejections": sum(r["safely_rejected"] for r in tasks),
            "failure_classes": dict(Counter(r["outcome"] for r in tasks)),
            "generation_calls": calls, "total_tokens": tokens,
            "planning_detection": detection_metrics(pairs),
            "expected_nl_faults": 4, "not_executed_nl_faults": 4 - len(pairs) // 2,
            "async_executor_only": detection_metrics(async_pairs),
            "tasks": tasks, "pairs": pairs, "async_pairs": async_pairs}
