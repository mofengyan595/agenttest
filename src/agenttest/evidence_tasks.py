"""Fresh developer tasks with actual dependency/phase differences; labels stay runner-side."""

import copy
import json

from starlette.responses import JSONResponse

from agenttest.feedback_check import inputs
from agenttest.investigation import ReadOnlyCapabilityRegistry
from agenttest.llm_planner import PlannerDraft
from agenttest.portability_demo import task_inputs
from agenttest.task_contract import TaskContract


def evidence_cases(split):
    if split not in {"development", "validation", "end_to_end"}:
        raise ValueError("Unknown fixed split")
    specs = {
        "development": [
            ("access", "courses", "ownership", "ownership", "two_resources"),
            ("access", "tickets", "ownership", "owner_leak", "after_close"),
            ("readback", "courses", "read", "response_name", "two_auth"),
            ("readback", "tickets", "read", "wrong_subject", "list_prefix"),
            ("insufficient", "courses", "read", "create_status", "plain"),
            ("insufficient", "tickets", "close", "close_ignored", "two_resources")],
        "validation": [
            ("access", "courses", "ownership", "ownership", "two_resources"),
            ("access", "courses", "ownership", "ownership", "swapped_two_resources"),
            ("access", "tickets", "ownership", "owner_leak", "after_close"),
            ("access", "tickets", "ownership", "owner_leak", "list_two_resources"),
            ("readback", "courses", "read", "response_name", "two_auth_two_resources"),
            ("readback", "courses", "read", "response_id", "two_auth"),
            ("readback", "tickets", "read", "wrong_subject", "after_close"),
            ("readback", "tickets", "read", "wrong_subject", "list_two_resources"),
            ("insufficient", "courses", "read", "create_status", "two_resources"),
            ("insufficient", "courses", "read", "read_unavailable", "two_auth"),
            ("insufficient", "tickets", "close", "close_ignored", "list_prefix"),
            ("insufficient", "tickets", "read", "transient_subject", "two_resources")],
        "end_to_end": [
            ("healthy", "courses", "ownership", "none", "swapped_two_resources"),
            ("healthy", "tickets", "ownership", "none", "after_close"),
            ("access", "courses", "ownership", "ownership", "two_resources"),
            ("readback", "tickets", "read", "wrong_subject", "after_close"),
            ("insufficient", "courses", "read", "read_unavailable", "two_auth"),
            ("insufficient", "tickets", "read", "transient_subject", "list_prefix")],
    }[split]
    prefix = {"development": "ED", "validation": "EV", "end_to_end": "EE"}[split]
    findings = {"access": "ACCESS_BOUNDARY_VIOLATION", "readback": "PERSISTENT_READBACK_MISMATCH",
                "insufficient": "EVIDENCE_INSUFFICIENT", "healthy": None}
    return [dict(id=f"{prefix}{i:02}", category=category, project=project, task=task,
                 fault=fault, structure=structure, title=f"证据调查{prefix}{i:02}",
                 finding=findings[category])
            for i, (category, project, task, fault, structure) in enumerate(specs, 1)]


def evidence_inputs(case, *, healthy=False):
    fault = "none" if healthy or case["fault"] in {"read_unavailable", "transient_subject"} \
        else case["fault"]
    app, doc, target, registry, env, task, _, capabilities = inputs(dict(case, fault=fault))
    data = task.model_dump(mode="json")
    steps, before = data["requirements"]["steps"], data["requirements"]["before"]
    structure = case["structure"]
    notes = []
    if "swapped" in structure:
        for step in steps:
            step["role"] = {"alice": "bob", "bob": "alice"}.get(step["role"], step["role"])
        data["user_task"] = data["user_task"].replace("alice", "temporary").replace(
            "bob", "alice").replace("temporary", "bob")
    producer = next(s for s in steps if s["operation_id"] in {"createCourse", "openTicket"})
    if "two_auth" in structure:
        role = "bob" if producer["role"] == "alice" else "alice"
        steps.insert(1, dict(key="observer_auth", operation_id="login", role=role,
                             policy_id="nl_login_v1", arguments=[], goals=producer["goals"]))
        notes.append(f"先额外登录观察角色{role}，无需其读取。")
    if "two_resources" in structure and case["project"] == "courses":
        other_role = "bob" if producer["role"] == "alice" else "alice"
        existing = next((s for s in steps if s["operation_id"] == "login"
                         and s["role"] == other_role), None)
        if existing:
            steps.remove(existing)
            steps.insert(1, existing)
        else:
            steps.insert(1, dict(key="decoy_auth", operation_id="login", role=other_role,
                policy_id="nl_login_v1", arguments=[], goals=producer["goals"]))
        decoy = copy.deepcopy(producer)
        decoy["key"] = "decoy"
        decoy["role"] = other_role
        for arg in decoy["arguments"]:
            if arg["name"] in {"name", "subject"}:
                arg["value"] = case["title"] + "前置资源"
        steps.insert(steps.index(producer), decoy)
        before.append(["decoy", producer["key"]])
        notes.append(f"先登录{other_role}并由其创建名为“{case['title']}前置资源”的独立资源，"
                     "后续所有检查只绑定第二个主资源，不读取前置资源。")
    if "two_resources" in structure and case["project"] == "tickets":
        # Existing TaskContract disallows repeated operation/role/policy signatures.
        # Use a distinct authorized phase rather than weaken the intent validator.
        steps.insert(0, dict(key="visitor_list", operation_id="listTickets", role="visitor",
            policy_id="ticket_list_v1", arguments=[], goals=producer["goals"]))
        before.append(["visitor_list", producer["key"]])
        notes.append("开始时先以visitor列出其可见工单，再执行主资源流程。")
    if "list" in structure:
        steps.insert(0, dict(key="initial_list", operation_id="listTickets", role="owner",
            policy_id="ticket_list_v1", arguments=[], goals=producer["goals"]))
        before.append(["initial_list", producer["key"]])
        notes.append("最先以owner列出工单，再开始创建流程。")
    if "after_close" in structure:
        close = task_inputs("tickets", "close")[0].model_dump(mode="json")[
            "requirements"]["steps"][1]
        close["goals"] = producer["goals"]
        steps.insert(steps.index(producer) + 1, close)
        read = next(s for s in steps if s["operation_id"] == "readTicket")
        read["policy_id"] = "ticket_closed_read_v1"
        entries = [c.model_dump(mode="json") for c in capabilities.entries()]
        for entry in entries:
            if entry["policy_id"] == "ticket_read_v1":
                entry["policy_id"] = "ticket_closed_read_v1"
                entry["id"] = "feedback_ticket_closed_read_v1"
        capabilities = ReadOnlyCapabilityRegistry.from_json(json.dumps(entries))
        before.append(["close", read["key"]])
        notes.append("创建后先将主工单关闭为closed，再进行原定读取检查。")
    data["user_task"] += " " + " ".join(notes)
    task = TaskContract.model_validate_json(json.dumps(data, ensure_ascii=False))
    draft = PlannerDraft.model_validate({"name": "evidence " + case["id"], "steps": [
        {"id": s.key, "operation_id": s.operation_id, "policy_id": s.policy_id, "role": s.role,
         "arguments": [{"location": a.location, "name": a.name, "value": a.value,
             "from_step": a.source, "select": a.selector} for a in s.arguments]}
        for s in task.requirements.steps]})
    if not healthy and case["fault"] in {"read_unavailable", "transient_subject"}:
        seen = 0

        @app.middleware("http")
        async def observation_fault(request, call_next):
            nonlocal seen
            response = await call_next(request)
            if request.method != "GET" or response.status_code != 200:
                return response
            if case["fault"] == "read_unavailable" and request.url.path.startswith("/api/courses/"):
                return JSONResponse({"message": "Synthetic read unavailable"}, status_code=503)
            if case["fault"] == "transient_subject" and request.url.path.startswith("/v2/tickets/"):
                seen += 1
                if seen == 1:
                    body = b"".join([chunk async for chunk in response.body_iterator])
                    value = json.loads(body)
                    value["payload"]["subject"] = "Synthetic transient discrepancy"
                    return JSONResponse(value)
            return response
    return app, doc, target, registry, env, task, draft, capabilities
