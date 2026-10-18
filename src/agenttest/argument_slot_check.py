"""Fresh slot tasks with frozen inputs, real or simulated planning, and mock business I/O."""

import copy
import json
import secrets
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
from agenttest.json_literal_check import closed, draft_value
from agenttest.llm_planner import GenericPlannerDraft, ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import grade
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract, task_validator

CASE_IDS = ("GA1001", "GA1002", "GA1003")
REFERENCE_IDS = ("GR1101", "GR1102", "GR1103")
DIAGNOSTIC_IDS = ("GD1201", "GD1202", "GD1203")
DATA = PROJECT_ROOT / "examples/argument-slots-development.json"
REFERENCE_DATA = PROJECT_ROOT / "examples/reference-selectors-development.json"
DIAGNOSTIC_DATA = PROJECT_ROOT / "examples/reference-diagnostics-development.json"


def schedule(case_ids):
    if tuple(case_ids) not in {CASE_IDS, REFERENCE_IDS, DIAGNOSTIC_IDS}:
        raise ValueError("Unknown development task set")
    return [(key, 0) for key in case_ids] + [
        (key, round_) for round_ in (1, 2, 3) for key in case_ids]


SCHEDULE = schedule(CASE_IDS)


def value_schema(value):
    if type(value) is dict:
        return closed({name: value_schema(child) for name, child in value.items()})
    if type(value) is list:
        return {"type": "array", "items": value_schema(value[0]), "maxItems": 5}
    return {"type": {str: "string", bool: "boolean", int: "integer", float: "number"}[type(value)]}


def case_inputs(case_id):
    return load_inputs(DATA, CASE_IDS, case_id)


def reference_inputs(case_id):
    return load_inputs(REFERENCE_DATA, REFERENCE_IDS, case_id)


def diagnostic_inputs(case_id):
    return load_inputs(DIAGNOSTIC_DATA, DIAGNOSTIC_IDS, case_id)


def load_inputs(path, case_ids, case_id):
    data = read_json(path)
    if (data["classification"] != "DEVELOPMENT_ONLY" or data["review_status"] != "UNREVIEWED"
            or data["human_reviewed"] != 0 or data["formal_eval_v2_score"] is not None
            or tuple(c["case_id"] for c in data["cases"]) != case_ids):
        raise ValueError("Invalid slot development tasks")
    item = next((case for case in data["cases"] if case["case_id"] == case_id), None)
    if item is None:
        raise ValueError("Unknown slot development task")
    create, read, path_key = item["create"], item["read"], item["path_key"]
    selector = item.get("reference_selector", "$.key")
    custom_response = "reference_response" in item
    path, text = "/v1/" + item["resource"], {"type": "string", "maxLength": 100}
    body_schema = value_schema(item["body"])
    document = json.dumps({"openapi": "3.0.3", "info": {"title": "Slot task", "version": "1"},
        "paths": {path: {"post": {"operationId": create, "requestBody": {"required": True,
            "content": {"application/json": {"schema": body_schema}}},
            "responses": {"201": {"description": "Created"}}}},
            path + "/{" + path_key + "}": {"get": {"operationId": read, "parameters": [
                {"name": path_key, "in": "path", "required": True, "schema": text},
                *[{"name": name, "in": "query", "required": True, "schema": text}
                  for name in item["query"]]],
                "responses": {"200": {"description": "Read"}}}}}}, ensure_ascii=False).encode()
    target = TargetConfig(environment="isolated", allow_mutations=True,
        allowed_operations=[create, read], business_scenario="argument_slots_v1", max_requests=2)
    policies = [{"id": create + "_v1", "operation_id": create, "roles": [None],
        "business_scenario": target.business_scenario, "closed_arguments": True,
        "planning_purpose": "Create one resource preserving each supplied top-level body field.",
        "assertions": {"reviewed": True, "source": "synthetic slot development fixture",
            "status": {"codes": [201]}, "fields": [{"select": selector, "operator": "exists"}]},
        "binding_rules": [{"location": "body", "name": name, "literal_schema": schema,
                           "response_selector": None if custom_response else "$." + name}
                          for name, schema in body_schema["properties"].items()]},
        {"id": read + "_v1", "operation_id": read, "roles": [None],
        "business_scenario": target.business_scenario, "closed_arguments": True,
        "planning_purpose": "Read the same created resource with the requested query fields.",
        "assertions": {"reviewed": True, "source": "synthetic slot development fixture",
                       "status": {"codes": [200]}},
        "binding_rules": [{"location": "path_parameters", "name": path_key,
            "source_policy_id": create + "_v1", "source_selector": selector,
            "role_relation": "anonymous", "response_selector": "$.key",
            "source_body_equalities": {"$." + name: name for name in item["body"]}},
            *[{"location": "query", "name": name, "literal_schema": text,
               "response_selector": "$.query." + name} for name in item["query"]]]}]
    registry = PolicyRegistry.from_json(json.dumps(policies))
    task_text = ("创建一条资源，请求体严格为 " + json.dumps(item["body"], ensure_ascii=False)
        + "；使用创建响应的 " + (selector if custom_response else "key")
        + " 作为路径参数读取同一条，查询参数严格为 "
        + json.dumps(item["query"], ensure_ascii=False) + "，核对所有原始字段和查询参数。")
    arguments = [[{"location": "body", "name": name, "value": value}
                  for name, value in item["body"].items()],
                 [{"location": "path_parameters", "name": path_key, "source": "created",
                   "selector": selector}, *[{"location": "query", "name": name, "value": value}
                                           for name, value in item["query"].items()]]]
    steps = [{"key": key, "operation_id": operation, "role": None, "policy_id": operation + "_v1",
              "arguments": args, "goals": ["preserved"]}
             for key, operation, args in zip(("created", "read"), (create, read), arguments)]
    contract = load_openapi(document)
    task = TaskContract.model_validate({"user_task": task_text,
        "contract_sha256": fingerprint(contract.model_dump()),
        "target_config_sha256": fingerprint(target.model_dump()),
        "policy_sha256": registry.digest(),
        "requirements": {"steps": steps, "required_goals": ["preserved"]}})
    draft = GenericPlannerDraft.model_validate({"name": "fixed slot control", "steps": [
        {"id": step["key"], "operation_id": step["operation_id"], "role": None,
         "policy_id": step["policy_id"], "arguments": [{"location": arg["location"],
             "name": arg["name"], "value": draft_value(arg.get("value")),
             "from_step": arg.get("source"), "select": arg.get("selector")}
             for arg in step["arguments"]]} for step in steps]})
    records = {}

    def envelope(value, key):
        if isinstance(value, dict):
            return {name: envelope(child, key) for name, child in value.items()}
        if isinstance(value, list):
            return [envelope(child, key) for child in value]
        return {"$RESOURCE": key, "$DECOY": "decoy-" + key}.get(value, value)

    def response(request):
        if request.method == "POST" and request.url.path == path:
            key = "slot-" + secrets.token_hex(6)
            records[key] = json.loads(request.content)
            payload = (envelope(item["reference_response"], key) if custom_response
                       else {"key": key, **records[key]})
            return httpx.Response(201, json=payload)
        key = request.url.path.removeprefix(path + "/")
        if request.method == "GET" and key in records:
            return httpx.Response(200, json={"key": key, **copy.deepcopy(records[key]),
                                            "query": dict(request.url.params)})
        raise ValueError("Unapproved mock business request")

    context = planning_context(contract, target, registry, task.user_task,
                               Scenario.model_json_schema())
    return document, target, registry, task, draft, httpx.MockTransport(response), context


def row_from_report(case_id, round_, task, report, plan):
    scored = grade({"expected": "PASS"}, task, report, plan) if plan else {"verified": False}
    identity = (report.contract_sha256 == task.contract_sha256
                and report.target_config_sha256 == task.target_config_sha256
                and report.policy_registry_sha256 == task.policy_sha256)
    return {"case_id": case_id, "round": round_, "run_id": report.run_id,
        "report_sha256": report_digest(report),
        "plan": plan.model_dump(mode="json") if plan else None,
        "verified": bool(scored["verified"] and identity), "outcome": report.outcome.value,
        "reason": report.reason, "requests": sum(len(s.attempts) for s in report.steps),
        "model_calls": report.model_call.calls if report.model_call else 0,
        "total_tokens": report.model_call.total_tokens if report.model_call else None}


def summarize(source, rows, case_ids=CASE_IDS):
    if (source not in {"mock_transport", "real"}
            or [(r["case_id"], r["round"]) for r in rows] != schedule(case_ids)):
        raise ValueError("Incomplete slot task denominator")
    if any(type(r["model_calls"]) is not int or r["model_calls"] not in {0, 1}
           or not r["round"] and r["model_calls"] for r in rows):
        raise ValueError("Invalid slot model count")
    model = [r for r in rows if r["round"]]
    called = [r for r in model if r["model_calls"]]
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "human_reviewed": 0, "formal_eval_v2_score": None, "source": source,
        "business_transport": "mock_transport", "scheduled_controls": 3, "scheduled_trials": 9,
        "controls_passed": sum(r["verified"] for r in rows if not r["round"]),
        "generation_successes": sum(r["verified"] for r in model),
        "per_case": {key: sum(r["verified"] for r in model if r["case_id"] == key)
                     for key in case_ids},
        "model_calls": sum(r["model_calls"] for r in model),
        "real_model_calls": sum(r["model_calls"] for r in model) if source == "real" else 0,
        "not_attempted": sum(r["run_id"] is None for r in rows),
        "usage_status": "SIMULATED" if source == "mock_transport" else
            "OBSERVED" if all(r["total_tokens"] is not None for r in called) else "UNKNOWN",
        "total_tokens": sum(r["total_tokens"] for r in called)
            if all(r["total_tokens"] is not None for r in called) else None,
        "mock_business_requests": sum(r["requests"] for r in rows), "results": rows}


def verify_slots(directory):
    if read_json(directory / "archive.json")["files"] != inventory(directory):
        raise ValueError("Slot archive changed")
    frozen, result = read_json(directory / "freeze.json"), read_json(directory / "results.json")
    case_ids = tuple(frozen.get("case_ids", CASE_IDS))
    if (frozen["schedule"] != [list(position) for position in schedule(case_ids)]
            or frozen["max_calls"] != 9):
        raise ValueError("Changed slot schedule")
    for key in case_ids:
        if fingerprint(read_json(directory / (key + "-request.json"))) != frozen["requests"][key]:
            raise ValueError("Changed frozen request")
    if summarize(frozen["source"], result["results"], case_ids) != result:
        raise ValueError("Slot summary mismatch")
    used = set()
    for row in result["results"]:
        if row["run_id"] is None:
            if row != missing_row(row["case_id"], row["round"], row["reason"]):
                raise ValueError("Invalid unattempted trial")
            continue
        if row["run_id"] in used:
            raise ValueError("Reused slot report")
        used.add(row["run_id"])
        report = ReportStore(directory / "reports").read(row["run_id"])
        task = TaskContract.model_validate(frozen["tasks"][row["case_id"]])
        plan = PlanEvidence.model_validate(row["plan"]) if row["plan"] else None
        if row_from_report(row["case_id"], row["round"], task, report, plan) != row:
            raise ValueError("Slot regrading mismatch")
        if ((not row["round"] and report.model_call is not None)
                or row["round"] and report.model_call is None):
            raise ValueError("Invalid planning source")
        if report.model_call and (report.model_call.calls not in {0, 1} or
                report.model_call.request_sha256 != frozen["requests"][row["case_id"]]):
            raise ValueError("Unfrozen model request")
    return {"archive_verified": True, "rescored_runs": len(result["results"]), "new_model_calls": 0}


def missing_row(key, round_, reason):
    return {"case_id": key, "round": round_, "run_id": None, "report_sha256": None,
            "plan": None, "verified": False, "outcome": "NOT_ATTEMPTED", "reason": reason,
            "requests": 0, "model_calls": 0, "total_tokens": None}


async def check_slots(output: Path, settings: ModelSettings | None = None, *, references=False,
                      diagnostics=False):
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    if references and diagnostics:
        raise ValueError("Choose one fresh development set")
    if settings and (settings.model != "ecnu-plus" or settings.max_input_tokens > 20_000
                     or settings.max_output_tokens > 4096 or settings.max_total_tokens > 25_000):
        raise ValueError("This development run only authorizes ecnu-plus")
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    source = "real" if settings else "mock_transport"
    selected = settings or ModelSettings(model="offline-slots", base_url="https://offline.invalid/v1",
                                         api_key="synthetic-slot-credential")
    case_ids, factory = (REFERENCE_IDS, reference_inputs) if references else (CASE_IDS, case_inputs)
    if diagnostics:
        case_ids, factory = DIAGNOSTIC_IDS, diagnostic_inputs
    positions = schedule(case_ids)
    frozen = {"source": source, "git": checkout_identity(PROJECT_ROOT), "tasks": {}, "requests": {},
              "schedule": positions, "max_calls": 9,
              "reserved_token_ceiling": 9 * (
                  selected.max_input_tokens + selected.max_output_tokens)}
    if references or diagnostics:
        frozen["case_ids"] = list(case_ids)
    for key in case_ids:
        *_, task, draft, transport, context = factory(key)
        payload = OpenAICompatiblePlanner(selected, profile="generic").prepare_request(context)
        frozen["tasks"][key] = task.model_dump(mode="json")
        frozen["requests"][key] = fingerprint(json.loads(payload))
        save_evaluation(json.loads(payload), output / (key + "-request.json"))
    save_evaluation(frozen, output / "freeze.json")

    def guard():
        if checkout_identity(PROJECT_ROOT) != frozen["git"]:
            raise ValueError("Source changed during slot check")

    rows, stop, store = [], None, ReportStore(output / "reports")
    for key, round_ in positions:
        if round_ and stop:
            rows.append(missing_row(key, round_, stop))
            continue
        guard()
        document, target, registry, task, draft, transport, context = factory(key)
        if not round_:
            delegate = FakeLLM(draft.scenario().model_dump_json())
        else:
            def response(request, content=draft.model_dump_json()):
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                    "message": {"content": content}}], "usage": {
                        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

            delegate = OpenAICompatiblePlanner(selected,
                None if settings else httpx.MockTransport(response), profile="generic")
        planner = FrozenContextPlanner(delegate, context, guard)
        report = await Workflow(document, target, registry, planner, store, transport, {},
            WorkflowBudget(model_timeout=65, workflow_timeout=120), user_task=task.user_task,
            intent_validator=task_validator(task, load_openapi(document), target, registry),
            execution_guard=guard).run()
        proof = capture_plan(planner.plan) if planner.plan else None
        row = row_from_report(key, round_, task, report, proof)
        rows.append(row)
        save_evaluation(row, output / (f"position-{len(rows):02}.json"))
        planner.output = None
        if not round_ and not row["verified"]:
            stop = "FIXED_CONTROL_FAILED"
        if round_ and (report.outcome == Outcome.TEST_INFRA_ERROR or row["total_tokens"] is None):
            stop = "MODEL_INFRASTRUCTURE_OR_UNKNOWN_USAGE"
    guard()
    result = summarize(source, rows, case_ids)
    save_evaluation(result, output / "results.json")
    summary = {k: v for k, v in result.items() if k != "results"}
    (output / "summary.md").write_text("# 参数名约束开发验收\n\n" + json.dumps(
        summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal(output)
    verify_slots(output)
    return summary
