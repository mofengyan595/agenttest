"""Disposable two-domain flows; task answers stay outside the planner boundary.

This fixture module is loaded by path in both isolated engine processes. It uses
only APIs available in the frozen baseline. Public purpose configuration is shared.
"""

import copy
import json
from pathlib import Path

from starlette.responses import JSONResponse

from agenttest.investigation import ReadOnlyCapabilityRegistry
from agenttest.llm_planner import PlannerDraft
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import project_inputs, task_inputs
from agenttest.task_contract import TaskContract

ROOT = Path(__file__).resolve().parents[2]


def cases(split):
    if split not in {"development", "validation", "demo"}:
        raise ValueError("Unknown new-task split")
    return json.loads((ROOT / "examples/nl-reliability" / (split + ".json")).read_bytes())


def task_inputs_for(case, *, healthy=False):
    special = case["fault"] in {"read_unavailable", "transient_subject"}
    fault = "none" if healthy or special else case["fault"]
    app, doc, target, registry, env = project_inputs(case["project"], fault)
    purposes = json.loads((ROOT / "examples/nl-reliability/policy-purposes.json").read_bytes())
    definitions = registry.definitions()
    for policy in definitions:
        policy.planning_purpose = purposes[policy.id]
    registry = PolicyRegistry(definitions)
    base, _ = task_inputs(case["project"], "read")
    data = base.model_dump(mode="json")
    if case["project"] == "courses":
        ownership = task_inputs("courses", "ownership")[0].model_dump(mode="json")
        originals = {s["key"]: s for s in ownership["requirements"]["steps"]}
        templates = {}
        for suffix, role, other, title in (
            ("a", case["roles"][0], case["roles"][1], case["names"][0]),
            ("b", case["roles"][1], case["roles"][0], case["names"][1]),
        ):
            for name, source in (("auth", "auth"), ("create", "create"),
                                 ("read", "read"), ("deny", "denied")):
                step = copy.deepcopy(originals[source])
                step["key"] = name + "_" + suffix
                step["role"] = other if name == "deny" else role
                for argument in step["arguments"]:
                    if argument["source"]:
                        argument["source"] = "create_" + suffix
                    elif argument["name"] == "name":
                        argument["value"] = title
                templates[step["key"]] = step
    else:
        read = base.model_dump(mode="json")["requirements"]["steps"]
        close = task_inputs("tickets", "close")[0].model_dump(mode="json")[
            "requirements"]["steps"]
        ownership = task_inputs("tickets", "ownership")[0].model_dump(mode="json")
        denied = next(s for s in ownership["requirements"]["steps"]
                      if s["policy_id"] == "ticket_denied_v1")
        templates = {"open": copy.deepcopy(read[0]), "read_open": copy.deepcopy(read[1]),
                     "close": copy.deepcopy(close[1]), "read_closed": copy.deepcopy(close[2]),
                     "denied": copy.deepcopy(denied)}
        for key, step in templates.items():
            step["key"] = key
            for argument in step["arguments"]:
                if argument["source"]:
                    argument["source"] = "open"
                elif argument["name"] == "subject":
                    argument["value"] = case["names"][0]
                elif argument["name"] == "priority":
                    argument["value"] = case.get("priority", "high")
        for role in ("owner", "visitor"):
            key = "list_" + role
            templates[key] = dict(key=key, operation_id="listTickets", role=role,
                                 policy_id="ticket_list_v1", arguments=[], goals=[])
    steps = [copy.deepcopy(templates[key]) for key in case["sequence"]]
    for step in steps:
        step["goals"] = ["requested_flow"]
    data.update(user_task=case["user_task"], policy_sha256=registry.digest(),
        requirements=dict(steps=steps, before=list(zip(case["sequence"], case["sequence"][1:])),
                          required_goals=["requested_flow"]))
    task = TaskContract.model_validate_json(json.dumps(data, ensure_ascii=False))
    draft = PlannerDraft.model_validate(dict(name="offline mechanism fixture", steps=[
        dict(id=s.key, operation_id=s.operation_id, role=s.role, policy_id=s.policy_id,
             arguments=[dict(location=a.location, name=a.name, value=a.value,
                             from_step=a.source, select=a.selector) for a in s.arguments])
        for s in task.requirements.steps]))
    caps = []
    for policy in registry.definitions():
        if policy.operation_id not in {"getCourse", "readTicket"}:
            continue
        rule = policy.binding_rules[0]
        caps.append(dict(id="feedback_" + policy.id, operation_id=policy.operation_id,
            policy_id=policy.id, roles=policy.roles, business_scenario=target.business_scenario,
            source_policy_id=rule.source_policy_id, source_selector=rule.source_selector,
            path_parameter=rule.name, effect="read_only",
            no_side_effect_basis="Reviewed disposable ASGI GET handlers only; no database writes."))
    capabilities = ReadOnlyCapabilityRegistry.from_json(json.dumps(caps))
    if special and not healthy:
        seen = 0

        @app.middleware("http")
        async def observation_fault(request, call_next):
            nonlocal seen
            response = await call_next(request)
            if request.method != "GET" or response.status_code != 200:
                return response
            if case["fault"] == "read_unavailable" and request.url.path.startswith("/api/courses/"):
                return JSONResponse({"message": "Synthetic unavailable read"}, status_code=503)
            if case["fault"] == "transient_subject" and request.url.path.startswith("/v2/tickets/"):
                seen += 1
                if seen == 1:
                    body = b"".join([chunk async for chunk in response.body_iterator])
                    value = json.loads(body)
                    value["payload"]["subject"] = "Synthetic transient mismatch"
                    return JSONResponse(value)
            return response
    return app, doc, target, registry, env, task, draft, capabilities
