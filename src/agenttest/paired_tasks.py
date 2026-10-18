"""Fresh development identities over five supported templates; no held-out review claim."""

import json

from agenttest.feedback_check import inputs
from agenttest.llm_planner import PlannerDraft
from agenttest.task_contract import TaskContract


def paired_cases() -> list[dict]:
    cases = []
    groups = {
        "normal": [("courses", "read", "none")] * 3 + [
            ("tickets", "read", "none"), ("tickets", "close", "none"),
            ("tickets", "ownership", "none")],
        "invalid_parameter": [("courses", "read", "none")] * 3
            + [("tickets", "read", "none")] * 3,
        "permission": [("courses", "ownership", fault) for fault in
            ("ownership", "none", "ownership")] + [("tickets", "ownership", fault)
            for fault in ("owner_leak", "none", "owner_leak")],
        "resource_state": [("courses", "read", fault) for fault in
            ("response_name", "response_id", "none")] + [
            ("tickets", "read", "wrong_subject"), ("tickets", "close", "close_ignored"),
            ("tickets", "close", "none")],
    }
    checks = {"ownership": ["getCourse", "http_status", None],
        "owner_leak": ["readTicket", "http_status", None],
        "response_name": ["getCourse", "equals", "$.name"],
        "response_id": ["getCourse", "equals", "$.courseId"],
        "wrong_subject": ["readTicket", "equals", "$.payload.subject"],
        "close_ignored": ["closeTicket", "equals", "$.payload.state"]}
    for category, entries in groups.items():
        for index, (project, task, fault) in enumerate(entries):
            case_id = f"PV{1601 + len(cases)}"
            invalid = category == "invalid_parameter"
            finding = ("ACCESS_BOUNDARY_VIOLATION" if fault in {"ownership", "owner_leak"}
                else "PERSISTENT_READBACK_MISMATCH" if fault in {
                    "response_name", "response_id", "wrong_subject"}
                else "EVIDENCE_INSUFFICIENT" if fault == "close_ignored" else None)
            cases.append({"id": case_id, "category": category, "project": project,
                "task": task, "fault": fault, "title": "配对开发" + case_id,
                "owner": "alice" if project == "courses" else "owner",
                "invalid_kind": ("empty", "too_long", "wrong_type" if project == "courses"
                    else "bad_priority")[index % 3] if invalid else None,
                "expected": "SAFE_REJECTION" if invalid else
                    "ASSERTION_FAILED" if fault != "none" else "PASS",
                "finding": finding, "failed_check": checks.get(fault)})
    return cases


def paired_inputs(case: dict, *, healthy=False):
    selected = {**case, "fault": "none" if healthy else case["fault"]}
    app, doc, target, policies, env, task, draft, capabilities = inputs(selected)
    if case["invalid_kind"]:
        data = task.model_dump(mode="json")
        field = "priority" if case["invalid_kind"] == "bad_priority" else (
            "name" if case["project"] == "courses" else "subject")
        value = {"empty": "", "too_long": "x" * 81, "wrong_type": 42,
                 "bad_priority": "urgent"}[case["invalid_kind"]]
        for step in data["requirements"]["steps"]:
            for arg in step["arguments"]:
                if arg["location"] == "body" and arg["name"] == field:
                    arg["value"] = value
        data["user_task"] += (f" 将 {field} 精确设为 {json.dumps(value)}；"
                              "该异常参数违反批准范围，应在业务请求前拒绝，不要替换成合法值。")
        task = TaskContract.model_validate_json(json.dumps(data))
        draft = PlannerDraft.model_validate({"name": case["id"], "steps": [
            {"id": step.key, "operation_id": step.operation_id, "policy_id": step.policy_id,
             "role": step.role, "arguments": [{"location": arg.location, "name": arg.name,
                "value": arg.value, "from_step": arg.source, "select": arg.selector}
                for arg in step.arguments]} for step in task.requirements.steps]})
    return app, doc, target, policies, env, task, draft, capabilities
