"""Frozen three-round development comparison; sealed reports, no independent-review bypass."""

import json
import os
import shutil
import subprocess
import sys
import time
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
from agenttest.evaluation_freeze import _git, public_model_config
from agenttest.feedback import FEEDBACK_PROMPT, FeedbackAction, FeedbackModel
from agenttest.feedback_check import stats_for
from agenttest.investigation import FeedbackBudget, InvestigationConfig
from agenttest.llm_planner import GENERIC_PROMPT, ModelSettings, OpenAICompatiblePlanner
from agenttest.paired_tasks import paired_cases, paired_inputs
from agenttest.planner_input import planning_context
from agenttest.portability_demo import grade
from agenttest.report import ReportStore, fingerprint
from agenttest.task_contract import TaskContract, task_validator


def grade_agent_v1(case, task, report, plan, healthy=None):
    requests = sum(len(step.attempts) for step in report.steps)
    safe = (case["expected"] == "SAFE_REJECTION" and requests == 0
            and report.outcome.value in {"NEEDS_REVIEW", "TOOL_ERROR"}
            and report.diagnostic is not None)
    expected_code = {"empty": "OPENAPI_VALUE_LENGTH", "too_long": "OPENAPI_VALUE_LENGTH",
                     "wrong_type": "OPENAPI_VALUE_TYPE", "bad_priority": "OPENAPI_VALUE_ENUM"}
    parameter_rejection = safe and report.diagnostic.code == expected_code.get(case["invalid_kind"])
    business = bool(plan and case["expected"] != "SAFE_REJECTION" and grade(
        {"expected": case["expected"], "failed_check": case["failed_check"]},
        task, report, plan)["verified"])
    paired = bool(business and case["fault"] != "none" and healthy and healthy.outcome.value
                  == "PASS" and grade({"expected": "PASS", "failed_check": None},
                                      task, healthy, plan)["verified"])
    feedback = report.feedback
    selections = report.investigations
    correct_selections = sum(item.role == case["owner"] and item.method == "GET" and
        item.operation_id == ("getCourse" if case["project"] == "courses" else "readTicket")
        for item in selections)
    evidence = False
    if plan and report.steps and case["finding"] not in {None, "EVIDENCE_INSUFFICIENT"}:
        from agenttest.feedback_runtime import supported_finding

        roles = {step.step_id: step.role for step in plan.steps}
        evidence = any(supported_finding(FeedbackAction(action="finish", selection=None,
            finding=case["finding"], evidence_refs=["original", item.step_id]),
            report.steps[-1], roles[report.steps[-1].step_id], selections, roles)
            for item in selections)
    conclusion = bool(case["finding"] and feedback and business and
        feedback.finding == case["finding"] and (feedback.state == "SUPPORTED" and evidence
            if case["finding"] != "EVIDENCE_INSUFFICIENT" else
            feedback.state == "NEEDS_REVIEW"))
    return {"business_correct": business, "safe_rejection": bool(safe),
        "parameter_rejection": bool(parameter_rejection), "paired_detection": paired,
        "selection_count": len(selections), "correct_selections": correct_selections,
        "effective_evidence": bool(evidence), "conclusion_correct": conclusion,
        "false_positive": case["expected"] == "PASS" and report.outcome.value == "ASSERTION_FAILED",
        "task_goal_correct": business or bool(parameter_rejection)}


def grade_agent(case, task, report, plan, healthy=None):
    """A conservative engine fallback is not a correct model-authored abstention."""
    result = grade_agent_v1(case, task, report, plan, healthy)
    if case["finding"] == "EVIDENCE_INSUFFICIENT":
        feedback = report.feedback
        explicit = bool(feedback and feedback.reason == "FEEDBACK_EVIDENCE_INSUFFICIENT"
            and feedback.decisions and feedback.decisions[-1].action == "finish")
        result["conclusion_correct"] = bool(result["conclusion_correct"] and explicit)
    return result


def agent_row(case, round_index, task, report, plan, healthy,
              *, grading_version="explicit_finish_v2"):
    graders = {"legacy_v1": grade_agent_v1, "explicit_finish_v2": grade_agent}
    if grading_version not in graders:
        raise ValueError("Unknown paired grading version")
    stats = stats_for(report)
    called = [stat for stat in stats if stat.calls]
    known = all(stat.total_tokens is not None for stat in called)
    return {"case_id": case["id"], "round": round_index, "run_id": report.run_id,
        "report_sha256": report_digest(report), "outcome": report.outcome.value,
        "dsl_valid": report.dsl_valid, "policy_authorized": report.policy_authorized,
        "intent_verified": report.intent_verified, "task_satisfied": report.task_satisfied,
        "diagnostic": report.diagnostic.code if report.diagnostic else None,
        "feedback_state": report.feedback.state if report.feedback else None,
        "plan": plan.model_dump(mode="json") if plan else None,
        "healthy_run_id": healthy.run_id if healthy else None,
        "healthy_sha256": report_digest(healthy) if healthy else None,
        "grade": graders[grading_version](case, task, report, plan, healthy),
        "model_calls": sum(stat.calls for stat in stats), "usage_known": known,
        "total_tokens": sum(stat.total_tokens for stat in called) if known else None,
        "requests": sum(len(step.attempts) for step in report.steps),
        "investigation_requests": sum(len(step.attempts) for step in report.investigations),
        "healthy_requests": sum(len(step.attempts) for step in healthy.steps) if healthy else 0}


def summarize(rows, fixed, source):
    attempted = [row for row in rows if row.get("run_id")]
    keys = ("business_correct", "safe_rejection", "parameter_rejection", "paired_detection",
            "selection_count", "correct_selections", "effective_evidence", "conclusion_correct",
            "false_positive", "task_goal_correct")
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "human_reviewed": 0, "formal_eval_v2_score": None, "source": source,
        "unique_task_ids": 24, "supported_task_templates": 5, "rounds": 3,
        "scheduled_agent_positions": 72, "attempted": len(attempted),
        "not_attempted": 72-len(attempted), "fault_positions": 24,
        "supported_finding_positions": 21, "insufficient_evidence_positions": 3,
        "invalid_parameter_positions": 18, "healthy_positions": 30,
        **{key: sum(row["grade"][key] for row in attempted) for key in keys},
        **{key: sum(bool(row[key]) for row in attempted)
           for key in ("dsl_valid", "policy_authorized", "intent_verified", "task_satisfied")},
        "outcomes": {outcome: sum(row["outcome"] == outcome for row in attempted)
            for outcome in ("PASS", "ASSERTION_FAILED", "TOOL_ERROR", "TEST_INFRA_ERROR",
                            "NEEDS_REVIEW")},
        "model_calls": sum(row["model_calls"] for row in attempted),
        "real_model_calls": sum(row["model_calls"] for row in attempted) if source == "real" else 0,
        "total_tokens": sum(row["total_tokens"] for row in attempted)
            if all(row["usage_known"] for row in attempted) else None,
        "usage_status": "SIMULATED" if source != "real" else "OBSERVED"
            if all(row["usage_known"] for row in attempted) else "UNKNOWN",
        "asgi_requests": sum(row["requests"] + row["investigation_requests"]
            + row["healthy_requests"] for row in attempted) + sum(row["requests"] +
                (row["healthy"]["requests"] if row["healthy"] else 0) for row in fixed),
        "real_business_http": 0, "database_writes": 0,
        "fixed_pytest_positions": len(fixed),
        "fixed_correct": sum(fixed_correct(case, row) for (_, case), row in zip(
            [(r, c) for r in range(1, 4) for c in paired_cases()], fixed)),
        "fixed_expected_detections": sum(row["outcome"] == "ASSERTION_FAILED" and
            row["healthy"] and row["healthy"]["outcome"] == "PASS" for row in fixed),
        "fixed_safe_rejections": sum(row["outcome"] == "SAFE_REJECTION" for row in fixed),
        "fixed_false_positives": sum(row["outcome"] == "ASSERTION_FAILED" and
            not row["healthy"] for row in fixed),
        "baseline_authorship": "DEVELOPER_AUTHORED_FIXED_PYTEST_NOT_INDEPENDENT_HUMAN",
        "human_timing_seconds": None, "time_saved_seconds": None}


def fixed_correct(case, row):
    if row["case_id"] != case["id"] or row["outcome"] != case["expected"]:
        return False
    if case["invalid_kind"]:
        return row["requests"] == 0
    if case["fault"] != "none":
        return bool(row["healthy"] and row["healthy"]["outcome"] == "PASS" and any(
            check[:3] == case["failed_check"] and check[3] is False for check in row["checks"]))
    return bool(row["checks"] and all(check[3] is True for check in row["checks"]))


def known_schema_failure(report):
    stats = stats_for(report)
    called = [stat for stat in stats if stat.calls]
    return bool(called and all(stat.http_status == 200 and stat.total_tokens is not None
                               for stat in called) and report.feedback and
        report.feedback.state == "TOOL_ERROR" and report.feedback.reason == "FEEDBACK_MODEL_ERROR"
        and called[-1].diagnostic and called[-1].diagnostic.code.startswith("MODEL_SCHEMA_"))


def stop_paid_schedule(report):
    stats = [stat for stat in stats_for(report) if stat.calls]
    if (any(stat.total_tokens is None or stat.http_status != 200 for stat in stats)
            or report.outcome.value == "TEST_INFRA_ERROR"):
        return True
    return bool(report.feedback and report.feedback.state in {
        "TOOL_ERROR", "TEST_INFRA_ERROR", "TIMEOUT", "CANCELLED"}
        and not known_schema_failure(report))


def unattempted_parent(parent: Path):
    """Only an immutable known-usage format failure can release never-attempted positions."""
    verify_paired(parent)
    result = read_json(parent / "results.json")
    attempted = [row for row in result["results"] if row.get("run_id")]
    if not attempted or len(attempted) == 72:
        raise ValueError("No known failed parent with unattempted positions")
    store = ReportStore(parent / "reports")
    for row in attempted:
        report = store.read(row["run_id"])
        if (not row["usage_known"] or any(stat.calls and (stat.total_tokens is None or
                stat.http_status != 200) for stat in stats_for(report))):
            raise ValueError("Unknown or transport usage cannot release paid positions")
    if not known_schema_failure(store.read(attempted[-1]["run_id"])):
        raise ValueError("Only a known model schema failure permits a new schedule")
    return result


def match_parent_protocol(current, prior):
    keys = ("cases", "rounds", "order", "source", "tasks", "contexts", "capabilities", "model",
        "authority", "planner_prompt_sha256", "planning_requests", "feedback_prompt_sha256",
        "feedback_schema_sha256", "max_calls", "max_tokens")
    if any(current[key] != prior[key] for key in keys):
        raise ValueError("Parent model, tasks or runtime protocol changed")
    if current.get("grading_version", "legacy_v1") != prior.get("grading_version", "legacy_v1"):
        raise ValueError("Parent grading protocol changed")
    changes = _git(PROJECT_ROOT, "diff", "--name-only", prior["git"]["commit"], "HEAD",
                   "--", "src/agenttest").splitlines()
    if set(changes) - {"src/agenttest/paired_evaluation.py"}:
        raise ValueError("Parent execution engine changed")


async def run_paired(output: Path, settings: ModelSettings | None = None,
                     *, unattempted_from: Path | None = None):
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    private_output(PROJECT_ROOT, output)
    prior_result = prior_freeze = None
    if unattempted_from:
        private_output(PROJECT_ROOT, unattempted_from)
        if (output.resolve().is_relative_to(unattempted_from.resolve()) or
                unattempted_from.resolve().is_relative_to(output.resolve())):
            raise ValueError("New schedule must be separate from the parent archive")
        prior_result = unattempted_parent(unattempted_from)
        prior_freeze = read_json(unattempted_from / "freeze.json")
    output.mkdir(parents=True, exist_ok=False)
    identity = checkout_identity(PROJECT_ROOT)
    if settings and identity["worktree_status"]:
        raise ValueError("Paid schedule requires clean committed source")
    if settings and (settings.model != "ecnu-plus" or settings.protocol != "chatecnu" or
                     settings.base_url != "https://chat.ecnu.edu.cn/open/api/v1"):
        raise ValueError("Only the operator-configured provider is authorized")
    selected = settings or ModelSettings(model="paired-offline", api_key="synthetic-only",
                                         base_url="https://offline.invalid/v1")
    selected = ModelSettings.model_validate({**selected.model_dump(), "timeout": 60.0})
    source = "real" if settings else "mock_transport"
    authority = InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=180.0))
    cases = paired_cases()
    frozen = {"schema_version": "agenttest.paired-development.v2", "git": identity,
        "grading_version": "explicit_finish_v2",
        "cases": cases, "rounds": 3, "order": "round_then_case", "source": source,
        "tasks": {}, "contexts": {}, "capabilities": {},
        "model": public_model_config(selected), "authority": authority.model_dump(mode="json"),
        "planner_prompt_sha256": fingerprint(GENERIC_PROMPT), "planning_requests": {},
        "feedback_prompt_sha256": fingerprint(FEEDBACK_PROMPT),
        "feedback_schema_sha256": fingerprint(FeedbackAction.model_json_schema()),
        "max_calls": 288,
        "max_tokens": 288 * (selected.max_input_tokens + selected.max_output_tokens),
        "no_implicit_retries": True, "stop_on_unknown_usage": True,
        "known_preflight_rejections_continue": True, "human_reviewed": 0,
        "known_model_schema_failure": "STOP_RUN_CONTINUE_NEXT_UNUSED_POSITION"}
    for case in cases:
        _, doc, target, policies, _, task, _, capabilities = paired_inputs(case)
        frozen["tasks"][case["id"]] = task.model_dump(mode="json")
        frozen["contexts"][case["id"]] = planning_context(load_openapi(doc), target, policies,
            task.user_task, Scenario.model_json_schema())
        frozen["capabilities"][case["id"]] = [c.model_dump(mode="json")
                                               for c in capabilities.entries()]
        request = OpenAICompatiblePlanner(selected, profile="generic").prepare_request(
            frozen["contexts"][case["id"]])
        frozen["planning_requests"][case["id"]] = fingerprint(request.hex())
    prior_rows = {}
    if prior_result:
        match_parent_protocol(frozen, prior_freeze)
        prior_rows = {(row["round"], row["case_id"]): row for row in prior_result["results"]
                      if row["run_id"]}
        frozen["parent"] = {"commit": prior_freeze["git"]["commit"],
            "seal_sha256": fingerprint((unattempted_from / "archive.json").read_bytes().hex()),
            "freeze_sha256": fingerprint(prior_freeze), "results_sha256": fingerprint(prior_result),
            "inherited_positions": len(prior_rows), "new_positions": 72-len(prior_rows),
            "new_max_calls": 4 * (72-len(prior_rows)),
            "new_max_tokens": 4 * (72-len(prior_rows)) * (
                selected.max_input_tokens + selected.max_output_tokens),
            "reason": "KNOWN_SCHEMA_FAILURE_NO_RETRY_OF_ATTEMPTED_POSITION"}
        for name in ("archive.json", "freeze.json", "results.json"):
            shutil.copyfile(unattempted_from / name, output / ("prior-" + name))
        ids = {row[key] for row in prior_rows.values() for key in ("run_id", "healthy_run_id")
               if row[key]}
        for run_id in ids:
            destination = output / "reports" / run_id
            destination.mkdir(parents=True)
            shutil.copyfile(unattempted_from / "reports" / run_id / "report.json",
                            destination / "report.json")
    save_evaluation(frozen, output / "freeze.json")

    def guard():
        if checkout_identity(PROJECT_ROOT) != identity:
            raise ValueError("Paired evaluation source changed")

    started = time.perf_counter()
    env = {**os.environ, "AGENTTEST_PAIRED_OUTPUT": str(output.absolute())}
    if prior_result:
        names = ["fixed-pytest.log", "fixed-junit.xml"] + [
            f"fixed-{r}-{case['id']}.json" for r in range(1, 4) for case in cases]
        for name in names:
            shutil.copyfile(unattempted_from / name, output / name)
        fixed_exit = 1
    else:
        with (output / "fixed-pytest.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run([sys.executable, "-m", "pytest",
                "scripts/paired_fixed_pytest.py", "-q", "--tb=no", "--basetemp",
                str(output / "pytest-temp"), "--junitxml", str(output / "fixed-junit.xml")],
                cwd=PROJECT_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600,
                check=False)
        fixed_exit = completed.returncode
    fixed = [read_json(output / f"fixed-{r}-{case['id']}.json")
             for r in range(1, 4) for case in cases]
    if (fixed_exit != 1 or not all(fixed_correct(case, row)
            for (_, case), row in zip([(r, c) for r in range(1, 4) for c in cases], fixed))):
        raise ValueError("Fixed pytest baseline did not preserve expected failures")
    rows, stop = [], None
    for round_index in range(1, 4):
        for case in cases:
            guard()
            inherited = prior_rows.get((round_index, case["id"]))
            if inherited:
                rows.append(inherited)
                save_evaluation(inherited, output / f"agent-{round_index}-{case['id']}.json")
                continue
            if stop:
                rows.append({"case_id": case["id"], "round": round_index,
                             "run_id": None, "not_attempted_reason": stop})
                continue
            app, doc, target, policies, environ, task, draft, capabilities = paired_inputs(case)
            run_started = time.perf_counter()

            def response(request, draft=draft, case=case):
                payload = json.loads(request.content)
                if payload["response_format"]["json_schema"]["name"] == "agenttest_plan":
                    content = draft.model_dump(mode="json")
                else:
                    context = json.loads(payload["messages"][1]["content"])
                    if (not context["evidence"] and context["choices"]
                            and not context["finish_required"]):
                        choice = next(c for c in context["choices"] if c["role"] == case["owner"])
                        content = {"action": "investigate", "selection": choice,
                                   "finding": None, "evidence_refs": []}
                    else:
                        finding = case["finding"] or "EVIDENCE_INSUFFICIENT"
                        refs = (["original", context["evidence"][0]["ref"]]
                            if finding != "EVIDENCE_INSUFFICIENT" and context["evidence"] else [])
                        content = {"action": "finish", "selection": None,
                                   "finding": finding, "evidence_refs": refs}
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                    "message": {"content": json.dumps(content)}}], "usage": {
                        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

            transport = None if settings else httpx.MockTransport(response)
            planner = FrozenContextPlanner(OpenAICompatiblePlanner(selected, transport,
                profile="generic"), frozen["contexts"][case["id"]], guard)
            store = ReportStore(output / "reports")
            report = await Workflow(doc, target, policies, planner, store,
                httpx.ASGITransport(app), environ,
                WorkflowBudget(model_timeout=65, workflow_timeout=300), user_task=task.user_task,
                intent_validator=task_validator(task, load_openapi(doc), target, policies),
                investigation=authority, capabilities=capabilities,
                analyzer=FeedbackModel(selected, transport), execution_guard=guard).run()
            plan = capture_plan(planner.plan) if planner.plan else None
            healthy = None
            if case["fault"] != "none" and report.intent_verified and planner.plan:
                app, doc, target, policies, environ, _, _, _ = paired_inputs(case, healthy=True)
                healthy = await Workflow(doc, target, policies,
                    FakeLLM(planner.plan.model_dump_json()), store,
                    httpx.ASGITransport(app), environ,
                    user_task=task.user_task,
                    intent_validator=task_validator(task, load_openapi(doc), target, policies),
                    execution_guard=guard).run()
            item = agent_row(case, round_index, task, report, plan, healthy)
            item["elapsed_ms"] = round((time.perf_counter()-run_started)*1000, 3)
            rows.append(item)
            save_evaluation(item, output / f"agent-{round_index}-{case['id']}.json")
            print(json.dumps({"position": len(rows), "case_id": case["id"],
                "outcome": item["outcome"], "tokens": item["total_tokens"]}), flush=True)
            if stop_paid_schedule(report):
                stop = "UNKNOWN_USAGE_OR_TRANSPORT_INFRA_STOP"
            planner.output = None
    guard()
    artifacts = {name: fingerprint((output / name).read_bytes().hex())
                 for name in ("fixed-pytest.log", "fixed-junit.xml")}
    elapsed = round((time.perf_counter()-started)*1000, 3)
    result = {**summarize(rows, fixed, source), "baseline_artifacts": artifacts, "elapsed_ms":
        elapsed + (prior_result["elapsed_ms"] if prior_result else 0),
        "results": rows, "fixed": fixed}
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text("# 配对开发评估\n\n" + json.dumps(
        {k: v for k, v in result.items() if k not in {"results", "fixed"}},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal(output)
    verify_paired(output)
    return {k: v for k, v in result.items() if k not in {"results", "fixed"}}


def verify_paired(output: Path):
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("Paired archive changed")
    frozen, result = read_json(output / "freeze.json"), read_json(output / "results.json")
    grading_version = frozen.get("grading_version", "legacy_v1")
    if grading_version not in {"legacy_v1", "explicit_finish_v2"}:
        raise ValueError("Unknown paired grading version")
    if frozen.get("parent"):
        prior = read_json(output / "prior-results.json")
        prior_freeze = read_json(output / "prior-freeze.json")
        metadata = frozen["parent"]
        if (fingerprint(prior) != metadata["results_sha256"] or
            fingerprint(prior_freeze) != metadata["freeze_sha256"] or
            fingerprint((output / "prior-archive.json").read_bytes().hex()) !=
                metadata["seal_sha256"] or prior["fixed"] != result["fixed"]):
            raise ValueError("Parent snapshots changed")
        inherited = [row for row in prior["results"] if row["run_id"]]
        if (len(inherited) != metadata["inherited_positions"] or any(
                result["results"][prior["results"].index(row)] != row for row in inherited)):
            raise ValueError("An attempted parent position was replaced")
    if result["baseline_artifacts"] != {name: fingerprint((output / name).read_bytes().hex())
            for name in ("fixed-pytest.log", "fixed-junit.xml")}:
        raise ValueError("Baseline log or JUnit changed")
    if frozen["cases"] != paired_cases() or frozen["rounds"] != 3 or frozen["max_calls"] != 288:
        raise ValueError("Paired schedule changed")
    schedule = [(r, c) for r in range(1, 4) for c in frozen["cases"]]
    if [(row["round"], row["case_id"]) for row in result["results"]] != [
            (r, c["id"]) for r, c in schedule] or len(result["fixed"]) != 72:
        raise ValueError("Paired denominator changed")
    store, seen, strict_conclusions = ReportStore(output / "reports"), set(), 0
    for (r, case), item, fixed in zip(schedule, result["results"], result["fixed"]):
        if (fixed != read_json(output / f"fixed-{r}-{case['id']}.json") or
                fixed["round"] != r or not fixed_correct(case, fixed)):
            raise ValueError("Fixed row changed")
        if not item["run_id"]:
            if set(item) != {"round", "case_id", "run_id", "not_attempted_reason"}:
                raise ValueError("Missing position changed")
            continue
        if item["run_id"] in seen:
            raise ValueError("Reused report")
        seen.add(item["run_id"])
        report = store.read(item["run_id"])
        healthy = store.read(item["healthy_run_id"]) if item["healthy_run_id"] else None
        plan = PlanEvidence.model_validate(item["plan"]) if item["plan"] else None
        task = TaskContract.model_validate_json(json.dumps(frozen["tasks"][case["id"]]))
        expected = agent_row(case, r, task, report, plan, healthy,
                             grading_version=grading_version)
        strict_conclusions += grade_agent(case, task, report, plan, healthy)["conclusion_correct"]
        if expected != {k: v for k, v in item.items() if k != "elapsed_ms"}:
            raise ValueError("Cold regrading mismatch")
        if item["model_calls"] > 4 or item["investigation_requests"] > 2:
            raise ValueError("Per-run safety budget exceeded")
    summary = summarize(result["results"], result["fixed"], frozen["source"])
    excluded = {"results", "fixed", "elapsed_ms", "baseline_artifacts"}
    if summary != {k: v for k, v in result.items() if k not in excluded}:
        raise ValueError("Paired summary changed")
    return {"archive_verified": True, "agent_positions": 72, "fixed_positions": 72,
            "attempted_reports": len(seen), "new_model_calls": 0,
            "stored_grading_version": grading_version,
            "stored_conclusions_correct": result["conclusion_correct"],
            "explicit_finish_audit_correct": strict_conclusions,
            "historical_scores_unchanged": True}
