"""Small annotated-task evaluation; fixed fixtures are never treated as generalization."""

import json
import math
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from agenttest.dsl import Scenario
from agenttest.natural_demo import run_natural_demo
from agenttest.planner_input import validate_user_task
from agenttest.report import Outcome, ReportStore, RunReport, fingerprint
from agenttest.safety import StrictModel


class TaskCase(StrictModel):
    id: str = Field(pattern=r"^T[0-9]{2}$")
    category: Literal["consistency", "ownership"]
    creator: Literal["alice", "bob"]
    course_name: str = Field(min_length=1, max_length=80)
    user_task: str


class TaskSuite(StrictModel):
    version: Literal[1]
    reviewed: bool
    review_source: str | None
    cases: list[TaskCase] = Field(min_length=1, max_length=20)

    @classmethod
    def load(cls, path: Path):
        raw = path.read_bytes()
        if len(raw) > 100_000:
            raise ValueError("Task suite too large")
        suite = cls.model_validate_json(raw)
        if len({case.id for case in suite.cases}) != len(suite.cases):
            raise ValueError("Duplicate task ids")
        for case in suite.cases:
            validate_user_task(case.user_task)
        if suite.reviewed and not (suite.review_source or "").strip():
            raise ValueError("Human review source required")
        return suite


def manual_plan(case: TaskCase) -> Scenario:
    """Oracle baseline receives annotations; it does not interpret natural language."""
    other = "bob" if case.creator == "alice" else "alice"
    roles = [case.creator, other] if case.category == "ownership" else [case.creator]
    steps = [{"id": "login_" + role, "operation_id": "login", "role": role,
              "policy_id": "nl_login_v1"} for role in roles]
    steps += [{"id": "create", "operation_id": "createCourse", "role": case.creator,
               "policy_id": "nl_create_v1", "body": {"name": {"value": case.course_name}}},
              {"id": "read", "operation_id": "getCourse", "role": case.creator,
               "policy_id": "nl_read_v1", "path_parameters": {
                   "courseId": {"from_step": "create", "select": "$.courseId"}}}]
    if case.category == "ownership":
        steps.append({"id": "other_read", "operation_id": "getCourse", "role": other,
                      "policy_id": "nl_forbidden_v1", "path_parameters": {
                          "courseId": {"from_step": "create", "select": "$.courseId"}}})
    return Scenario.model_validate({"name": "annotated baseline " + case.id, "steps": steps})


def policy_selection_correct(case: TaskCase, report: RunReport) -> bool:
    """Grade goal coverage independently of assertions/PASS; do not feed annotations to LLM."""
    if not report.plan_valid:
        return False
    grouped = {}
    for step in report.plan_structure:
        grouped.setdefault(step.policy_id, []).append(step)
    required = {"nl_login_v1", "nl_create_v1", "nl_read_v1"}
    if case.category == "ownership":
        required.add("nl_forbidden_v1")
    if set(grouped) != required:
        return False
    if len(grouped["nl_create_v1"]) != 1 or len(grouped["nl_read_v1"]) != 1:
        return False
    create, read = grouped["nl_create_v1"][0], grouped["nl_read_v1"][0]
    if (create.role != case.creator or read.role != case.creator
            or read.dependencies.get("path_parameters.courseId") != create.step_id):
        return False
    roles = {s.role for s in grouped["nl_login_v1"]}
    if case.category == "ownership":
        forbidden = grouped["nl_forbidden_v1"]
        other = "bob" if case.creator == "alice" else "alice"
        return (roles == {case.creator, other} and len(forbidden) == 1
                and forbidden[0].role == other
                and forbidden[0].dependencies.get("path_parameters.courseId") == create.step_id)
    return roles == {case.creator}


def intent_satisfied(case: TaskCase, report: RunReport) -> bool:
    if not policy_selection_correct(case, report):
        return False
    create = next(s for s in report.plan_structure if s.policy_id == "nl_create_v1")
    return create.argument_sha256.get("body.name") == fingerprint(case.course_name)


def metrics(results: list[dict]) -> dict:
    total = len(results)
    return {key + "_rate": sum(r[key] for r in results) / total if total else None
            for key in ("plan_valid", "intent_satisfied", "policy_selection_correct",
                        "end_to_end_success")}


async def evaluate(suite: TaskSuite, baseline: Literal["manual", "fake", "real"],
                   store: ReportStore, *, planner=None, max_live_calls: int = 0,
                   max_total_tokens: int = 0, budget=None, progress=None) -> dict:
    from agenttest.workflow import FakeLLM

    if baseline not in {"manual", "fake", "real"}:
        raise ValueError("Unknown baseline")
    if baseline == "real" and (
            not suite.reviewed or not suite.review_source or planner is None
            or not 1 <= max_live_calls <= 20 or max_total_tokens < 1):
        raise ValueError("Real evaluation requires reviewed suite and operator budgets")
    fixed = manual_plan(suite.cases[0]).model_dump_json()
    results, calls, tokens, stop_reason = [], 0, 0, None
    for case in suite.cases:
        if baseline == "real":
            # Reserve a whole worst-case request before proceeding; unknown usage stops the suite.
            reserve = planner.token_reservation
            if stop_reason or calls >= max_live_calls or tokens + reserve > max_total_tokens:
                stop_reason = stop_reason or "EVALUATION_BUDGET"
                continue
            selected = planner
        else:
            selected = FakeLLM(manual_plan(case).model_dump_json()
                               if baseline == "manual" else fixed)
        report = await run_natural_demo(case.user_task, selected, store, budget=budget)
        correct = policy_selection_correct(case, report)
        intent = intent_satisfied(case, report)
        success = (correct and intent and report.outcome == Outcome.PASS
                   and report.workflow_completed is True)
        classification = report.outcome.value if not correct or not success else "PASS"
        if report.outcome == Outcome.PASS and not (correct and intent):
            classification = "TASK_MISMATCH"
        elif report.outcome == Outcome.PASS and not report.workflow_completed:
            classification = "WORKFLOW_INCOMPLETE"
        results.append({"task_id": case.id, "category": case.category, "run_id": report.run_id,
                        "plan_valid": report.plan_valid, "policy_selection_correct": correct,
                        "intent_satisfied": intent,
                        "end_to_end_success": success, "outcome": report.outcome.value,
                        "failure_class": classification, "reason": report.reason,
                        "plan_sha256": report.scenario_sha256,
                        "model_call": report.model_call.model_dump()
                        if report.model_call else None})
        if baseline == "real":
            stats = report.model_call
            calls += stats.calls if stats else 1
            if stats and stats.total_tokens is not None:
                tokens += stats.total_tokens
            elif stats and stats.calls:
                stop_reason = "MODEL_USAGE_UNAVAILABLE"
            if stats and stats.http_status in {401, 403, 404}:
                stop_reason = "CHECK_MODEL_CONFIGURATION"
        if progress:
            progress({"task_id": case.id, "outcome": report.outcome.value,
                      "intent_satisfied": intent, "end_to_end_success": success,
                      "reason": report.reason})
    total = len(results)
    latencies = sorted(r["model_call"]["elapsed_ms"] for r in results if r["model_call"])
    return {"baseline": baseline, "suite_sha256": fingerprint(suite.model_dump()),
            "human_reviewed": suite.reviewed, "requested": len(suite.cases), "attempted": total,
            "not_run": len(suite.cases) - total, "stop_reason": stop_reason,
            **metrics(results),
            "by_category": {category: {"attempted": len(rows), **metrics(rows)}
                            for category in ("consistency", "ownership")
                            for rows in [[r for r in results if r["category"] == category]]},
            "failure_classes": dict(Counter(r["failure_class"] for r in results)),
            "generation_calls": calls, "total_tokens": tokens,
            "model_latency_ms": {"mean": sum(latencies) / len(latencies),
                                 "p95": latencies[math.ceil(len(latencies) * .95) - 1]}
            if latencies else None, "results": results}


def save_evaluation(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
