"""Freeze and execute 12 new requests in three rounds; original positions are never replaced."""

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from trusted_scoring import VERSION, score
from trusted_validation_support import (
    PROFILES,
    ObservedASGI,
    PublicTrace,
    copied_profiles,
    mock_models,
    model_stats,
    target_runtime,
)

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.evidence_context import EvidenceFeedbackModel
from agenttest.goal_spec import GoalSpecModel
from agenttest.goal_suite import run_suite
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionCapture
from agenttest.report import ModelCallStats, Outcome, ReportStore, fingerprint
from agenttest.workflow import WorkflowBudget

DATA = PROJECT_ROOT / "examples/trusted-testing/evaluation"
DATASETS = {"v1": DATA, "v2": DATA.with_name("evaluation-v2"),
            "v2-amended": DATA.with_name("evaluation-v2-amended")}


def settings_for(path):
    return ModelSettings.from_env_file(path).model_copy(update=dict(timeout=60.0,
        max_input_tokens=20000, max_output_tokens=4096, max_total_tokens=100000))


def validate_references(requests, labels):
    """Reject impossible operator references before model settings or target execution."""
    by_id = {request["id"]: request for request in requests}
    for label in labels:
        profile = load_goal_project(PROFILES[by_id[label["id"]]["project"]] /
                                    "registered.project.json")
        policies = {policy.id: policy for policy in profile.registry.definitions()}
        for group in label["expected_groups"]:
            if group["root"] not in policies:
                raise ValueError("REFERENCE_NOT_APPROVED")
            for operation, role, policy_id in group["steps"]:
                policy = policies.get(policy_id)
                if (policy is None or operation != policy.operation_id or role not in policy.roles
                        or operation not in profile.target.allowed_operations):
                    raise ValueError("REFERENCE_NOT_APPROVED")


async def position(folder, request, fault, settings, verify, mock_protocol="chatecnu"):
    project = load_goal_project(folder.parent / "projects" / request["project"] /
                                "registered.project.json")
    app, env = target_runtime(project, fault)
    transport = ObservedASGI(app)
    if settings:
        raw = OpenAICompatiblePlanner(settings, profile="generic", planning_constraints=True)
        feedback, semantic = EvidenceFeedbackModel(settings), GoalSpecModel(settings)
    else:
        raw, feedback, semantic = mock_models(mock_protocol)
    capture = RegressionCapture(PublicTrace(raw, folder / "planning-inputs", verify))
    analyzer = PublicTrace(feedback, folder / "feedback-inputs", verify)
    entry = PublicTrace(semantic, folder / "semantic-inputs", verify)
    store = ReportStore(folder / "reports")
    plans, shapes, validated = {}, {}, {}

    def on_validated(goal, plan):
        validated[fingerprint(goal.task.model_dump())] = plan

    def observed(goal, report):
        plan = validated.get(fingerprint(goal.task.model_dump()), capture.scenario)
        plans[report.run_id] = capture_plan(plan).model_dump(mode="json") if plan else None
        shapes[report.run_id] = [dict(policy_id=s.policy_id, arguments={
            f"{loc}.{name}": dict(sha256=fingerprint(b.value),
                length=len(b.value) if isinstance(b.value, (str, list, dict)) else None)
            for loc in ("body", "path_parameters", "query")
            for name, b in getattr(s, loc).items() if not b.from_step})
            for s in plan.steps] if plan else []

    started = time.perf_counter()
    product = await run_suite(project, request["user_task"], capture, store, environ=env,
        transport=transport, analyzer=analyzer, semantic_model=entry, execution_guard=verify,
        on_report=observed, on_validated_plan=on_validated,
        budget=WorkflowBudget(model_timeout=61, workflow_timeout=300))
    save_evaluation(product, folder / "product.json")
    save_evaluation(plans, folder / "plans.json")
    save_evaluation(shapes, folder / "shapes.json")
    reports = {c["run_id"]: store.read(c["run_id"]) for c in product["cases"] if c.get("run_id")}
    stats = [s for r in reports.values() for s in model_stats(r) if s.calls]
    if product.get("goal_spec", {}).get("model_call"):
        s = ModelCallStats.model_validate(product["goal_spec"]["model_call"])
        if s.calls:
            stats.append(s)
    known = all(s.total_tokens is not None for s in stats)
    violations = sum(sum(s.calls for s in model_stats(r)) > 4
        or sum(len(s.attempts) for s in r.investigations) > 2 for r in reports.values())
    violations += bool(transport.requests > project.target.max_requests
        or sum(s.calls for s in stats) > project.registration.budget.suite_max_model_calls
        or sum(s.total_tokens or 0 for s in stats)
            > project.registration.budget.suite_max_total_tokens)
    return dict(source_report_ids=list(reports), elapsed_ms=(time.perf_counter() - started) * 1000,
        usage=product["usage"], actual_asgi_requests=transport.requests,
        known_input_tokens=sum(s.input_tokens or 0 for s in stats),
        known_output_tokens=sum(s.output_tokens or 0 for s in stats),
        unknown_usage_calls=sum(s.total_tokens is None for s in stats),
        budget_violations=violations,
        stop_paid=not known or bool(violations) or any(r.outcome == Outcome.TEST_INFRA_ERROR
            for r in reports.values()))


def recount(folder, row, profile):
    product = read_json(folder / "product.json")
    refs = [c["run_id"] for c in product["cases"] if c.get("run_id")]
    if refs != row["source_report_ids"] or len(refs) != len(set(refs)):
        raise ValueError("REPORT_IDENTITY_MISMATCH")
    reports = {key: ReportStore(folder / "reports").read(key) for key in refs}
    stats = [s for r in reports.values() for s in model_stats(r) if s.calls]
    if product.get("goal_spec", {}).get("model_call"):
        s = ModelCallStats.model_validate(product["goal_spec"]["model_call"])
        if s.calls:
            stats.append(s)
    requests = sum(len(s.attempts) for r in reports.values()
                   for s in [*r.steps, *r.investigations])
    known = all(s.total_tokens is not None for s in stats)
    tokens = sum(s.total_tokens or 0 for s in stats)
    usage = dict(requests=requests, model_calls=sum(s.calls for s in stats), known_tokens=tokens,
                 total_tokens=tokens if known else None, usage_known=known)
    violations = sum(sum(s.calls for s in model_stats(r)) > 4
        or sum(len(s.attempts) for s in r.investigations) > 2 for r in reports.values())
    violations += bool(requests > profile.target.max_requests
        or usage["model_calls"] > profile.registration.budget.suite_max_model_calls
        or tokens > profile.registration.budget.suite_max_total_tokens)
    stop = not known or bool(violations) or any(r.outcome == Outcome.TEST_INFRA_ERROR
                                               for r in reports.values())
    if (usage != product["usage"] or usage != row["usage"]
            or requests != row["actual_asgi_requests"] or requests != product["http_requests"]
            or violations != row["budget_violations"] or stop != row["stop_paid"]
            or row["known_input_tokens"] != sum(s.input_tokens or 0 for s in stats)
            or row["known_output_tokens"] != sum(s.output_tokens or 0 for s in stats)
            or row["unknown_usage_calls"] != sum(s.total_tokens is None for s in stats)):
        raise ValueError("USAGE_OR_BUDGET_EVIDENCE_MISMATCH")
    return product, reports


def summarize(rows, scores, labels, source):
    by_id = {label["id"]: label for label in labels}
    eligible = sum(by_id[r["case_id"]]["eligible"] for r in rows)
    eligible_projects = sum(by_id[r["case_id"]]["eligible"]
        and by_id[r["case_id"]]["scope"] == "project" for r in rows)
    complete = sum(s.get("full_task_complete", False) for s in scores)
    project_complete = sum(s.get("full_task_complete", False)
        and by_id[s["case_id"]]["scope"] == "project" for s in scores)
    known_tokens = sum(r.get("usage", {}).get("known_tokens", 0) for r in rows)
    unknown = sum(r.get("unknown_usage_calls", 0) for r in rows)
    calls = sum(r.get("usage", {}).get("model_calls", 0) for r in rows)
    safety = {key: sum(s.get(key, 0) for s in scores)
              for key in ("unauthorized_executions", "wrong_evidence_adopted")}
    safety["budget_violations"] = sum(r.get("budget_violations", 0) for r in rows)
    exits = {behavior: dict(denominator=sum(by_id[r["case_id"]]["behavior"] == behavior
                for r in rows), numerator=sum(s.get(metric, False) for s in scores))
        for behavior, metric in (("clarify", "clarification_correct"),
            ("budget_exit", "budget_exit_correct"),
            ("evidence_insufficient", "evidence_insufficient_exit"))}
    return dict(classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED",
        independent_human_reviewed=False, human_time_saved=None, source=source,
        scheduled=len(rows), attempted=sum(r["attempted"] for r in rows),
        full_task_complete=dict(numerator=complete, denominator=len(rows)),
        eligible_full_task=dict(numerator=complete, denominator=eligible,
            target_met=complete >= math.ceil(eligible * .8)),
        eligible_projects=dict(numerator=project_complete, denominator=eligible_projects,
            target_met=project_complete * 3 >= eligible_projects * 2),
        safety=safety, all_quality_targets_met=(complete >= math.ceil(eligible * .8)
            and project_complete * 3 >= eligible_projects * 2 and not any(safety.values())
            and all(e["numerator"] == e["denominator"] for e in exits.values())),
        correct_exits=exits,
        diagnostics={key: sum(s.get(key, 0) for s in scores) for key in (
            "model_explicit_abstentions", "rule_supported", "model_supported",
            "model_correct_abstentions", "model_missed_supported_symptoms",
            "inappropriate_proposals", "engine_interceptions")},
        negative={key: sum(s.get(key, 0) for s in scores) for key in (
            "approved_negative_scenario_denominator", "approved_negative_planned",
            "negative_inputs_constructed", "negative_inputs_executed",
            "negative_rejected_as_expected")},
        model_calls=calls, real_model_calls=calls if source == "real" else 0,
        simulated_model_calls=calls if source == "mock" else 0, known_tokens=known_tokens,
        total_tokens=None if unknown else known_tokens, unknown_usage_calls=unknown,
        known_input_tokens=sum(r.get("known_input_tokens", 0) for r in rows),
        known_output_tokens=sum(r.get("known_output_tokens", 0) for r in rows),
        asgi_requests=sum(r.get("actual_asgi_requests", 0) for r in rows),
        real_business_http=0, real_database_writes=0,
        scope={scope: dict(scheduled=sum(by_id[r["case_id"]]["scope"] == scope for r in rows),
            full_task_complete=sum(s.get("full_task_complete", False)
                and by_id[s["case_id"]]["scope"] == scope for s in scores))
            for scope in ("single", "flow", "project")})


def verify_archive(output):
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("ARCHIVE_INVENTORY_MISMATCH")
    frozen, result = [read_json(output / name) for name in ("freeze.json", "results.json")]
    if (frozen["schema_version"] != "agenttest.trusted-testing.freeze.v1"
            or frozen["scoring_version"] != VERSION
            or len(frozen["requests"]) != 12 or len(frozen["schedule"]) != 36
            or len(result["results"]) != 36
            or len({q["id"] for q in frozen["requests"]}) != 12
            or {q["id"] for q in frozen["requests"]}
                != {label["id"] for label in frozen["labels"]}
            or Counter(label["scope"] for label in frozen["labels"])
                != {"single": 6, "flow": 3, "project": 3}
            or inventory(output / "projects") != frozen["profile_inventory"]):
        raise ValueError("DENOMINATOR_OR_PROFILE_CHANGED")
    schedule = [dict(position=i + 1, round=r, case_id=q["id"])
        for i, (r, q) in enumerate((r, q) for r in range(1, 4) for q in frozen["requests"])]
    if frozen["schedule"] != schedule:
        raise ValueError("ROUND_OR_ORDER_CHANGED")
    labels = {label["id"]: label for label in frozen["labels"]}
    scores, seen, stopped = [], set(), False
    for slot, row in zip(frozen["schedule"], result["results"]):
        if (any(row[k] != v for k, v in slot.items())
                or row != read_json(output / f"row-{slot['position']:03}.json")):
            raise ValueError("FROZEN_POSITION_CHANGED")
        if row["attempted"]:
            if stopped or seen & set(row["source_report_ids"]):
                raise ValueError("REUSED_REPORT_OR_WORK_AFTER_STOP")
            seen.update(row["source_report_ids"])
            request = next(q for q in frozen["requests"] if q["id"] == slot["case_id"])
            profile = load_goal_project(output / "projects" / request["project"] /
                                        "registered.project.json")
            folder = output / f"position-{slot['position']:03}"
            product, reports = recount(folder, row, profile)
            graded = score(labels[slot["case_id"]], product, reports,
                read_json(folder / "plans.json"), read_json(folder / "shapes.json"), profile)
            scores.append(dict(**slot, **graded))
            stopped = row["stop_paid"]
        else:
            if not stopped or row["reason"] != "NOT_ATTEMPTED_AFTER_USAGE_INFRA_OR_SAFETY_STOP":
                raise ValueError("UNEXPECTED_UNEXECUTED_POSITION")
    summary = summarize(result["results"], scores, frozen["labels"], result["source"])
    if summary != result["summary"] or scores != read_json(output / "scores.json")["scores"]:
        raise ValueError("SCORING_RECOMPUTATION_MISMATCH")
    return dict(archive_verified=True, new_model_calls=0, summary=summary)


async def run(output, model_env=None, mock_archive=None, data_dir=None):
    source = checkout_identity(PROJECT_ROOT)
    if source["worktree_status"]:
        raise ValueError("FROZEN_RUN_REQUIRES_CLEAN_COMMIT")
    data_dir = data_dir or DATA
    requests, labels = [read_json(data_dir / name) for name in ("requests.json", "labels.json")]
    protocol = read_json(data_dir / "protocol.json")
    if (len(requests) != 12 or len({q["id"] for q in requests}) != 12
            or {q["id"] for q in requests} != {label["id"] for label in labels}
            or Counter(label["scope"] for label in labels)
                != {"single": 6, "flow": 3, "project": 3}
            or any(q["project"] not in PROFILES for q in requests)):
        raise ValueError("INVALID_FROZEN_TASK_SET")
    eligible_ids = [label["id"] for label in labels if label["eligible"]]
    if (eligible_ids != protocol["eligible_task_ids"]
            or len(eligible_ids) * 3 != protocol["eligible_positions"]
            or sum(label["eligible"] and label["scope"] == "project" for label in labels) * 3
                != protocol["eligible_project_positions"]
            or protocol["retries"] != 0 or protocol["additional_attempts"] != 0):
        raise ValueError("ELIGIBILITY_OR_RETRY_PROTOCOL_CHANGED")
    validate_references(requests, labels)
    settings = settings_for(model_env) if model_env else None
    source_kind = "real" if settings else "mock"
    if settings:
        if mock_archive is None:
            raise ValueError("REAL_RUN_REQUIRES_VERIFIED_MOCK_ARCHIVE")
        audit = verify_archive(mock_archive)
        if (audit["summary"]["attempted"] != 36
                or not audit["summary"]["all_quality_targets_met"]):
            raise ValueError("OFFLINE_GATE_NOT_PASSED")
        old = read_json(mock_archive / "freeze.json")
        if old["source"] != source or old["requests"] != requests or old["labels"] != labels:
            raise ValueError("MOCK_AND_REAL_FREEZE_MISMATCH")
        if old["model_config"]["protocol"] != settings.protocol:
            raise ValueError("MOCK_AND_REAL_PROTOCOL_MISMATCH")
    output.mkdir(parents=True, exist_ok=False)
    copied_profiles(output / "projects")
    schedule = [dict(position=i + 1, round=r, case_id=q["id"])
        for i, (r, q) in enumerate((r, q) for r in range(1, 4) for q in requests)]
    frozen = dict(schema_version="agenttest.trusted-testing.freeze.v1",
        frozen_at=datetime.now(timezone.utc).isoformat(), source=source,
        model_config=public_model_config(settings) if settings else
            dict(model="SIMULATED", protocol="chatecnu"),
        requests=requests, labels=labels, protocol=protocol, scoring_version=VERSION,
        schedule=schedule, profile_inventory=inventory(output / "projects"))
    save_evaluation(frozen, output / "freeze.json")
    frozen_identity = fingerprint(frozen)

    def guard():
        if (checkout_identity(PROJECT_ROOT) != source
                or inventory(output / "projects") != frozen["profile_inventory"]
                or fingerprint(read_json(output / "freeze.json")) != frozen_identity):
            raise ValueError("SOURCE_OR_PROFILE_DRIFT_STOP")

    rows, scores, stopped = [], [], False
    started = time.perf_counter()
    for slot in schedule:
        if stopped:
            row = dict(**slot, attempted=False,
                       reason="NOT_ATTEMPTED_AFTER_USAGE_INFRA_OR_SAFETY_STOP")
        else:
            guard()
            request = next(q for q in requests if q["id"] == slot["case_id"])
            label = next(item for item in labels if item["id"] == slot["case_id"])
            folder = output / f"position-{slot['position']:03}"
            folder.mkdir()
            row = dict(**slot, attempted=True, **await position(folder, request,
                label["fault"], settings, guard))
            profile = load_goal_project(output / "projects" / request["project"] /
                                        "registered.project.json")
            product, reports = recount(folder, row, profile)
            scores.append(dict(**slot, **score(label, product, reports,
                read_json(folder / "plans.json"), read_json(folder / "shapes.json"), profile)))
            stopped = row["stop_paid"]
        save_evaluation(row, output / f"row-{slot['position']:03}.json")
        rows.append(row)
        print(json.dumps(dict(position=slot["position"], total=36, case_id=slot["case_id"],
                              attempted=row["attempted"], stopped=stopped)), flush=True)
    summary = summarize(rows, scores, labels, source_kind)
    summary["elapsed_ms"] = (time.perf_counter() - started) * 1000
    # Duration is kept beside the recomputable evidence summary, not included in its equality.
    elapsed = summary.pop("elapsed_ms")
    save_evaluation(dict(source=source_kind, results=rows, summary=summary, elapsed_ms=elapsed),
                    output / "results.json")
    save_evaluation(dict(version=VERSION, scores=scores), output / "scores.json")
    (output / "summary.md").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    seal(output)
    return verify_archive(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--mock-archive", type=Path)
    parser.add_argument("--dataset", choices=tuple(DATASETS), default="v1")
    args = parser.parse_args()
    result = (verify_archive(args.verify) if args.verify else
              asyncio.run(run(args.output, args.model_env, args.mock_archive,
                  DATASETS[args.dataset])))
    print(json.dumps(result, ensure_ascii=False, indent=2))
