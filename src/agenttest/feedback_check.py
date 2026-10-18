"""Fresh feedback development cases; fixed controls, frozen source and cold regrading."""

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
from agenttest.evaluation_freeze import public_model_config
from agenttest.feedback import FEEDBACK_PROMPT, FeedbackAction, FeedbackModel
from agenttest.investigation import FeedbackBudget, InvestigationConfig, ReadOnlyCapabilityRegistry
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerDraft
from agenttest.planner_input import planning_context
from agenttest.portability_demo import grade, project_inputs, task_inputs
from agenttest.report import ReportStore, fingerprint
from agenttest.task_contract import TaskContract, task_validator

CASES = [
    {"id": "FC1401", "project": "courses", "task": "ownership", "fault": "ownership",
     "title": "反馈闭环课程归属", "owner": "alice", "finding": "ACCESS_BOUNDARY_VIOLATION",
     "failed_check": ["getCourse", "http_status", None]},
    {"id": "FC1402", "project": "tickets", "task": "ownership", "fault": "owner_leak",
     "title": "反馈闭环工单归属", "owner": "owner", "finding": "ACCESS_BOUNDARY_VIOLATION",
     "failed_check": ["readTicket", "http_status", None]},
    {"id": "FC1403", "project": "courses", "task": "read", "fault": "response_name",
     "title": "反馈闭环课程回读", "owner": "alice", "finding": "PERSISTENT_READBACK_MISMATCH",
     "failed_check": ["getCourse", "equals", "$.name"]},
]


def inputs(case):
    app, document, target, registry, environ = project_inputs(case["project"], case["fault"])
    old, _ = task_inputs(case["project"], case["task"])
    task_data = old.model_dump(mode="json")
    for step in task_data["requirements"]["steps"]:
        for argument in step["arguments"]:
            if argument["location"] == "body" and argument["name"] in {"name", "subject"}:
                task_data["user_task"] = task_data["user_task"].replace(
                    argument["value"], case["title"])
                argument["value"] = case["title"]
    task = TaskContract.model_validate_json(json.dumps(task_data))
    draft = PlannerDraft.model_validate({"name": "fresh feedback " + case["id"], "steps": [
        {"id": step.key, "operation_id": step.operation_id, "policy_id": step.policy_id,
         "role": step.role, "arguments": [{"location": argument.location,
             "name": argument.name, "value": argument.value, "from_step": argument.source,
             "select": argument.selector} for argument in step.arguments]}
        for step in task.requirements.steps]})
    selected = ({"nl_read_v1", "nl_forbidden_v1"} if case["project"] == "courses"
                else {"ticket_read_v1", "ticket_denied_v1"})
    capabilities = []
    for policy in registry.definitions():
        if policy.id not in selected:
            continue
        rule = policy.binding_rules[0]
        capabilities.append({"id": "feedback_" + policy.id, "operation_id": policy.operation_id,
            "policy_id": policy.id, "roles": policy.roles,
            "business_scenario": target.business_scenario,
            "source_policy_id": rule.source_policy_id, "source_selector": rule.source_selector,
            "path_parameter": rule.name, "effect": "read_only",
            "no_side_effect_basis": "Reviewed get_course and read_ticket: "
                "dictionary lookup and ownership check only, no writes or state advancement."})
    return (app, document, target, registry, environ, task, draft,
            ReadOnlyCapabilityRegistry.from_json(json.dumps(capabilities)))


def stats_for(report):
    stats = [report.model_call] if report.model_call else []
    if report.feedback:
        stats += [decision.model_call for decision in report.feedback.decisions
                  if decision.model_call is not None]
    return stats


def row(case, phase, task, report, plan):
    business = bool(plan and grade({"expected": "ASSERTION_FAILED",
        "failed_check": case["failed_check"]}, task, report, plan)["verified"])
    feedback = report.feedback
    supported = False
    if feedback and plan and report.steps:
        from agenttest.feedback_runtime import supported_finding

        failed = report.steps[-1]
        roles = {step.step_id: step.role for step in plan.steps}
        conclusion = FeedbackAction(action="finish", selection=None, finding=feedback.finding,
                                    evidence_refs=feedback.evidence_refs)
        supported = supported_finding(conclusion, failed, roles[failed.step_id],
                                      report.investigations, roles)
    stats = stats_for(report)
    called = [stat for stat in stats if stat.calls]
    known = all(stat.total_tokens is not None for stat in called)
    return {"case_id": case["id"], "phase": phase, "run_id": report.run_id,
        "report_sha256": report_digest(report),
        "plan": plan.model_dump(mode="json") if plan else None,
        "business_correct": business,
        "finding_correct": bool(feedback and feedback.state == "SUPPORTED"
                                and feedback.finding == case["finding"] and business and supported),
        "outcome": report.outcome.value,
        "feedback_state": feedback.state if feedback else None,
        "feedback_reason": feedback.reason if feedback else None,
        "model_calls": sum(stat.calls for stat in stats),
        "total_tokens": sum(stat.total_tokens for stat in called) if known else None,
        "usage_known": known, "requests": sum(len(step.attempts) for step in report.steps),
        "investigation_requests": sum(len(step.attempts) for step in report.investigations)}


def summary(rows, source):
    trials = [item for item in rows if item["phase"] == "model"]
    called = [item for item in trials if item.get("run_id")]
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "human_reviewed": 0, "formal_eval_v2_score": None, "source": source,
        "scheduled_controls": 3, "scheduled_trials": 3,
        "controls_passed": sum(item.get("business_correct", False) and
                               item.get("finding_correct", False)
                               for item in rows if item["phase"] == "control"),
        "business_correct": sum(item.get("business_correct", False) for item in trials),
        "finding_correct": sum(item.get("finding_correct", False) for item in trials),
        "not_attempted": sum(not item.get("run_id") for item in trials),
        "model_calls": sum(item.get("model_calls", 0) for item in trials),
        "real_model_calls": sum(item.get("model_calls", 0) for item in trials) if source == "real"
            else 0,
        "total_tokens": sum(item["total_tokens"] for item in called)
            if all(item["usage_known"] for item in called) else None,
        "usage_status": "SIMULATED" if source == "mock_transport" else
            "OBSERVED" if all(item["usage_known"] for item in called) else "UNKNOWN",
        "mock_requests": sum(item.get("requests", 0) + item.get("investigation_requests", 0)
                             for item in rows)}


async def check_feedback(output: Path, settings: ModelSettings | None = None):
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    source = "real" if settings else "mock_transport"
    selected = settings or ModelSettings(model="offline-feedback", base_url="https://offline.invalid/v1",
        api_key="synthetic-feedback-only")
    if settings and (settings.model != "ecnu-plus" or settings.protocol != "chatecnu"
                     or settings.base_url != "https://chat.ecnu.edu.cn/open/api/v1"):
        raise ValueError("Use the existing operator-selected provider only")
    selected = ModelSettings.model_validate({**selected.model_dump(), "timeout": 60.0})
    authority = InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=180.0))
    identity = checkout_identity(PROJECT_ROOT)
    if settings and identity["worktree_status"]:
        raise ValueError("Live feedback requires a clean committed checkout")
    frozen = {"git": identity, "source": source, "cases": CASES,
        "tasks": {}, "contexts": {}, "authority": authority.model_dump(mode="json"),
        "capabilities": {}, "model": public_model_config(selected),
        "feedback_prompt_sha256": fingerprint(FEEDBACK_PROMPT),
        "feedback_schema_sha256": fingerprint(FeedbackAction.model_json_schema()),
        "max_calls": 12, "max_tokens": 12 * (
            selected.max_input_tokens + selected.max_output_tokens)}
    for case in CASES:
        _, document, target, registry, _, task, _, capabilities = inputs(case)
        frozen["tasks"][case["id"]] = task.model_dump(mode="json")
        frozen["contexts"][case["id"]] = planning_context(
            load_openapi(document), target, registry, task.user_task, Scenario.model_json_schema())
        frozen["capabilities"][case["id"]] = [c.model_dump() for c in capabilities.entries()]
    save_evaluation(frozen, output / "freeze.json")

    def guard():
        if checkout_identity(PROJECT_ROOT) != frozen["git"]:
            raise ValueError("Feedback source changed")

    rows, stop = [], None
    for phase in ("control", "model"):
        for case in CASES:
            if phase == "model" and stop:
                rows.append({"case_id": case["id"], "phase": phase, "run_id": None,
                             "not_attempted_reason": stop})
                continue
            guard()
            app, document, target, registry, environ, task, draft, capabilities = inputs(case)

            def response(request, draft=draft):
                payload = json.loads(request.content)
                if payload["response_format"]["json_schema"]["name"] == "agenttest_plan":
                    content = draft.model_dump(mode="json")
                else:
                    context = json.loads(payload["messages"][1]["content"])
                    if not context["evidence"] and not context["finish_required"]:
                        choice = next(c for c in context["choices"] if c["role"] == case["owner"])
                        content = {"action": "investigate", "selection": choice,
                                   "finding": None, "evidence_refs": []}
                    else:
                        content = {"action": "finish", "selection": None,
                            "finding": case["finding"], "evidence_refs": [
                                "original", context["evidence"][0]["ref"]]}
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                    "message": {"content": json.dumps(content)}}], "usage": {
                        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

            mocked = phase == "control" or settings is None
            transport = httpx.MockTransport(response) if mocked else None
            delegate = (FakeLLM(draft.scenario().model_dump_json()) if phase == "control" else
                        OpenAICompatiblePlanner(selected, transport, profile="generic"))
            planner = FrozenContextPlanner(delegate, frozen["contexts"][case["id"]], guard)
            store = ReportStore(output / "reports")
            report = await Workflow(document, target, registry, planner, store,
                httpx.ASGITransport(app), environ, WorkflowBudget(model_timeout=65,
                    workflow_timeout=300), user_task=task.user_task,
                intent_validator=task_validator(task, load_openapi(document), target, registry),
                investigation=authority, capabilities=capabilities,
                analyzer=FeedbackModel(selected, transport), execution_guard=guard).run()
            proof = capture_plan(planner.plan) if planner.plan else None
            item = row(case, phase, task, report, proof)
            rows.append(item)
            save_evaluation(item, output / (f"position-{len(rows):02}.json"))
            if phase == "control" and not (item["business_correct"] and item["finding_correct"]):
                stop = "FIXED_CONTROL_FAILED"
            if phase == "model" and (not item["usage_known"] or report.outcome.value in {
                    "TEST_INFRA_ERROR", "TOOL_ERROR"} or report.feedback and
                    report.feedback.state in {"TOOL_ERROR", "TEST_INFRA_ERROR",
                                              "TIMEOUT", "CANCELLED"}):
                stop = "MODEL_OR_TOOL_ERROR_STOP"
            planner.output = None
    guard()
    result = {**summary(rows, source), "results": rows}
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text("# 有界反馈开发验收\n\n" + json.dumps(
        {key: value for key, value in result.items() if key != "results"},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal(output)
    verify_feedback(output)
    return {key: value for key, value in result.items() if key != "results"}


def verify_feedback(output: Path):
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("Feedback archive changed")
    frozen, result = read_json(output / "freeze.json"), read_json(output / "results.json")
    if (frozen["source"] not in {"real", "mock_transport"} or frozen["max_calls"] != 12
            or [case["id"] for case in frozen["cases"]] != [case["id"] for case in CASES]):
        raise ValueError("Feedback protocol changed")
    expected = [(phase, case["id"]) for phase in ("control", "model") for case in frozen["cases"]]
    rows = result["results"]
    if [(item["phase"], item["case_id"]) for item in rows] != expected:
        raise ValueError("Feedback denominator changed")
    seen = set()
    for item in rows:
        if item["run_id"] is None:
            if set(item) != {"case_id", "phase", "run_id", "not_attempted_reason"}:
                raise ValueError("Invalid missing feedback position")
            continue
        if item["run_id"] in seen:
            raise ValueError("Reused feedback report")
        seen.add(item["run_id"])
        case = next(case for case in frozen["cases"] if case["id"] == item["case_id"])
        report = ReportStore(output / "reports").read(item["run_id"])
        task = TaskContract.model_validate_json(json.dumps(frozen["tasks"][case["id"]]))
        plan = PlanEvidence.model_validate(item["plan"]) if item["plan"] else None
        if row(case, item["phase"], task, report, plan) != item:
            raise ValueError("Feedback regrading mismatch")
        if item["model_calls"] > 4 or item["investigation_requests"] > 2:
            raise ValueError("Feedback run exceeded budget")
    if {**summary(rows, frozen["source"]), "results": rows} != result:
        raise ValueError("Feedback summary mismatch")
    return {"archive_verified": True, "rescored_runs": len(rows), "new_model_calls": 0}
