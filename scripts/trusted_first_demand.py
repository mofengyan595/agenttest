"""One frozen new demand, using the existing goal runtime; no retry or model code execution."""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from evaluate_trusted_testing import DATA, DATASETS, settings_for, verify_archive
from trusted_validation_support import ObservedASGI, PublicTrace, copied_profiles, model_stats

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.evidence_context import EvidenceFeedbackModel
from agenttest.goal_cli import synthetic_runtime
from agenttest.goal_runtime import run_goal
from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionCapture, RegressionRuntime, export_regression
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.workflow import WorkflowBudget


def evidence(output):
    frozen = read_json(output / "freeze.json")
    request = frozen["request"]
    report = ReportStore(output / "reports").read(read_json(output / "result.json")["run_id"])
    stats = [s for s in model_stats(report) if s.calls]
    attempts = sum(len(s.attempts) for s in [*report.steps, *report.investigations])
    calls = sum(s.calls for s in stats)
    tokens = sum(s.total_tokens or 0 for s in stats)
    known = all(s.total_tokens is not None for s in stats)
    budget_ok = (calls <= request["max_model_calls"] and attempts <= request["max_requests"]
        and sum(len(s.attempts) for s in report.investigations)
            <= request["max_readonly_investigations"] and tokens <= request["max_total_tokens"])
    return dict(run_id=report.run_id, outcome=report.outcome.value,
        task_satisfied=report.task_satisfied, dsl_valid=report.dsl_valid,
        policy_authorized=report.policy_authorized, intent_verified=report.intent_verified,
        full_task_complete=bool(report.outcome == Outcome.PASS and report.task_satisfied
            and report.dsl_valid and report.policy_authorized and report.intent_verified
            and known and budget_ok),
        real_model_calls=calls, known_tokens=tokens, total_tokens=tokens if known else None,
        unknown_usage_calls=sum(s.total_tokens is None for s in stats),
        asgi_requests=attempts, real_business_http=0, real_database_writes=0,
        budget_ok=budget_ok, first_attempt=True, additional_attempts=0,
        exported=(output / "bundle/manifest.json").exists(),
        classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED",
        independent_human_reviewed=False, human_time_saved=None)


def verify(output):
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("FIRST_DEMAND_ARCHIVE_CHANGED")
    result = evidence(output)
    if result != read_json(output / "result.json"):
        raise ValueError("FIRST_DEMAND_EVIDENCE_MISMATCH")
    return dict(archive_verified=True, new_model_calls=0, result=result)


async def run(output, model_env, prior, data_dir=None):
    previous = verify_archive(prior)
    source = checkout_identity(PROJECT_ROOT)
    if (source["worktree_status"] or read_json(prior / "freeze.json")["source"] != source
            or previous["summary"]["source"] != "real"
            or previous["summary"]["attempted"] != 36
            or any(row.get("stop_paid") for row in read_json(prior / "results.json")["results"])
            or previous["summary"]["unknown_usage_calls"]
            or any(previous["summary"]["safety"].values())):
        raise ValueError("FIRST_DEMAND_SOURCE_OR_PRIOR_USAGE_GATE_FAILED")
    request = read_json((data_dir or DATA) / "first-demand.json")
    settings = settings_for(model_env)
    if public_model_config(settings) != read_json(prior / "freeze.json")["model_config"]:
        raise ValueError("FIRST_DEMAND_MODEL_CONFIG_CHANGED")
    output.mkdir(parents=True, exist_ok=False)
    copied_profiles(output / "projects")
    # Explicitly bound this new run to eight requests, including optional reads.
    target_path = output / "projects" / request["project"] / "target.json"
    target = read_json(target_path)
    target["max_requests"] = request["max_requests"]
    target_path.write_text(json.dumps(target, indent=2) + "\n", encoding="utf-8")
    project = load_goal_project(target_path.parent / "registered.project.json")
    frozen = dict(request=request, source=source, model_config=public_model_config(settings),
        profile_inventory=inventory(output / "projects"),
        frozen_at=datetime.now(timezone.utc).isoformat(), prior_archive=str(prior))
    save_evaluation(frozen, output / "freeze.json")
    identity = fingerprint(frozen)

    def guard():
        if (checkout_identity(PROJECT_ROOT) != source
                or inventory(output / "projects") != frozen["profile_inventory"]
                or fingerprint(read_json(output / "freeze.json")) != identity):
            raise ValueError("FIRST_DEMAND_FREEZE_DRIFT")

    env, transport = synthetic_runtime(project)
    observed = ObservedASGI(transport.app)
    planner = RegressionCapture(PublicTrace(OpenAICompatiblePlanner(settings, profile="generic",
        planning_constraints=True), output / "planning-inputs", guard))
    analyzer = PublicTrace(EvidenceFeedbackModel(settings), output / "feedback-inputs", guard)
    store = ReportStore(output / "reports")
    goal, report = await run_goal(project, request["user_task"], planner, store, environ=env,
        transport=observed, analyzer=analyzer, execution_guard=guard,
        budget=WorkflowBudget(model_timeout=61, workflow_timeout=300))
    guard()
    save_evaluation(capture_plan(planner.scenario).model_dump(mode="json")
                    if planner.scenario else None, output / "plan.json")
    # Save the actual run id before recomputing the receipt from source evidence.
    save_evaluation(dict(run_id=report.run_id), output / "result.json")
    result = evidence(output)
    if observed.requests != result["asgi_requests"]:
        raise ValueError("FIRST_DEMAND_REQUEST_ACCOUNTING_MISMATCH")
    if result["full_task_complete"]:
        runtime = RegressionRuntime(project.document, project.target, project.registry,
            goal.task, env, None, store)
        export_regression(output / "bundle", runtime, planner.scenario, report)
    (output / "result.json").write_text(json.dumps(evidence(output), indent=2) + "\n",
                                         encoding="utf-8")
    seal(output)
    return verify(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--dataset", choices=tuple(DATASETS), default="v1")
    args = parser.parse_args()
    result = verify(args.verify) if args.verify else asyncio.run(
        run(args.output, args.model_env, args.prior,
            DATASETS[args.dataset]))
    print(json.dumps(result, ensure_ascii=False, indent=2))
