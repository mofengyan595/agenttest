"""Paired ticket faults reuse one validated plan; public execution is offline only."""

import json
from pathlib import Path

import httpx

from agenttest.composed_planning import FrozenContextPlanner, report_digest
from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.models import PlanEvidence
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import grade, project_inputs, task_inputs
from agenttest.report import ReportStore
from agenttest.task_contract import TaskContract, task_validator

DATA = PROJECT_ROOT / "examples/ticket-fault-development.json"
CASE_IDS = ("G701", "G702", "G703")
PHASES = ("generation", "control", "fault")


def case_inputs(case_id: str, broken: bool = False):
    data = read_json(DATA)
    if (data["classification"] != "DEVELOPMENT_ONLY" or data["review_status"] != "UNREVIEWED"
            or data["human_reviewed"] != 0 or data["formal_eval_v2_score"] is not None
            or tuple(c["case_id"] for c in data["cases"]) != CASE_IDS):
        raise ValueError("Invalid ticket development scope")
    item = next((c for c in data["cases"] if c["case_id"] == case_id), None)
    if item is None:
        raise ValueError("Unknown ticket case")
    app, document, target, registry, environ = project_inputs(
        "tickets", item["fault"] if broken else "none")
    purposes = read_json(PROJECT_ROOT / "examples/planning-metadata-development.json")["purposes"]
    definitions = registry.definitions()
    for policy in definitions:
        policy.planning_purpose = purposes[policy.id]
    registry = PolicyRegistry(definitions)
    task, draft = task_inputs("tickets", item["template"])
    task.user_task, task.policy_sha256 = item["user_task"], registry.digest()
    for steps in (task.requirements.steps, draft.steps):
        for step in steps:
            for arg in step.arguments:
                if arg.name == "subject":
                    arg.value = item["subject"]
                elif arg.name == "priority":
                    arg.value = item["priority"]
    return document, target, registry, environ, httpx.ASGITransport(app), task, draft, item


def case_snapshot(case_id: str) -> dict:
    document, target, registry, _, _, task, _, item = case_inputs(case_id)
    return {"case_id": case_id, "task": task.model_dump(mode="json"),
            "contract": load_openapi(document).model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
            "registry": [p.model_dump(mode="json") for p in registry.definitions()],
            "fault": item["fault"], "failed_check": item["failed_check"]}


def unattempted(case_id: str, phase: str, reason: str) -> dict:
    return {"case_id": case_id, "phase": phase, "run_id": None, "report_sha256": None,
            "plan": None, "outcome": "NOT_ATTEMPTED", "reason": reason, "verified": False,
            "normal_completed": False, "same_plan": False, "authorized_execution": False,
            "model_calls": 0, "total_tokens": None, "requests": 0}


def report_row(snapshot: dict, phase: str, report, plan, anchor: str | None) -> dict:
    """Only the intended failed check counts; a tool error never counts as detection."""
    task = TaskContract.model_validate_json(json.dumps(snapshot["task"]))
    expectation = {"expected": "ASSERTION_FAILED" if phase == "fault" else "PASS",
                   "failed_check": snapshot["failed_check"]}
    matched = plan is not None and (phase == "generation" or plan.scenario_sha256 == anchor)
    verified = matched and grade(expectation, task, report, plan)["verified"]
    normal = matched and grade({"expected": "PASS"}, task, report, plan)["verified"]
    return {"case_id": snapshot["case_id"], "phase": phase, "run_id": report.run_id,
            "report_sha256": report_digest(report),
            "plan": plan.model_dump(mode="json") if plan else None,
            "outcome": report.outcome.value, "reason": report.reason,
            "verified": bool(verified), "normal_completed": bool(normal),
            "same_plan": bool(matched), "authorized_execution": bool(
                matched and report.dsl_valid and report.policy_authorized
                and report.intent_verified and report.workflow_completed),
            "model_calls": report.model_call.calls if report.model_call else 0,
            "total_tokens": report.model_call.total_tokens if report.model_call else None,
            "requests": sum(len(s.attempts) for s in report.steps)}


async def run_case(snapshot: dict, delegate, store: ReportStore, guard) -> list[dict]:
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    case_id, rows, scenario, anchor = snapshot["case_id"], [], None, None
    for phase in PHASES:
        if phase != "generation" and scenario is None:
            rows.append(unattempted(case_id, phase, "GENERATION_NOT_COMPLETED"))
            continue
        guard()
        document, target, registry, environ, transport, task, _, _ = case_inputs(
            case_id, phase == "fault")
        contract = load_openapi(document)
        context = planning_context(contract, target, registry, task.user_task,
                                   Scenario.model_json_schema())
        selected = delegate if phase == "generation" else FakeLLM(scenario.model_dump_json())
        planner = FrozenContextPlanner(selected, context, guard)
        report = await Workflow(document, target, registry, planner, store, transport, environ,
            WorkflowBudget(model_timeout=65, workflow_timeout=120), user_task=task.user_task,
            intent_validator=task_validator(task, contract, target, registry),
            execution_guard=guard).run()
        proof = capture_plan(planner.plan) if planner.plan else None
        row = report_row(snapshot, phase, report, proof, anchor)
        rows.append(row)
        if phase == "generation" and row["verified"]:
            scenario, anchor = planner.plan, proof.scenario_sha256
        planner.output = None
    return rows


def summarize(source: str, rows: list[dict]) -> dict:
    if (source not in {"mock_transport", "real"} or
            [(r["case_id"], r["phase"]) for r in rows] != [
                (key, phase) for key in CASE_IDS for phase in PHASES]):
        raise ValueError("Incomplete ticket fault denominator")
    generations = [r for r in rows if r["phase"] == "generation"]
    if any(type(r["model_calls"]) is not int or r["model_calls"] not in {0, 1}
           or r["phase"] != "generation" and r["model_calls"] for r in rows):
        raise ValueError("Invalid ticket model call count")
    controls = [r for r in rows if r["phase"] == "control"]
    faults = [r for r in rows if r["phase"] == "fault"]
    detected = sum(f["verified"] and c["verified"] for c, f in zip(controls, faults))
    calls = [r for r in generations if r["model_calls"]]
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
            "human_reviewed": 0, "formal_eval_v2_score": None, "source": source,
            "scheduled_tasks": 3, "generation_successes": sum(r["verified"] for r in generations),
            "model_calls": sum(r["model_calls"] for r in calls),
            "real_model_calls": sum(r["model_calls"] for r in calls) if source == "real" else 0,
            "total_tokens": sum(r["total_tokens"] for r in calls)
            if all(r["total_tokens"] is not None for r in calls) else None,
            "scheduled_faults": 3, "paired_detections": detected,
            "controls_passed": sum(r["verified"] for r in controls),
            "inconclusive_controls": sum(not r["normal_completed"] and not (
                r["authorized_execution"] and r["outcome"] == "ASSERTION_FAILED")
                for r in controls),
            "false_positives": sum(r["authorized_execution"] and r["outcome"] == "ASSERTION_FAILED"
                                   for r in controls),
            "false_negatives": sum(r["normal_completed"] for r in faults),
            "inconclusive_faults": sum(not r["verified"] and not r["normal_completed"]
                                       for r in faults),
            "not_executed": sum(r["run_id"] is None for r in rows), "results": rows}


def finish_archive(output: Path, frozen: dict, rows: list[dict]) -> dict:
    result = summarize(frozen["source"], rows)
    save_evaluation(result, output / "results.json")
    summary = {k: v for k, v in result.items() if k != "results"}
    (output / "summary.md").write_text("# 工单配对故障开发验证\n\n" + json.dumps(
        summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal(output)
    verify_faults(output)
    return summary


def verify_faults(directory: Path) -> dict:
    if read_json(directory / "archive.json")["files"] != inventory(directory):
        raise ValueError("Ticket fault archive changed")
    frozen, result = read_json(directory / "freeze.json"), read_json(directory / "results.json")
    rows = result["results"]
    if summarize(frozen["source"], rows) != result:
        raise ValueError("Ticket fault summary mismatch")
    anchors, used = {}, set()
    for row in rows:
        key, phase = row["case_id"], row["phase"]
        if row["run_id"] is None:
            if row != unattempted(key, phase, row["reason"]):
                raise ValueError("Invalid unattempted ticket trial")
            continue
        if row["run_id"] in used:
            raise ValueError("Reused ticket report")
        used.add(row["run_id"])
        report = ReportStore(directory / "reports").read(row["run_id"])
        plan = PlanEvidence.model_validate(row["plan"]) if row["plan"] else None
        if report_row(frozen["cases"][key], phase, report, plan, anchors.get(key)) != row:
            raise ValueError("Ticket fault regrading mismatch")
        if phase == "generation" and row["verified"]:
            anchors[key] = plan.scenario_sha256
    return {"archive_verified": True, "rescored_runs": len(rows), "new_model_calls": 0}


async def check_faults(output: Path) -> dict:
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = {"source": "mock_transport", "git": checkout_identity(PROJECT_ROOT),
              "cases": {key: case_snapshot(key) for key in CASE_IDS}}
    save_evaluation(frozen, output / "freeze.json")

    def guard():
        if checkout_identity(PROJECT_ROOT) != frozen["git"]:
            raise ValueError("Source changed during ticket checks")

    store, rows = ReportStore(output / "reports"), []
    for key in CASE_IDS:
        draft = case_inputs(key)[6]

        def response(request, content=draft.model_dump_json()):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": content}}], "usage": {
                    "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

        planner = OpenAICompatiblePlanner(ModelSettings(model="offline-ticket-faults",
            base_url="https://offline.invalid/v1", api_key="synthetic-fixture-key"),
            httpx.MockTransport(response), profile="generic")
        rows.extend(await run_case(frozen["cases"][key], planner, store, guard))
    guard()
    return finish_archive(output, frozen, rows)
