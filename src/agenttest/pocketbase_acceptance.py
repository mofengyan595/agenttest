"""New PocketBase development tasks; offline checks never prove third-party behavior."""

import hashlib
import json
import math
import secrets
import time
from collections import Counter
from pathlib import Path

import httpx

from agenttest.composed_planning import FrozenContextPlanner, report_digest
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.models import PlanEvidence
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.executor import Executor
from agenttest.llm_planner import GenericPlannerDraft, ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.portability_demo import grade
from agenttest.project import check_project
from agenttest.report import ReportStore, fingerprint
from agenttest.task_contract import TaskContract, task_validator

DIRECTORY = PROJECT_ROOT / "examples/portability/pocketbase"
CASE_IDS = tuple(f"PB90{i}" for i in range(1, 7))
ORIGIN = "http://127.0.0.1:18095"


def schedule() -> list[dict]:
    """Six fixed baselines first, then three prearranged rounds; never append retries."""
    rows = [{"phase": "baseline", "round": 0, "case_id": key} for key in CASE_IDS]
    return rows + [{"phase": "planning", "round": n + 1, "case_id": key}
                   for n in range(3) for key in (*CASE_IDS[n:], *CASE_IDS[:n])]


def credentials() -> dict[str, str]:
    """Fresh process-owned values; never write or print these passwords."""
    return {f"PB_{role}_{field}": value for role in ("OWNER", "OTHER")
            for field, value in (("EMAIL", role.lower() + "@acceptance.invalid"),
                                 ("PASSWORD", secrets.token_urlsafe(32)))}


def inputs(case_id: str, environ: dict[str, str]):
    if case_id not in CASE_IDS:
        raise ValueError("Unknown PocketBase development case")
    checked = check_project(DIRECTORY / (case_id + ".project.json"), environ)
    if checked.inputs is None:
        raise ValueError("PocketBase project preflight rejected")
    data = checked.inputs
    if (data.target.base_url != ORIGIN or not data.target.allow_mutations
            or data.target.environment != "isolated"):
        raise ValueError("PocketBase target scope changed")
    fixed = Scenario.model_validate(read_json(DIRECTORY / "reference-plans.json")[case_id])
    task_validator(data.task, data.contract, data.target, data.registry)(data.task.user_task, fixed)
    Executor(data.target, data.contract, environ=environ, registry=data.registry).validate(fixed)
    return data, fixed


def snapshot(case_id, environ):
    data, fixed = inputs(case_id, environ)
    context = planning_context(data.contract, data.target, data.registry, data.task.user_task,
                               Scenario.model_json_schema())
    return {"case_id": case_id, "task": data.task.model_dump(mode="json"),
            "reference_sha256": fingerprint(fixed.model_dump(mode="json")),
            "context_sha256": fingerprint(context)}


def graded(task, report, plan):
    result = {"verified": False, "outcome": report.outcome.value,
              "requests": sum(len(s.attempts) for s in report.steps),
              "task_satisfied": report.task_satisfied}
    if plan is not None:
        result = grade({"expected": "PASS"}, task, report, plan)
    result["verified"] = bool(result["verified"]
        and report.contract_sha256 == task.contract_sha256
        and report.target_config_sha256 == task.target_config_sha256
        and report.policy_registry_sha256 == task.policy_sha256)
    return result


def fixture_transport(environ, fault=None):
    """Synthetic, process-local protocol checks only; not an upstream target emulator."""
    records, actors = {}, {}
    for role in ("OWNER", "OTHER"):
        actors[secrets.token_urlsafe(32)] = (role, role.lower() + "account001")

    def response(request):
        path, method = request.url.path, request.method
        if path == "/api/collections/acceptance_users/auth-with-password" and method == "POST":
            payload = json.loads(request.content)
            for token, (role, ident) in actors.items():
                if payload == {"identity": environ[f"PB_{role}_EMAIL"],
                               "password": environ[f"PB_{role}_PASSWORD"]}:
                    return httpx.Response(200, json={"token": token, "record": {"id": ident}})
            return httpx.Response(400, json={})
        actor = actors.get(request.headers.get("Authorization", "").removeprefix("Bearer "))
        if not actor:
            return httpx.Response(403, json={})
        _, owner = actor
        root = "/api/collections/acceptance_notes/records"
        if path == root and method == "POST":
            payload = json.loads(request.content)
            if payload["owner"] != owner:
                return httpx.Response(400, json={})
            ident = secrets.token_hex(8)[:15]
            records[ident] = {"id": ident, **payload}
            return httpx.Response(200, json=records[ident])
        if path == root and method == "GET":
            visible = [r for r in records.values()
                       if r["owner"] == owner and r["title"] == "归属字段校验"]
            return httpx.Response(200, json={"totalItems": len(visible), "items": visible})
        if not path.startswith(root + "/"):
            raise ValueError("Unapproved fixture request")
        ident = path.rsplit("/", 1)[-1]
        record = records.get(ident)
        if record is None or (record["owner"] != owner and fault != "ownership_leak"):
            return httpx.Response(404, json={})
        if method == "PATCH":
            record.update(json.loads(request.content))
        elif method == "DELETE":
            del records[ident]
            return httpx.Response(204)
        elif method != "GET":
            raise ValueError("Unapproved fixture method")
        return httpx.Response(200, json=record.copy())

    return httpx.MockTransport(response)


def fixed_draft(plan):
    """Only the baseline/simulated adapter reads these pre-authored fixed plans."""
    return GenericPlannerDraft.model_validate({"name": plan.name, "steps": [
        {"id": s.id, "operation_id": s.operation_id, "role": s.role, "policy_id": s.policy_id,
         "arguments": [{"location": loc, "name": name, "value": binding.value,
                        "from_step": binding.from_step,
                        "select": binding.select if binding.from_step else None}
                       for loc in ("body", "path_parameters", "query")
                       for name, binding in getattr(s, loc).items()]} for s in plan.steps]})


async def run_trial(slot, delegate, store, environ, transport, guard):
    from agenttest.workflow import Workflow, WorkflowBudget

    data, _ = inputs(slot["case_id"], environ)
    context = planning_context(data.contract, data.target, data.registry, data.task.user_task,
                               Scenario.model_json_schema())
    planner = FrozenContextPlanner(delegate, context, guard)
    started = time.monotonic()
    report = await Workflow(data.document, data.target, data.registry, planner, store, transport,
        environ, WorkflowBudget(model_timeout=65, workflow_timeout=120),
        user_task=data.task.user_task,
        intent_validator=task_validator(data.task, data.contract, data.target, data.registry),
        execution_guard=guard).run()
    evidence = capture_plan(planner.plan) if planner.plan else None
    stats = report.model_call
    return {**slot, "run_id": report.run_id, "report_sha256": report_digest(report),
        "plan": evidence.model_dump(mode="json") if evidence else None,
        "grade": graded(data.task, report, evidence), "outcome": report.outcome.value,
        "model_calls": stats.calls if stats else 0,
        "total_tokens": stats.total_tokens if stats else None,
        "workflow_elapsed_ms": round((time.monotonic() - started) * 1000, 3)}


def summarize(rows, source):
    if (source not in {"mock_transport", "real"}
            or [{k: r[k] for k in ("phase", "round", "case_id")} for r in rows] != schedule()):
        raise ValueError("PocketBase denominator or source changed")
    for r in rows:
        unknown = r["outcome"] == "ATTEMPT_OUTCOME_UNKNOWN"
        if (not unknown and (type(r["model_calls"]) is not int or r["model_calls"] not in {0, 1})
                or unknown and (r["run_id"] is not None or r["grade"]["verified"]
                                or r["model_calls"] is not None
                                or r["grade"]["requests"] is not None)
                or r["phase"] == "baseline" and r["model_calls"]
                or r["run_id"] is None and not unknown and (
                    r["outcome"] != "NOT_ATTEMPTED" or r["grade"]["verified"] or r["model_calls"]
                    or r["grade"]["requests"] != 0 or r["plan"] is not None)):
            raise ValueError("Invalid PocketBase trial accounting")
        if r["total_tokens"] is not None and (
                type(r["total_tokens"]) is not int or r["total_tokens"] < 0):
            raise ValueError("Invalid PocketBase token accounting")
        elapsed = r["workflow_elapsed_ms"]
        if elapsed is not None and (type(elapsed) not in {int, float}
                                   or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError("Invalid PocketBase duration")
    baseline = [r for r in rows if r["phase"] == "baseline"]
    trials = [r for r in rows if r["phase"] == "planning"]
    calls = [r for r in trials if r["model_calls"]]
    known = [r for r in calls if r["total_tokens"] is not None]
    unknown_calls = sum(r["outcome"] == "ATTEMPT_OUTCOME_UNKNOWN" for r in trials)
    if any(r["run_id"] is not None or r["outcome"] == "ATTEMPT_OUTCOME_UNKNOWN" for r in trials):
        if not all(r["grade"]["verified"] for r in baseline):
            raise ValueError("PocketBase planning started without six successful baselines")
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "human_reviewed": 0, "formal_eval_v2_score": None, "source": source,
        "target_evidence": "THIRD_PARTY_HTTP" if source == "real" else "SYNTHETIC_PROTOCOL_ONLY",
        "scheduled_baselines": 6,
        "baseline_successes": sum(r["grade"]["verified"] for r in baseline),
        "scheduled_trials": 18, "attempted_trials": sum(
            r["run_id"] is not None or r["outcome"] == "ATTEMPT_OUTCOME_UNKNOWN" for r in trials),
        "trial_successes": sum(r["grade"]["verified"] for r in trials),
        "not_attempted": sum(r["outcome"] == "NOT_ATTEMPTED" for r in rows),
        "cases": [{"case_id": key, "scheduled": 3,
                   "successes": sum(r["grade"]["verified"] for r in trials if r["case_id"] == key)}
                  for key in CASE_IDS], "outcomes": dict(Counter(r["outcome"] for r in trials)),
        "real_model_calls": (None if unknown_calls else sum(r["model_calls"] for r in calls))
                            if source == "real" else 0,
        "unconfirmed_model_call_slots": unknown_calls if source == "real" else 0,
        "usage_kind": ("UNKNOWN" if unknown_calls or len(known) != len(calls) else "OBSERVED")
                      if source == "real" else "SIMULATED",
        "known_tokens": sum(r["total_tokens"] for r in known),
        "total_tokens": sum(r["total_tokens"] for r in known)
                        if len(known) == len(calls) and not unknown_calls else None}


def finish(output, frozen, rows):
    for row in rows:
        path = output / position_name(row)
        if path.exists():
            if read_json(path) != row:
                raise ValueError("PocketBase position record changed")
        else:
            save_evaluation(row, path)
    result = {"summary": summarize(rows, frozen["source"]), "results": rows}
    save_evaluation(result, output / "results.json")
    save_evaluation({"schema_version": "agenttest.pocketbase.archive.v1",
                     "files": inventory(output)}, output / "archive.json")
    verify(output)
    return result["summary"]


def position_name(row):
    return f"{row['phase']}-R{row['round']}-{row['case_id']}.json"


def verify(directory):
    """Regrade the frozen reports and all 24 positions without network, model, or service."""
    manifest = read_json(directory / "archive.json")
    if (manifest.get("schema_version") != "agenttest.pocketbase.archive.v1"
            or manifest["files"] != inventory(directory)):
        raise ValueError("PocketBase archive changed")
    frozen, result = [read_json(directory / name) for name in ("freeze.json", "results.json")]
    if (frozen.get("schema_version") != "agenttest.pocketbase.freeze.v1"
            or frozen["schedule"] != schedule() or set(frozen["cases"]) != set(CASE_IDS)
            or result["summary"] != summarize(result["results"], frozen["source"])):
        raise ValueError("PocketBase freeze or summary changed")
    runtime = None
    if frozen["source"] == "real":
        runtime = read_json(directory / "runtime.json")
        if (frozen.get("max_calls") != 18 or frozen.get("reserved_tokens") != 433_728
                or frozen.get("model", {}).get("model") != "ecnu-plus"
                or frozen["model"].get("protocol") != "chatecnu"
                or runtime.get("origin") != ORIGIN
                or type(runtime.get("native_exit_confirmed")) is not bool
                or type(runtime.get("slots_reserved")) is not int
                or not 0 <= runtime["slots_reserved"] <= 18
                or runtime.get("tokens_reserved") != runtime["slots_reserved"] * 24_096):
            raise ValueError("PocketBase native provenance or budget changed")
    if frozen.get("prior_archive_sha256"):
        parent = directory / "prior"
        original_freeze = read_json(parent / "freeze.json")
        inherited = frozen.get("inherited_planning_positions")
        if (original_freeze.get("prior_archive_sha256")
                or hashlib.sha256((parent / "archive.json").read_bytes()).hexdigest()
                != frozen["prior_archive_sha256"]
                or original_freeze["cases"] != frozen["cases"]
                or original_freeze["model"] != frozen["model"]
                or type(inherited) is not int or not 0 < inherited < 18 or runtime is None
                or runtime.get("inherited_model_slots") != inherited):
            raise ValueError("PocketBase prior archive identity changed")
        if not verify(parent).get("native_exit_confirmed"):
            raise ValueError("PocketBase prior process exit was not confirmed")
        old_rows = read_json(parent / "results.json")["results"]
        if result["results"][6:6 + inherited] != old_rows[6:6 + inherited]:
            raise ValueError("PocketBase inherited positions were changed")
        for row in old_rows[6:6 + inherited]:
            name = Path("reports") / row["run_id"] / "report.json"
            if (directory / name).read_bytes() != (parent / name).read_bytes():
                raise ValueError("PocketBase inherited report changed")
    seen = set()
    for row in result["results"]:
        if read_json(directory / position_name(row)) != row:
            raise ValueError("PocketBase position/results mismatch")
        if row["run_id"] is None:
            continue
        if row["run_id"] in seen:
            raise ValueError("Duplicate PocketBase run ID")
        seen.add(row["run_id"])
        task = TaskContract.model_validate_json(json.dumps(frozen["cases"][row["case_id"]]["task"]))
        report = ReportStore(directory / "reports").read(row["run_id"])
        evidence = PlanEvidence.model_validate(row["plan"]) if row["plan"] else None
        stats = report.model_call
        expected_model = "ecnu-plus" if frozen["source"] == "real" else "offline-pocketbase"
        if row["phase"] == "planning" and (
                stats is not None and stats.calls and stats.model_name != expected_model
                or row["grade"]["verified"] and (stats is None or stats.calls != 1)):
            raise ValueError("PocketBase model provenance mismatch")
        if (row["report_sha256"] != report_digest(report)
                or row["grade"] != graded(task, report, evidence)
                or row["outcome"] != report.outcome.value
                or row["model_calls"] != (stats.calls if stats else 0)
                or row["total_tokens"] != (stats.total_tokens if stats else None)):
            raise ValueError("PocketBase regrading mismatch")
    return {"archive_verified": True, "rescored_positions": len(result["results"]),
            "native_exit_confirmed": runtime["native_exit_confirmed"] if runtime else None,
            "new_model_calls": 0, "new_http_requests": 0}


async def check_offline(output: Path):
    """Only synthetic transports and fixed replies; no live opt-in in this function."""
    from agenttest.workflow import FakeLLM

    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    environ, identity = credentials(), checkout_identity(PROJECT_ROOT)

    def guard():
        if checkout_identity(PROJECT_ROOT) != identity:
            raise ValueError("PocketBase source changed during acceptance")

    frozen = {"schema_version": "agenttest.pocketbase.freeze.v1", "source": "mock_transport",
        "schedule": schedule(), "cases": {k: snapshot(k, environ) for k in CASE_IDS},
        "git": identity}
    save_evaluation(frozen, output / "freeze.json")
    rows, store = [], ReportStore(output / "reports")
    for slot in schedule():
        _, plan = inputs(slot["case_id"], environ)
        if slot["phase"] == "baseline":
            delegate = FakeLLM(plan.model_dump_json())
        else:
            def response(request, content=fixed_draft(plan).model_dump_json()):
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                    "message": {"content": content}}], "usage": {
                        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

            delegate = OpenAICompatiblePlanner(ModelSettings(model="offline-pocketbase",
                base_url="https://offline.invalid/v1", api_key="synthetic-pocketbase-key"),
                httpx.MockTransport(response), profile="generic")
        rows.append(await run_trial(slot, delegate, store, environ,
                                    fixture_transport(environ), guard))
        save_evaluation(rows[-1], output / position_name(rows[-1]))
    guard()
    return finish(output, frozen, rows)
