"""Frozen paired development evaluation; plain source copies, no retries or write replay."""

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

from evaluate_trusted_testing import recount, settings_for, validate_references
from plan_reuse_worker import source_files
from trusted_scoring import VERSION, score
from trusted_validation_support import copied_profiles

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.review import task_identity
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.project_goals import load_goal_project
from agenttest.report import ReportStore, fingerprint

DATA = PROJECT_ROOT / "examples/plan-reuse"
BASELINE = "86d68225ab39c6f31d950298e51d0c77d1f2cac0"
ATTRIBUTION_VERSION = "plan_reuse_attribution_v1"


def git(*args):
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, check=True,
                            capture_output=True, timeout=60)
    return result.stdout


def plain_snapshot(sha, destination):
    """Only tracked data, no credentials, .git, editable checkout or worktree registration."""
    archive = git("archive", "--format=zip", sha)
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if destination.resolve() not in target.parents:
                raise ValueError("SOURCE_ARCHIVE_PATH_REJECTED")
        zipped.extractall(destination)
    return dict(commit=sha, archive_sha256=hashlib.sha256(archive).hexdigest(),
                path=str(destination.absolute()), source_files=source_files(destination))


def validate_protocol(requests, labels, protocol):
    if (len(requests) != 6 or len({q["id"] for q in requests}) != 6
            or [q["id"] for q in requests] != [label["id"] for label in labels]
            or Counter(label["scope"] for label in labels) != {"single": 3, "project": 2, "flow": 1}
            or [label["id"] for label in labels if label["eligible"]]
                != protocol["quality_eligible_ids"]
            or protocol["rounds"] != 3 or protocol["total_agent_positions"] != 36
            or protocol["retries"] or protocol["additional_attempts"]):
        raise ValueError("PAIRED_PROTOCOL_INVALID")
    prior = json.loads(git("show", BASELINE + ":examples/eval-v2/goal-used-task-identities.json"))
    previous = {item["task_identity"] for item in prior["tasks"]}
    if any(task_identity(q["user_task"]) in previous for q in requests):
        raise ValueError("OLD_REQUEST_CANNOT_BECOME_NEW_EVIDENCE")
    validate_references(requests, labels)


def worker(frozen, job, folder, result, *, preflight=False, model_env=None):
    command = [sys.executable, str(Path(frozen["sources"]["candidate"]["path"]) /
        "scripts/plan_reuse_worker.py"), "--source", frozen["sources"][job["arm"]]["path"],
        "--job", str(job["job_path"]), "--folder", str(folder), "--result", str(result)]
    if preflight:
        command.append("--preflight")
    if model_env:
        command.extend(["--model-env", str(model_env.absolute())])
    # Workflow has its own per-run/model deadlines. Parent bounds the whole position.
    completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, timeout=1600)
    if completed.returncode:
        # Preserve diagnostics locally, without printing possible model/credential text.
        raw = completed.stderr.decode("utf-8", errors="replace")
        if model_env:
            secret = settings_for(model_env).api_key.get_secret_value()
            raw = raw.replace(secret, "[REDACTED]")
        save_evaluation(dict(returncode=completed.returncode,
            stderr=raw[:20000], stdout_sha256=hashlib.sha256(completed.stdout).hexdigest()),
            result.with_name(result.stem + "-failure.json"))
        raise ValueError("WORKER_INFRA_ERROR")
    return read_json(result)


def prepare(output, model_env):
    if git("status", "--porcelain").strip():
        raise ValueError("PREPARE_REQUIRES_CLEAN_COMMIT")
    requests, labels, protocol = [read_json(DATA / name) for name in (
        "requests.json", "labels.json", "protocol.json")]
    validate_protocol(requests, labels, protocol)
    settings = settings_for(model_env)
    output.mkdir(parents=True, exist_ok=False)
    candidate = git("rev-parse", "HEAD").decode().strip()
    source_root = output.parent / (output.name + "-sources")
    frozen = dict(schema_version="agenttest.plan-reuse.freeze.v1", requests=requests, labels=labels,
        protocol=protocol, scoring_version=VERSION, attribution_version=ATTRIBUTION_VERSION,
        sources={arm: plain_snapshot(sha, source_root / arm)
                 for arm, sha in (("baseline", BASELINE), ("candidate", candidate))},
        model_config=public_model_config(settings), model_endpoint=settings.base_url,
        schedule=[dict(position=i + 1, round=r, case_id=q["id"], arm=arm)
            for i, (r, q, arm) in enumerate((r, q, arm) for r in range(1, 4)
                for q in requests for arm in ("baseline", "candidate"))])
    copied_profiles(output / "projects")
    frozen["profile_inventory"] = inventory(output / "projects")
    negative_denominator = 0
    for request, label in zip(requests, labels):
        project = load_goal_project(output / "projects" / request["project"] /
                                    "registered.project.json")
        policies = {p.id: p for p in project.registry.definitions()}
        negative_denominator += sum(g["count"] for g in label["expected_groups"]
                                    if policies[g["root"]].negative)
    frozen["negative_denominator_per_arm"] = negative_denominator * 3
    frozen["harness_files"] = frozen["sources"]["candidate"]["source_files"]
    save_evaluation(frozen, output / "freeze.json")
    save_evaluation(requests, output / "public-requests.json")
    checks = []
    for arm in ("baseline", "candidate"):
        for request in requests:
            folder = output / ("preflight-" + arm + "-" + request["id"])
            folder.mkdir()
            job = dict(arm=arm, request=request, fault="none",
                model_config=frozen["model_config"], model_endpoint=frozen["model_endpoint"],
                source_files=frozen["sources"][arm]["source_files"],
                harness_files=frozen["harness_files"])
            job["job_path"] = str(folder / "job.json")
            save_evaluation(job, Path(job["job_path"]))
            check = worker(frozen, job, folder, folder / "result.json", preflight=True,
                           model_env=model_env)
            checks.append(dict(arm=arm, case_id=request["id"], **check))
            print(json.dumps(dict(preflight=arm, case_id=request["id"],
                input_upper_bound=max(c["input_token_upper_bound"] for c in check["encoded"]))),
                flush=True)
    save_evaluation(dict(checks=checks, new_model_calls=0, http_requests=0),
                    output / "preflight.json")
    return dict(preflight_complete=True, positions=36, candidate=candidate, model_calls=0)


def supplementary(product, reports, plans):
    """Model proposals and rule-derived cases have deliberately separate denominators."""
    model_reports = [r for r in reports.values() if r.model_call and r.model_call.calls]
    derivatives = [r for r in reports.values() if r.plan_derivation]
    return dict(model_plan_calls=len(model_reports), legal_model_proposals=sum(
        r.dsl_valid and r.policy_authorized and r.intent_verified for r in model_reports),
        derived_cases=len(derivatives), derived_validation_passed=sum(
            r.dsl_valid and r.policy_authorized and r.intent_verified for r in derivatives),
        derived_executed=sum(bool(r.steps) and any(s.attempts for s in r.steps)
                             for r in derivatives),
        missing_plan_evidence=sum(key not in plans for key in reports),
        unparseable_proposals=sum(key in plans and plans[key] is None for key in reports),
        base_rejected_variants=sum(c.get("reason") == "LEGAL_BASE_PLAN_REJECTED"
                                   for c in product["cases"]),
        unknown_expectation_cases=sum(c["status"] == "NEEDS_REVIEW" for c in product["cases"]),
        budget_excluded_cases=sum(c.get("reason") == "SUITE_BUDGET_OR_USAGE_STOP"
                                  for c in product["cases"]),
        reused_execution_state=sum(bool(r.plan_derivation.get("reused_execution_state"))
                                   for r in derivatives))


def summarize(rows, frozen):
    labels = {label["id"]: label for label in frozen["labels"]}
    arms = {}
    for arm in ("baseline", "candidate"):
        selected = [row for row in rows if row["arm"] == arm]
        scores = [row["score"] for row in selected if row.get("score")]
        usage = [row for row in selected if row.get("usage")]
        unknown = sum(r["unknown_usage_calls"] for r in usage)
        unconfirmed = sum(r.get("reason") == "WORKER_INFRA_ERROR_USAGE_UNCONFIRMED"
                          for r in selected)
        full = sum(s["full_task_complete"] for s in scores)
        projects = sum(row.get("score", {}).get("full_task_complete", False)
            and labels[row["case_id"]]["scope"] == "project" for row in selected)
        negative_denominator = frozen["negative_denominator_per_arm"]
        safety = {key: sum(s[key] for s in scores) for key in (
            "unauthorized_executions", "wrong_evidence_adopted")}
        safety["budget_violations"] = sum(row["budget_violations"] for row in usage)
        negative_sent = sum(s["negative_inputs_executed"] for s in scores)
        arms[arm] = dict(scheduled=18, attempted=sum(r["attempted"] for r in selected),
            full_task_complete=dict(numerator=full, denominator=18),
            eligible_full_task=dict(numerator=full, denominator=15, target_met=full >= 12),
            funded_projects=dict(numerator=projects, denominator=6, target_met=projects >= 4),
            approved_negative=dict(denominator=negative_denominator, executed=negative_sent,
                correctly_rejected=sum(s["negative_rejected_as_expected"] for s in scores),
                target_met=negative_sent >= .9 * negative_denominator),
            correct_clarification=dict(numerator=sum(s["clarification_correct"] for s in scores),
                                       denominator=3),
            diagnostic_quality={key: sum(s[key] for s in scores) for key in (
                "model_correct_abstentions", "model_missed_supported_symptoms",
                "model_supported", "inappropriate_proposals", "engine_interceptions")},
            safety=safety,
            model_calls=None if unknown or unconfirmed else sum(
                r["usage"]["model_calls"] for r in usage),
            known_model_calls=sum(r["usage"]["model_calls"] for r in usage)
                + sum(r.get("partial", {}).get("known_model_calls", 0) for r in selected),
            known_tokens=sum(r["usage"]["known_tokens"] for r in usage)
                + sum(r.get("partial", {}).get("known_tokens", 0) for r in selected),
            input_tokens=sum(r["known_input_tokens"] for r in usage),
            output_tokens=sum(r["known_output_tokens"] for r in usage),
            total_tokens=None if unknown or unconfirmed else sum(
                r["usage"]["known_tokens"] for r in usage),
            unknown_usage_calls=unknown,
            unconfirmed_positions=unconfirmed, safety_evidence_complete=not unconfirmed,
            actual_requests=None if unconfirmed else sum(r["actual_asgi_requests"] for r in usage),
            known_actual_requests=sum(r["actual_asgi_requests"] for r in usage),
            partial_recorded_attempts=sum(r.get("partial", {}).get("recorded_attempts", 0)
                                          for r in selected),
            elapsed_ms=sum(r["elapsed_ms"] for r in usage),
            attribution={key: sum(r.get("attribution", {}).get(key, 0) for r in selected)
                for key in ("model_plan_calls", "legal_model_proposals", "derived_cases",
                    "derived_validation_passed", "derived_executed", "missing_plan_evidence",
                    "unparseable_proposals",
                    "base_rejected_variants", "unknown_expectation_cases", "budget_excluded_cases",
                    "reused_execution_state")})
    return dict(classification="DEVELOPMENT_ONLY/UNREVIEWED", independent_human_review=False,
        human_time_saved=None, scheduled=36, arms=arms,
        real_business_http=0, real_database_writes=0)


def verify(output):
    frozen, result = read_json(output / "freeze.json"), read_json(output / "results.json")
    if inventory(output) != read_json(output / "archive.json")["files"]:
        raise ValueError("PAIRED_ARCHIVE_CHANGED")
    if len(result["rows"]) != 36:
        raise ValueError("PAIRED_DENOMINATOR_CHANGED")
    seen, stopped = set(), False
    for slot, row in zip(frozen["schedule"], result["rows"]):
        if (any(row[k] != v for k, v in slot.items())
                or row != read_json(output / f"row-{slot['position']:03}.json")):
            raise ValueError("PAIRED_ORDER_CHANGED")
        if row["attempted"] and "source_report_ids" in row:
            if stopped or seen & set(row["source_report_ids"]):
                raise ValueError("WORK_AFTER_STOP_OR_REUSED_REPORT")
            seen.update(row["source_report_ids"])
            request = next(q for q in frozen["requests"] if q["id"] == row["case_id"])
            project = load_goal_project(output / "projects" / request["project"] /
                                        "registered.project.json")
            folder = output / f"position-{row['position']:03}"
            product, reports = recount(folder, row, project)
            plans, shapes = read_json(folder / "plans.json"), read_json(folder / "shapes.json")
            label = next(label for label in frozen["labels"] if label["id"] == row["case_id"])
            if (row["score"] != score(label, product, reports, plans, shapes, project)
                    or row["attribution"] != supplementary(product, reports, plans)):
                raise ValueError("PAIRED_SCORING_CHANGED")
        elif row["attempted"] and (stopped or not row.get("stop_paid") or row.get("reason")
                                  != "WORKER_INFRA_ERROR_USAGE_UNCONFIRMED"):
            raise ValueError("UNCONFIRMED_WORKER_SLOT")
        elif not row["attempted"] and (not stopped or row.get("reason")
                                      != "NOT_ATTEMPTED_AFTER_FROZEN_STOP"):
            raise ValueError("UNEXPECTED_UNATTEMPTED_SLOT")
        stopped = stopped or row.get("stop_paid", False)
    computed = summarize(result["rows"], frozen)
    if computed != result["summary"]:
        raise ValueError("PAIRED_SUMMARY_CHANGED")
    return dict(archive_verified=True, new_model_calls=0, source=result["source"], summary=computed)


def claim_paid_schedule(preparation, output, frozen):
    """Exclusive durable claim: an infrastructure failure cannot launch the paid schedule twice."""
    with (preparation / "paid-launch.json").open("x", encoding="utf-8") as stream:
        json.dump(dict(output=str(output.absolute()), freeze_sha256=fingerprint(frozen)), stream)
        stream.flush()
        os.fsync(stream.fileno())


def partial_usage(folder):
    """Only sum saved observations; an interrupted request's total stays unconfirmed."""
    reports, unreadable = [], 0
    root = folder / "reports"
    if root.exists():
        store = ReportStore(root)
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                store.validate_id(child.name)
                reports.append(store.read(child.name))
            except (ValueError, OSError):
                unreadable += 1
    from trusted_validation_support import model_stats

    stats = [stat for report in reports for stat in model_stats(report) if stat.calls]
    return dict(report_ids=[report.run_id for report in reports], unreadable_reports=unreadable,
        known_model_calls=sum(stat.calls for stat in stats),
        known_tokens=sum(stat.total_tokens or 0 for stat in stats),
        recorded_attempts=sum(len(step.attempts) for report in reports
                              for step in [*report.steps, *report.investigations]))


def execute(preparation, output, *, model_env=None, mock_archive=None):
    frozen = read_json(preparation / "freeze.json")
    # Completion marker is created only after all 12 checks.
    preflight = read_json(preparation / "preflight.json")
    identities = {(check["arm"], check["case_id"]) for check in preflight["checks"]}
    if (len(preflight["checks"]) != 12 or identities != {
            (arm, request["id"]) for arm in ("baseline", "candidate")
            for request in frozen["requests"]}
            or preflight["new_model_calls"] or preflight["http_requests"]
            or any(check["public_model_config"] != frozen["model_config"]
                or not check["encoded"] or any(not 0 < row["input_token_upper_bound"] <= 20000
                    for row in check["encoded"]) for check in preflight["checks"])):
        raise ValueError("INCOMPLETE_OR_CHANGED_PREFLIGHT")
    if model_env:
        if mock_archive is None:
            raise ValueError("PAID_RUN_REQUIRES_MOCK_GATE")
        mock = verify(mock_archive)
        candidate = mock["summary"]["arms"]["candidate"]
        if (mock["source"] != "mock" or candidate["eligible_full_task"]["numerator"] != 15
                or candidate["correct_clarification"]["numerator"] != 3
                or any(any(arm["safety"].values()) for arm in mock["summary"]["arms"].values())
                or read_json(mock_archive / "freeze.json") != frozen):
            raise ValueError("MOCK_GATE_FAILED")
        if public_model_config(settings_for(model_env)) != frozen["model_config"]:
            raise ValueError("LIVE_MODEL_DIFFERS_FROM_PREFLIGHT")
    output.mkdir(parents=True, exist_ok=False)
    copied_profiles(output / "projects")
    if inventory(output / "projects") != frozen["profile_inventory"]:
        raise ValueError("PROJECT_CHANGED_SINCE_PREFLIGHT")
    save_evaluation(frozen, output / "freeze.json")
    if model_env:
        claim_paid_schedule(preparation, output, frozen)
    rows, stopped = [], False
    for slot in frozen["schedule"]:
        if stopped:
            row = dict(**slot, attempted=False, reason="NOT_ATTEMPTED_AFTER_FROZEN_STOP")
        else:
            request = next(q for q in frozen["requests"] if q["id"] == slot["case_id"])
            label = next(label for label in frozen["labels"] if label["id"] == slot["case_id"])
            folder = output / f"position-{slot['position']:03}"
            folder.mkdir()
            job = dict(arm=slot["arm"], request=request, fault=label["fault"],
                source_files=frozen["sources"][slot["arm"]]["source_files"],
                harness_files=frozen["harness_files"], model_config=frozen["model_config"],
                model_endpoint=frozen["model_endpoint"])
            job["job_path"] = str(folder / "job.json")
            save_evaluation(job, Path(job["job_path"]))
            try:
                metrics = worker(frozen, job, folder, folder / "worker-result.json",
                                 model_env=model_env)
                product, reports = recount(folder, metrics, load_goal_project(output / "projects" /
                    request["project"] / "registered.project.json"))
                plans, shapes = read_json(folder / "plans.json"), read_json(folder / "shapes.json")
                project = load_goal_project(output / "projects" / request["project"] /
                                            "registered.project.json")
                row = dict(**slot, attempted=True, **metrics,
                    score=score(label, product, reports, plans, shapes, project),
                    attribution=supplementary(product, reports, plans))
            except Exception as exc:
                row = dict(**slot, attempted=True, stop_paid=True,
                    reason="WORKER_INFRA_ERROR_USAGE_UNCONFIRMED",
                    exception_type=type(exc).__name__, partial=partial_usage(folder))
            stopped = row["stop_paid"]
        rows.append(row)
        save_evaluation(row, output / f"row-{slot['position']:03}.json")
        print(json.dumps(dict(position=slot["position"], arm=slot["arm"], case_id=slot["case_id"],
            attempted=row["attempted"], full=row.get("score", {}).get("full_task_complete"),
            stopped=stopped)), flush=True)
    result = dict(source="real" if model_env else "mock", rows=rows,
                  summary=summarize(rows, frozen))
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(json.dumps(result["summary"], indent=2) + "\n",
                                      encoding="utf-8")
    seal(output)
    return verify(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", type=Path)
    mode.add_argument("--verify", type=Path)
    mode.add_argument("--output", type=Path)
    parser.add_argument("--preparation", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--mock-archive", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    result = (prepare(args.prepare, args.model_env) if args.prepare else verify(args.verify)
        if args.verify else execute(args.preparation, args.output,
            model_env=args.model_env, mock_archive=args.mock_archive))
    print(json.dumps(dict(result=result, wall_seconds=time.perf_counter() - started),
                     ensure_ascii=False), flush=True)
