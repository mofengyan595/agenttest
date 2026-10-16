"""Four frozen development tasks, three trials each; reuse the trusted engine."""

import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path

import httpx

from agenttest.composed_demo import composed_inputs
from agenttest.composed_evaluation import (
    CompositionCase,
    CompositionPlanEvidence,
    CompositionSuite,
    capture_composition,
    reference_draft,
    score_composed_report,
)
from agenttest.composed_intent import validate_composed_intent
from agenttest.composed_planning import report_digest
from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.eval_v2.models import PlanEvidence
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.executor import Executor
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import grade, project_inputs, task_inputs
from agenttest.report import ReportStore, fingerprint
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract, task_validator

CASE_IDS = ("P501", "G501", "F201", "F202")
DEVELOPMENT = PROJECT_ROOT / "examples/stability-development.json"


def schedule() -> list[dict]:
    """Rotate the fixed task order; retries never occupy another planned trial."""
    return [{"round": n + 1, "case_id": case_id}
            for n in range(3) for case_id in (*CASE_IDS[n:], *CASE_IDS[:n])]


def case_inputs(case_id: str):
    """Operator-owned fixtures only; model input never receives labels or fixed plans."""
    data = read_json(DEVELOPMENT)
    if (data["schema_version"] != "agenttest.stability.development.v1"
            or data["classification"] != "DEVELOPMENT_ONLY" or data["rounds"] != 3
            or data["review_status"] != "UNREVIEWED" or data["human_reviewed"] != 0
            or data["formal_eval_v2_score"] is not None
            or data["composition_case_id"] != "P501" or data["ticket"]["case_id"] != "G501"
            or [c["case_id"] for c in data["petclinic"]] != ["F201", "F202"]):
        raise ValueError("Invalid development fixture scope")
    if case_id == "P501":
        case = CompositionSuite.model_validate(read_json(PROJECT_ROOT /
            "examples/composed-stability-development.json")).cases[0]
        app, target, registry, environ = composed_inputs()
        document, fixed = json.dumps(app.openapi()).encode(), reference_draft(case.expected)
        return (document, target, registry, environ, httpx.ASGITransport(app), case,
                fixed.scenario(), "mini_composed", validate_composed_intent)
    if case_id == "G501":
        app, document, target, registry, environ = project_inputs("tickets")
        task, draft = task_inputs("tickets", "close")
        task.user_task = data["ticket"]["user_task"]
        task.requirements.steps[0].arguments[0].value = data["ticket"]["subject"]
        draft.steps[0].arguments[0].value = data["ticket"]["subject"]
        return (document, target, registry, environ, httpx.ASGITransport(app), task,
                draft.scenario(), "generic", task_validator(
                    task, load_openapi(document), target, registry))
    if case_id not in {"F201", "F202"}:
        raise ValueError("Unknown stability fixture")
    item = next(c for c in data["petclinic"] if c["case_id"] == case_id)
    directory = PROJECT_ROOT / "examples/portability/petclinic-chain"
    document = (directory.parent / "petclinic/openapi.json").read_bytes()
    target = TargetConfig.model_validate_json((directory / "target.json").read_bytes())
    task = TaskContract.model_validate_json((directory / "first.task.json").read_bytes())
    policies = read_json(directory / "policies.json")
    for policy, candidate in zip(policies[1:], data["petclinic"]):
        policy["binding_rules"][0]["source_selector"] = candidate["source_selector"]
        policy["id"] = "petclinic_stability_" + candidate["case_id"]
    selected_policy_id = "petclinic_stability_" + case_id
    registry = PolicyRegistry.from_json(json.dumps(policies))
    task.user_task, task.policy_sha256 = item["user_task"], registry.digest()
    task.requirements.steps[1].policy_id = selected_policy_id
    task.requirements.steps[1].arguments[0].selector = item["source_selector"]
    fixed = Scenario.model_validate_json((directory / "first.plan.json").read_bytes())
    fixed.steps[1].policy_id = selected_policy_id
    fixed.steps[1].path_parameters["petTypeId"].select = item["source_selector"]
    rows = [{"id": ident, "name": f"synthetic{ident}"} for ident in (41, 9, 22, 87, 61, 50)]

    def response(request):
        if request.method != "GET":
            raise ValueError("Read-only fixture")
        if request.url.path == "/petclinic/api/pettypes":
            return httpx.Response(200, json=rows)
        ident = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=next(row for row in rows if row["id"] == ident))

    return (document, target, registry, {}, httpx.MockTransport(response), task, fixed,
            "generic", task_validator(task, load_openapi(document), target, registry))


def case_snapshot(case_id: str) -> dict:
    document, target, registry, environ, _, label, fixed, profile, validate = case_inputs(case_id)
    contract = load_openapi(document)
    validate(label.user_task, fixed)
    Executor(target, contract, environ=environ, registry=registry).validate(fixed)
    # Credential values are not part of snapshots or model context.
    return {"case_id": case_id, "label": label.model_dump(mode="json"), "profile": profile,
            "contract_sha256": fingerprint(contract.model_dump()),
            "target": target.model_dump(mode="json"),
            "registry": [p.model_dump(mode="json") for p in registry.definitions()],
            "fixed_plan_sha256": fingerprint(fixed.model_dump())}


def capture(case_id, plan, registry):
    return capture_composition(plan, registry) if case_id == "P501" else capture_plan(plan)


def grade_trial(snapshot, report, evidence):
    """Acceptance needs complete intent/execution evidence, never just outcome=PASS."""
    if snapshot["case_id"] == "P501":
        registry = PolicyRegistry.from_json(json.dumps(snapshot["registry"]))
        result = score_composed_report(CompositionCase.model_validate(snapshot["label"]),
                                       report, evidence, registry)
        return {"verified": result["end_to_end_success"], "details": result}
    if evidence is None:
        return {"verified": False}
    task = TaskContract.model_validate_json(json.dumps(snapshot["label"]))
    return grade({"expected": "PASS"}, task, report, evidence)


def summarize(rows: list[dict], source: str) -> dict:
    """Keep all twelve scheduled slots, unknown usage, and per-task three-trial results."""
    if source not in {"mock_transport", "real"} or [
        {k: r[k] for k in ("round", "case_id")} for r in rows] != schedule():
        raise ValueError("Incomplete or reordered stability denominator")
    calls = [r for r in rows if r["model_calls"]]
    for row in rows:
        if (type(row["model_calls"]) is not int or row["model_calls"] not in {0, 1}
                or type(row["grade"]["verified"]) is not bool
                or row["total_tokens"] is not None and (
                    type(row["total_tokens"]) is not int or row["total_tokens"] < 0)):
            raise ValueError("Invalid trial accounting")
        if row["run_id"] is None and (row["model_calls"] or row["total_tokens"] is not None
                                      or row["grade"]["verified"]
                                      or row["outcome"] != "NOT_ATTEMPTED"):
            raise ValueError("Unattempted row cannot claim calls or success")
        duration = row.get("workflow_elapsed_ms")
        if duration is not None and (not math.isfinite(duration) or duration < 0):
            raise ValueError("Invalid elapsed time")
    known = [r for r in calls if r["total_tokens"] is not None]
    observed = sum(r["total_tokens"] for r in known)
    by_case = []
    for case_id in CASE_IDS:
        selected = [r for r in rows if r["case_id"] == case_id]
        elapsed = [r["workflow_elapsed_ms"] for r in selected
                   if r["workflow_elapsed_ms"] is not None]
        by_case.append({"case_id": case_id, "scheduled": 3,
            "attempted": sum(r["run_id"] is not None for r in selected),
            "successes": sum(r["grade"]["verified"] for r in selected),
            "all_three_succeeded": all(r["grade"]["verified"] for r in selected),
            "outcomes": [r["outcome"] for r in selected],
            "workflow_ms": {"min": min(elapsed), "median": statistics.median(elapsed),
                            "max": max(elapsed)} if elapsed else None})
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "human_reviewed": 0, "formal_eval_v2_score": None, "source": source,
        "scheduled": 12, "attempted": sum(r["run_id"] is not None for r in rows),
        "successes": sum(r["grade"]["verified"] for r in rows), "cases": by_case,
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "model_calls": sum(r["model_calls"] for r in rows),
        "real_model_calls": sum(r["model_calls"] for r in rows) if source == "real" else 0,
        "usage_kind": "OBSERVED" if source == "real" else "SIMULATED",
        "known_tokens": observed, "unknown_usage_calls": len(calls) - len(known),
        "total_tokens": observed if len(calls) == len(known) else None}


def finish_archive(output: Path, frozen: dict, rows: list[dict]):
    result = {"summary": summarize(rows, frozen["source"]), "results": rows}
    save_evaluation(result, output / "results.json")
    save_evaluation({"schema_version": "agenttest.stability.archive.v1",
                     "files": inventory(output)}, output / "archive.json")
    verify_stability(output)
    return result["summary"]


def verify_stability(directory: Path) -> dict:
    """Cold regrade reports and the complete schedule without models, HTTP, or fixtures."""
    manifest = read_json(directory / "archive.json")
    if (manifest.get("schema_version") != "agenttest.stability.archive.v1"
            or manifest["files"] != inventory(directory)):
        raise ValueError("Stability archive changed")
    frozen, result = [read_json(directory / name) for name in ("freeze.json", "results.json")]
    if (frozen["schema_version"] != "agenttest.stability.freeze.v1"
            or frozen["schedule"] != schedule() or set(frozen["cases"]) != set(CASE_IDS)):
        raise ValueError("Invalid stability freeze")
    rows = result["results"]
    if result["summary"] != summarize(rows, frozen["source"]):
        raise ValueError("Stability summary mismatch")
    seen = set()
    for row in rows:
        key = f"R{row['round']}-{row['case_id']}"
        if read_json(directory / (key + ".json")) != row:
            raise ValueError("Stability row mismatch")
        if row["run_id"] is None:
            if row["grade"]["verified"] or row["model_calls"] or row["outcome"] != "NOT_ATTEMPTED":
                raise ValueError("Unattempted row cannot succeed")
            continue
        if row["run_id"] in seen:
            raise ValueError("Duplicate trial run ID")
        seen.add(row["run_id"])
        report = ReportStore(directory / "reports").read(row["run_id"])
        evidence_type = CompositionPlanEvidence if row["case_id"] == "P501" else PlanEvidence
        evidence = evidence_type.model_validate(row["plan"]) if row["plan"] else None
        stats = report.model_call
        if (report_digest(report) != row["report_sha256"]
                or row["outcome"] != report.outcome.value
                or row["model_calls"] != (stats.calls if stats else 0)
                or row["total_tokens"] != (stats.total_tokens if stats else None)
                or row["grade"] != grade_trial(frozen["cases"][row["case_id"]], report, evidence)):
            raise ValueError("Stability regrading mismatch")
    return {"archive_verified": True, "rescored_trials": len(rows),
            "new_model_calls": 0, "new_http_requests": 0}


async def check_stability(output: Path):
    """Fixed replies only; all three targets are disposable offline transports."""
    from agenttest.development_ab import private_output
    from agenttest.eval_v2.freeze import checkout_identity
    from agenttest.eval_v2.runner import ContextPlanner
    from agenttest.workflow import Workflow

    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = {"schema_version": "agenttest.stability.freeze.v1", "source": "mock_transport",
        "schedule": schedule(), "cases": {key: case_snapshot(key) for key in CASE_IDS},
        "git": checkout_identity(PROJECT_ROOT)}
    save_evaluation(frozen, output / "freeze.json")
    rows, store = [], ReportStore(output / "reports")
    for slot in schedule():
        values = case_inputs(slot["case_id"])
        document, target, registry, environ, transport, label, fixed, profile, validator = values
        context = planning_context(load_openapi(document), target, registry, label.user_task,
                                   Scenario.model_json_schema())
        if profile == "mini_composed":
            draft = reference_draft(label.expected)
        else:
            from agenttest.llm_planner import PlannerDraft

            draft = PlannerDraft(name="fixed stability fixture", steps=[{
                "id": s.id, "operation_id": s.operation_id, "role": s.role,
                "policy_id": s.policy_id, "arguments": [
                    {"location": loc, "name": name, "value": a.value,
                     "from_step": a.from_step, "select": a.select if a.from_step else None}
                    for loc in ("body", "path_parameters", "query")
                    for name, a in getattr(s, loc).items()]} for s in fixed.steps])

        def response(request, content=draft.model_dump_json()):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": content}}], "usage": {
                    "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

        planner = OpenAICompatiblePlanner(ModelSettings(model="offline-stability",
            base_url="https://model.invalid/v1", api_key="synthetic-stability-key"),
            httpx.MockTransport(response), profile=profile)
        captured = ContextPlanner(planner, context, context)
        started = time.monotonic()
        report = await Workflow(document, target, registry, captured, store, transport, environ,
            user_task=label.user_task, intent_validator=validator).run()
        evidence = capture(slot["case_id"], captured.plan, registry) if captured.plan else None
        row = {**slot, "run_id": report.run_id, "outcome": report.outcome.value,
            "report_sha256": report_digest(report),
            "plan": evidence.model_dump(mode="json") if evidence else None,
            "grade": grade_trial(frozen["cases"][slot["case_id"]], report, evidence),
            "model_calls": report.model_call.calls if report.model_call else 0,
            "total_tokens": report.model_call.total_tokens if report.model_call else None,
            "workflow_elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
        save_evaluation(row, output / f"R{slot['round']}-{slot['case_id']}.json")
        rows.append(row)
    if checkout_identity(PROJECT_ROOT) != frozen["git"]:
        raise ValueError("Source changed during stability check")
    return finish_archive(output, frozen, rows)
