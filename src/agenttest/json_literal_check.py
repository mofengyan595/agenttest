"""Disposable JSON request fixtures and development evidence; public runs stay offline."""

import copy
import json
import secrets
from pathlib import Path

import httpx

from agenttest.composed_planning import FrozenContextPlanner
from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.models import PlanEvidence
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import GenericPlannerDraft, ModelSettings, OpenAICompatiblePlanner
from agenttest.policies import PolicyRegistry
from agenttest.report import ReportStore, fingerprint
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract, task_validator
from agenttest.ticket_fault_check import PHASES, report_row, unattempted

DATA = PROJECT_ROOT / "examples/json-literals-development.json"
CASE_IDS = ("J801", "J802", "J803")
REPAIR_CASE_IDS = ("J804", "J805")


def closed(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "additionalProperties": False,
            "required": list(properties) if required is None else required}


TEXT = {"type": "string", "maxLength": 80}
TAGS = {"type": "array", "items": TEXT, "maxItems": 4}
BODY = closed({"label": TEXT, "config": closed({"enabled": {"type": "boolean"},
    "note": {**TEXT, "nullable": True}, "bounds": closed({"lower": {"type": "number"},
    "upper": {"type": "number"}}), "tags": TAGS}, ["enabled", "tags"]),
    "items": {"type": "array", "maxItems": 4, "items": closed({"sku": TEXT,
        "qty": {"type": "integer", "minimum": 1},
        "unit_cost": {"type": "number", "minimum": 0},
        "flags": closed({"gift": {"type": "boolean"}, "labels": TAGS})})},
    "ratio": {"type": "number", "minimum": -1, "maximum": 3}})


def draft_value(value):
    if isinstance(value, dict):
        return {"object": [{"key": k, "value": draft_value(v)} for k, v in value.items()]}
    if isinstance(value, list):
        return {"array": [draft_value(v) for v in value]}
    return value


def case_inputs(case_id: str, broken: bool = False):
    """Only this operator-owned factory can select a fault or create a mock transport."""
    repairing = case_id in REPAIR_CASE_IDS
    data = read_json(PROJECT_ROOT / "examples/json-literals-repair-development.json"
                     if repairing else DATA)
    if (data["classification"] != "DEVELOPMENT_ONLY" or data["review_status"] != "UNREVIEWED"
            or data["human_reviewed"] != 0 or data["formal_eval_v2_score"] is not None
            or tuple(c["case_id"] for c in data["cases"])
            != (REPAIR_CASE_IDS if repairing else CASE_IDS)):
        raise ValueError("Invalid JSON literal development scope")
    item = next((c for c in data["cases"] if c["case_id"] == case_id), None)
    if item is None:
        raise ValueError("Unknown JSON literal case")
    document = json.dumps({"openapi": "3.0.3", "info": {"title": "Payload drafts", "version": "1"},
        "paths": {"/v3/drafts": {"post": {"operationId": "createDraft",
            "requestBody": {"required": True, "content": {"application/json": {"schema": BODY}}},
            "responses": {"201": {"description": "Created"}}}},
            "/v3/drafts/{draft_key}": {"get": {"operationId": "readDraft", "parameters": [
                {"name": "draft_key", "in": "path", "required": True, "schema": TEXT}],
                "responses": {"200": {"description": "Read"}}}}}}, ensure_ascii=False).encode()
    target = TargetConfig(environment="isolated", allow_mutations=True,
        allowed_operations=["createDraft", "readDraft"], business_scenario="json_literals_v1",
        max_requests=4)
    source = "operator-owned synthetic JSON development policy"
    policies = [{"id": "draft_create_v1", "operation_id": "createDraft", "roles": [None],
        "business_scenario": target.business_scenario, "closed_arguments": True,
        "planning_purpose": "Create one draft and verify every supplied body field is preserved.",
        "assertions": {"reviewed": True, "source": source, "status": {"codes": [201]},
            "fields": [{"select": "$.payload.key", "operator": "exists"}]},
        "binding_rules": [{"location": "body", "name": name, "literal_schema": schema,
                           "response_selector": "$.payload." + name}
                          for name, schema in BODY["properties"].items()]},
        {"id": "draft_read_v1", "operation_id": "readDraft", "roles": [None],
        "business_scenario": target.business_scenario, "closed_arguments": True,
        "planning_purpose": "Read the created draft key and verify every original field.",
        "assertions": {"reviewed": True, "source": source, "status": {"codes": [200]}},
        "binding_rules": [{"location": "path_parameters", "name": "draft_key",
            "source_policy_id": "draft_create_v1", "source_selector": "$.payload.key",
            "role_relation": "anonymous", "response_selector": "$.payload.key",
            "source_body_equalities": {"$.payload." + name: name for name in BODY["properties"]}}]}]
    registry = PolicyRegistry.from_json(json.dumps(policies))
    user_task = ("创建一份草稿，请求体为以下 JSON：" + json.dumps(item["body"], ensure_ascii=False)
                 + "。用创建响应返回的 key 读取同一份草稿，核对四个字段与输入完全一致。")
    arguments = [{"location": "body", "name": k, "value": v} for k, v in item["body"].items()]
    task = TaskContract.model_validate_json(json.dumps({"user_task": user_task,
        "contract_sha256": fingerprint(load_openapi(document).model_dump()),
        "target_config_sha256": fingerprint(target.model_dump()),
        "policy_sha256": registry.digest(),
        "requirements": {"steps": [{"key": "create", "operation_id": "createDraft", "role": None,
            "policy_id": "draft_create_v1", "arguments": arguments, "goals": ["preserved"]},
            {"key": "read", "operation_id": "readDraft", "role": None,
             "policy_id": "draft_read_v1", "goals": ["preserved"], "arguments": [
                 {"location": "path_parameters", "name": "draft_key", "source": "create",
                  "selector": "$.payload.key"}]}], "required_goals": ["preserved"]}}))
    draft = GenericPlannerDraft.model_validate({"name": "fixed JSON literal fixture", "steps": [
        {"id": "create", "operation_id": "createDraft", "role": None,
         "policy_id": "draft_create_v1", "arguments": [
             {**a, "value": draft_value(a["value"]), "from_step": None, "select": None}
             for a in arguments]}, {"id": "read", "operation_id": "readDraft", "role": None,
            "policy_id": "draft_read_v1", "arguments": [{"location": "path_parameters",
                "name": "draft_key", "value": None, "from_step": "create",
                "select": "$.payload.key"}]}]})
    records = {}

    def response(request):
        if request.method == "POST" and request.url.path == "/v3/drafts":
            key = "draft-" + secrets.token_hex(6)
            records[key] = json.loads(request.content)
            return httpx.Response(201, json={"payload": {"key": key, **records[key]}})
        if request.method == "GET" and request.url.path.startswith("/v3/drafts/"):
            key = request.url.path.rsplit("/", 1)[-1]
            result = {"key": key, **copy.deepcopy(records[key])}
            if broken:
                if item["fault"] == "nested_type":
                    result["config"]["enabled"] = 1
                elif item["fault"] == "array_order":
                    result["items"].reverse()
                elif item["fault"] == "decimal_value":
                    result["ratio"] = 0.375
            return httpx.Response(200, json={"payload": result})
        raise ValueError("Unapproved mock request")

    return document, target, registry, {}, httpx.MockTransport(response), task, draft, item


def case_snapshot(case_id: str) -> dict:
    document, target, registry, _, _, task, _, item = case_inputs(case_id)
    return {"case_id": case_id, "task": task.model_dump(mode="json"),
            "contract_sha256": fingerprint(load_openapi(document).model_dump()),
            "target_config_sha256": fingerprint(target.model_dump()),
            "policy_sha256": registry.digest(),
            "failed_check": ["readDraft", "equals", item["failed_selector"]]}


def graded_row(snapshot, phase, report, plan, anchor):
    row = report_row(snapshot, phase, report, plan, anchor)
    identity = (report.contract_sha256 == snapshot["contract_sha256"]
                and report.target_config_sha256 == snapshot["target_config_sha256"]
                and report.policy_registry_sha256 == snapshot["policy_sha256"])
    for field in ("verified", "normal_completed", "authorized_execution"):
        row[field] = bool(row[field] and identity)
    return row


async def run_case(snapshot, delegate, store, guard):
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    scenario, anchor, rows = None, None, []
    for phase in PHASES:
        if phase != "generation" and scenario is None:
            rows.append(unattempted(snapshot["case_id"], phase, "GENERATION_NOT_COMPLETED"))
            continue
        guard()
        document, target, registry, environ, transport, task, _, _ = case_inputs(
            snapshot["case_id"], phase == "fault")
        contract = load_openapi(document)
        from agenttest.planner_input import planning_context

        context = planning_context(contract, target, registry, task.user_task,
                                   Scenario.model_json_schema())
        planner = FrozenContextPlanner(delegate if phase == "generation" else
            FakeLLM(scenario.model_dump_json()), context, guard)
        report = await Workflow(document, target, registry, planner, store, transport, environ,
            WorkflowBudget(model_timeout=65, workflow_timeout=120), user_task=task.user_task,
            intent_validator=task_validator(task, contract, target, registry),
            execution_guard=guard).run()
        proof = capture_plan(planner.plan) if planner.plan else None
        row = graded_row(snapshot, phase, report, proof, anchor)
        rows.append(row)
        if phase == "generation" and row["verified"]:
            scenario, anchor = planner.plan, proof.scenario_sha256
        planner.output = None
    return rows


def summarize(source, rows, case_ids=CASE_IDS):
    if (case_ids not in {CASE_IDS, REPAIR_CASE_IDS} or source not in {"mock_transport", "real"}
            or [(r["case_id"], r["phase"]) for r in rows]
            != [(key, phase) for key in case_ids for phase in PHASES]):
        raise ValueError("Incomplete JSON literal denominator")
    if any(type(r["model_calls"]) is not int or r["model_calls"] not in {0, 1}
           or r["phase"] != "generation" and r["model_calls"]
           for r in rows):
        raise ValueError("Invalid JSON literal model count")
    generations, controls, faults = [[r for r in rows if r["phase"] == phase] for phase in PHASES]
    calls = [r for r in generations if r["model_calls"]]
    return {"source": source, "classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "human_reviewed": 0, "formal_eval_v2_score": None, "scheduled_tasks": len(case_ids),
        "generation_successes": sum(r["verified"] for r in generations),
        "controls_passed": sum(r["verified"] for r in controls),
        "paired_detections": sum(c["verified"] and f["verified"] for c, f in zip(controls, faults)),
        "false_positives": sum(r["authorized_execution"] and r["outcome"] == "ASSERTION_FAILED"
                               for r in controls),
        "false_negatives": sum(r["normal_completed"] for r in faults),
        "inconclusive_controls": sum(not r["normal_completed"] and not (
            r["authorized_execution"] and r["outcome"] == "ASSERTION_FAILED") for r in controls),
        "inconclusive_faults": sum(not r["verified"] and not r["normal_completed"] for r in faults),
        "not_executed": sum(r["run_id"] is None for r in rows),
        "model_calls": sum(r["model_calls"] for r in calls),
        "real_model_calls": sum(r["model_calls"] for r in calls) if source == "real" else 0,
        "usage_status": "SIMULATED" if source == "mock_transport" else
            "OBSERVED" if all(r["total_tokens"] is not None for r in calls) else "UNKNOWN",
        "total_tokens": sum(r["total_tokens"] for r in calls)
            if all(r["total_tokens"] is not None for r in calls) else None, "results": rows}


def finish_archive(output, frozen, rows):
    result = summarize(frozen["source"], rows, tuple(frozen.get("case_ids", CASE_IDS)))
    save_evaluation(result, output / "results.json")
    summary = {k: v for k, v in result.items() if k != "results"}
    (output / "summary.md").write_text("# JSON literal development check\n\n" + json.dumps(
        summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal(output)
    verify_literals(output)
    return summary


def verify_literals(directory):
    if read_json(directory / "archive.json")["files"] != inventory(directory):
        raise ValueError("JSON literal archive changed")
    frozen, result = read_json(directory / "freeze.json"), read_json(directory / "results.json")
    if summarize(frozen["source"], result["results"],
                 tuple(frozen.get("case_ids", CASE_IDS))) != result:
        raise ValueError("JSON literal summary mismatch")
    anchors, used = {}, set()
    for row in result["results"]:
        key, phase = row["case_id"], row["phase"]
        if row["run_id"] is None:
            if row != unattempted(key, phase, row["reason"]):
                raise ValueError("Invalid unattempted JSON trial")
            continue
        if row["run_id"] in used:
            raise ValueError("Reused JSON literal report")
        used.add(row["run_id"])
        report = ReportStore(directory / "reports").read(row["run_id"])
        plan = PlanEvidence.model_validate(row["plan"]) if row["plan"] else None
        if graded_row(frozen["cases"][key], phase, report, plan, anchors.get(key)) != row:
            raise ValueError("JSON literal regrading mismatch")
        if phase == "generation" and row["verified"]:
            anchors[key] = plan.scenario_sha256
    return {"archive_verified": True, "rescored_runs": len(result["results"]), "new_model_calls": 0}


async def check_literals(output: Path, repair: bool = False):
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    case_ids = REPAIR_CASE_IDS if repair else CASE_IDS
    frozen = {"source": "mock_transport", "git": checkout_identity(PROJECT_ROOT),
              "case_ids": list(case_ids), "cases": {key: case_snapshot(key) for key in case_ids}}
    save_evaluation(frozen, output / "freeze.json")

    def guard():
        if checkout_identity(PROJECT_ROOT) != frozen["git"]:
            raise ValueError("Source changed during JSON literal checks")

    rows, store = [], ReportStore(output / "reports")
    for key in case_ids:
        draft = case_inputs(key)[6]

        def response(request, content=draft.model_dump_json()):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": content}}], "usage": {
                    "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

        planner = OpenAICompatiblePlanner(ModelSettings(model="offline-json-literals",
            base_url="https://offline.invalid/v1", api_key="synthetic-fixture-key"),
            httpx.MockTransport(response), profile="generic")
        rows.extend(await run_case(frozen["cases"][key], planner, store, guard))
    guard()
    return finish_archive(output, frozen, rows)
