"""New development fixtures for public policy purposes; no real-call entry point."""

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

DATA = PROJECT_ROOT / "examples/planning-metadata-development.json"
CASE_IDS = ("G601", "G602", "G603", "G604")


def schedule():
    return [{"round": n + 1, "case_id": key}
            for n in range(2) for key in (*CASE_IDS[n:], *CASE_IDS[:n])]


def case_inputs(case_id):
    data = read_json(DATA)
    if (data["classification"] != "DEVELOPMENT_ONLY" or data["review_status"] != "UNREVIEWED"
            or data["human_reviewed"] != 0 or data["formal_eval_v2_score"] is not None
            or data["rounds"] != 2 or tuple(c["case_id"] for c in data["cases"]) != CASE_IDS):
        raise ValueError("Invalid metadata development scope")
    item = next((c for c in data["cases"] if c["case_id"] == case_id), None)
    if item is None:
        raise ValueError("Unknown metadata case")
    app, document, target, registry, environ = project_inputs(item["project"])
    task, draft = task_inputs(item["project"], item["template"])
    definitions = registry.definitions()
    for policy in definitions:
        if item["project"] == "tickets":
            policy.planning_purpose = data["purposes"][policy.id]
    aliases = {p.id: f"rule_{i + 1}" if item["opaque_policies"] else p.id
               for i, p in enumerate(definitions)}
    for policy in definitions:
        policy.id = aliases[policy.id]
        for rule in policy.binding_rules:
            if rule.source_policy_id:
                rule.source_policy_id = aliases[rule.source_policy_id]
    registry = PolicyRegistry(definitions)
    task.user_task, task.policy_sha256 = item["user_task"], registry.digest()
    for steps in (task.requirements.steps, draft.steps):
        for step in steps:
            step.policy_id = aliases[step.policy_id]
            if item["project"] == "courses":
                step.role = {"alice": "bob", "bob": "alice"}[step.role]
            for arg in step.arguments:
                if getattr(arg, "from_step", getattr(arg, "source", None)):
                    continue
                if arg.name in {"subject", "name"}:
                    arg.value = item["literal"]
                elif arg.name == "priority":
                    arg.value = item["priority"]
    return document, target, registry, environ, httpx.ASGITransport(app), task, draft


def case_snapshot(case_id):
    document, target, registry, _, _, task, _ = case_inputs(case_id)
    return {"case_id": case_id, "task": task.model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
            "contract": load_openapi(document).model_dump(mode="json"),
            "registry": [p.model_dump(mode="json") for p in registry.definitions()]}


def grade_row(snapshot, report, evidence):
    if evidence is None:
        return {"verified": False, "requests": sum(len(s.attempts) for s in report.steps)}
    task = TaskContract.model_validate_json(json.dumps(snapshot["task"]))
    return grade({"expected": "PASS"}, task, report, evidence)


def summarize(frozen: dict, rows: list):
    if frozen["source"] not in {"mock_transport", "real"}:
        raise ValueError("Unknown metadata source")
    if [{k: r[k] for k in ("round", "case_id")} for r in rows] != schedule():
        raise ValueError("Incomplete metadata denominator")
    for row in rows:
        if type(row["model_calls"]) is not int or row["model_calls"] not in {0, 1}:
            raise ValueError("Invalid metadata call count")
    actual = [r for r in rows if r["model_calls"]]
    known = all(r["total_tokens"] is not None for r in actual)
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
              "human_reviewed": 0, "formal_eval_v2_score": None, "source": frozen["source"],
              "trials": len(rows), "attempted": len(actual),
              "successes": sum(r["grade"]["verified"] for r in rows),
              "total_tokens": sum(r["total_tokens"] for r in actual) if known else None,
              "real_model_calls": len(actual) if frozen["source"] == "real" else 0,
              "usage_kind": ("OBSERVED" if known else "UNKNOWN")
              if frozen["source"] == "real" else "SIMULATED",
              "per_case": {key: sum(r["grade"]["verified"] for r in rows if r["case_id"] == key)
                           for key in CASE_IDS}, "results": rows}


def finish_archive(output: Path, frozen: dict, rows: list):
    result = summarize(frozen, rows)
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(
        "# 策略用途与鉴权开发验收\n\n" + json.dumps(
            {k: v for k, v in result.items() if k != "results"}, ensure_ascii=False, indent=2)
        + "\n", encoding="utf-8")
    seal(output)
    verify_metadata(output)
    return {k: v for k, v in result.items() if k != "results"}


def verify_metadata(directory: Path):
    if read_json(directory / "archive.json")["files"] != inventory(directory):
        raise ValueError("Metadata archive changed")
    frozen, result = read_json(directory / "freeze.json"), read_json(directory / "results.json")
    rows = result["results"]
    if summarize(frozen, rows) != result:
        raise ValueError("Metadata summary mismatch")
    for row in rows:
        if row["run_id"] is None:
            if (row["model_calls"] or row["grade"]["verified"] or row["plan"] is not None
                    or row["grade"]["requests"]):
                raise ValueError("Invalid unattempted trial")
            continue
        report = ReportStore(directory / "reports").read(row["run_id"])
        evidence = PlanEvidence.model_validate(row["plan"]) if row["plan"] else None
        if (report_digest(report) != row["report_sha256"]
                or row["model_calls"] != (report.model_call.calls if report.model_call else 0)
                or row["total_tokens"] != (
                    report.model_call.total_tokens if report.model_call else None)
                or grade_row(frozen["cases"][row["case_id"]], report, evidence) != row["grade"]):
            raise ValueError("Metadata regrading mismatch")
    return {"archive_verified": True, "rescored_trials": len(rows), "new_model_calls": 0}


async def check_metadata(output: Path):
    from agenttest.workflow import Workflow

    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = {"source": "mock_transport", "git": checkout_identity(PROJECT_ROOT),
              "cases": {key: case_snapshot(key) for key in CASE_IDS}}
    save_evaluation(frozen, output / "freeze.json")

    def guard():
        if checkout_identity(PROJECT_ROOT) != frozen["git"]:
            raise ValueError("Source changed during metadata checks")

    rows, store = [], ReportStore(output / "reports")
    for slot in schedule():
        document, target, registry, environ, transport, task, draft = case_inputs(slot["case_id"])
        contract = load_openapi(document)
        context = planning_context(contract, target, registry, task.user_task,
                                   Scenario.model_json_schema())

        def response(request, content=draft.model_dump_json()):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": content}}], "usage": {
                    "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

        adapter = OpenAICompatiblePlanner(ModelSettings(model="offline-metadata",
            base_url="https://offline.invalid/v1", api_key="synthetic-fixture-key"),
            httpx.MockTransport(response), profile="generic")
        planner = FrozenContextPlanner(adapter, context, guard)
        report = await Workflow(document, target, registry, planner, store, transport, environ,
            user_task=task.user_task, intent_validator=task_validator(
                task, contract, target, registry), execution_guard=guard).run()
        evidence = capture_plan(planner.plan) if planner.plan else None
        rows.append({**slot, "run_id": report.run_id, "report_sha256": report_digest(report),
                     "plan": evidence.model_dump(mode="json") if evidence else None,
                     "grade": grade_row(frozen["cases"][slot["case_id"]], report, evidence),
                     "model_calls": report.model_call.calls,
                     "total_tokens": report.model_call.total_tokens})
    guard()
    return finish_archive(output, frozen, rows)
