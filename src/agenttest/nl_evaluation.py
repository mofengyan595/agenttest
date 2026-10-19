"""Frozen natural-language comparison using separate baseline/candidate processes."""

import io
import json
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.feedback_check import stats_for
from agenttest.llm_planner import ModelSettings
from agenttest.nl_scoring import GRADING_VERSION, score_nl
from agenttest.nl_tasks import cases
from agenttest.paired_evaluation import stop_paid_schedule
from agenttest.report import ReportStore, fingerprint
from agenttest.task_contract import TaskContract

BASELINE = "ecc362cb19af79ea4e4102c3aac1ed7014afefb7"
BASELINE_ROOT = PROJECT_ROOT / "runs/nlr-baseline-ecc362c"
SELECTION_SCOPE = {"src/agenttest/evidence_context.py", "src/agenttest/feedback_runtime.py",
                   "src/agenttest/nl_evaluation.py", "scripts/evaluate_nl_reliability.py"}


def runtime_files_identity():
    names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=PROJECT_ROOT, capture_output=True, check=True, text=True).stdout.splitlines()
    return {str(PROJECT_ROOT / n): fingerprint((PROJECT_ROOT / n).read_bytes().hex())
        for n in names if n.startswith(("src/", "scripts/", "examples/nl-reliability/"))}


def selected_candidate(prior_phase, selection_path, runtime_files):
    """Bind the single pre-validation input fix to the unchanged development archive.

    This is a developer selection record, never independent review or permission
    to change tasks, scoring, assertions, authorization, execution or symptom rules.
    """
    prior = read_json(prior_phase / "freeze.json")
    old = read_json(prior_phase / "execution-identity.json")
    changed = sorted(Path(p).relative_to(PROJECT_ROOT).as_posix()
                     for p, digest in runtime_files.items() if old.get(p) != digest)
    removed = [p for p in old if str(PROJECT_ROOT) in p and p not in runtime_files
        and Path(p).relative_to(PROJECT_ROOT).as_posix().startswith((
            "src/", "scripts/", "examples/nl-reliability/"))]
    if not changed and not removed:
        return None
    if selection_path is None or removed or not set(changed) <= SELECTION_SCOPE:
        raise ValueError("Candidate change outside bounded pre-validation selection scope")
    selection = read_json(selection_path)
    required = dict(development_archive_sha256=fingerprint(read_json(prior_phase / "archive.json")),
        development_runtime_sha256=prior["candidate_runtime_sha256"],
        selected_runtime_sha256=fingerprint(runtime_files), changed_runtime_files=changed,
        grading_version=GRADING_VERSION, classification="DEVELOPMENT_ONLY",
        review_status="UNREVIEWED",
        additional_paid_development_calls=0)
    if any(selection.get(k) != v for k, v in required.items()):
        raise ValueError("Pre-validation candidate selection identity mismatch")
    return selection


def baseline_snapshot():
    """Verify exported contents against Git's exact object, without branch/worktree writes."""
    raw = subprocess.run(["git", "archive", "--format=zip", BASELINE], cwd=PROJECT_ROOT,
        capture_output=True, check=True, timeout=30).stdout
    archive = zipfile.ZipFile(io.BytesIO(raw))
    if not BASELINE_ROOT.exists():
        BASELINE_ROOT.mkdir(parents=True)
        archive.extractall(BASELINE_ROOT)
    files = {}
    for name in archive.namelist():
        if name.endswith("/"):
            continue
        actual = (BASELINE_ROOT / name).read_bytes()
        if actual != archive.read(name):
            raise ValueError("Baseline export differs from exact Git object")
        if name.startswith("src/"):
            files[str(BASELINE_ROOT / name)] = fingerprint(actual.hex())
    if (BASELINE_ROOT / ".git").exists():
        raise ValueError("Baseline must be a source export, not a checkout/worktree")
    return files


def schedule_for(selected, rounds):
    return [dict(case_id=case["id"], category=case["category"], round=r, arm=arm)
        for r in range(1, rounds + 1) for index, case in enumerate(selected)
        for arm in (("baseline", "candidate") if (index + r) % 2 else
                    ("candidate", "baseline"))]


def worker_command(case_file, output, arm, mode, identity_file, model_env):
    root = BASELINE_ROOT if arm == "baseline" else PROJECT_ROOT
    command = [sys.executable, str(PROJECT_ROOT / "scripts/run_nl_worker.py"),
        "--engine-root", str(root), "--case-file", str(case_file), "--output", str(output),
        "--arm", arm, "--mode", mode, "--identity-file", str(identity_file)]
    if model_env:
        command += ["--model-env", str(model_env)]
    return command


def run_worker(*args):
    result = subprocess.run(worker_command(*args), cwd=PROJECT_ROOT, capture_output=True,
                            timeout=360)
    if result.returncode:
        # Errors can contain operator paths; never forward arbitrary exception text.
        raise ValueError("Isolated worker failed; no additional paid positions allowed")


def row_for(case, slot, folder, elapsed):
    prepared, observed = (read_json(folder / n) for n in ("prepared.json", "worker.json"))
    report = ReportStore(folder / "reports").read(observed["run_id"])
    task = TaskContract.model_validate_json(json.dumps(prepared["task"]))
    plan = Scenario.model_validate_json(json.dumps(observed["scenario"])) \
        if observed["scenario"] else None
    stats = [s for s in stats_for(report) if s.calls]
    known = all(s.total_tokens is not None for s in stats)
    return dict(**slot, run_id=report.run_id, report_sha256=fingerprint(report.model_dump()),
        scenario=observed["scenario"], outcome=report.outcome.value,
        diagnostic=report.diagnostic.model_dump() if report.diagnostic else None,
        grade=score_nl(case, task, report, plan),
        model_calls=sum(s.calls for s in stats), usage_known=known,
        input_tokens=sum(s.input_tokens for s in stats) if known else None,
        output_tokens=sum(s.output_tokens for s in stats) if known else None,
        total_tokens=sum(s.total_tokens for s in stats) if known else None,
        business_requests=sum(len(s.attempts) for s in report.steps),
        investigation_requests=sum(len(s.attempts) for s in report.investigations),
        workflow_elapsed_ms=observed["elapsed_ms"], position_elapsed_ms=elapsed,
        stop_paid=stop_paid_schedule(report))


def summarize(rows, schedule, source):
    groups = {}
    for arm in ("baseline", "candidate"):
        for category in ("all", "healthy", "access", "readback", "insufficient"):
            slots = [s for s in schedule if s["arm"] == arm
                     and (category == "all" or s["category"] == category)]
            called = [r for r in rows if r.get("run_id") and r["arm"] == arm
                      and (category == "all" or r["category"] == category)]
            unknown = [r for r in rows if r.get("attempted_unknown") and r["arm"] == arm
                       and (category == "all" or r["category"] == category)]
            if not slots:
                continue
            keys = list(called[0]["grade"]) if called else []
            groups[arm + "/" + category] = dict(denominator=len(slots),
                attempted=len(called) + len(unknown), reports=len(called),
                unknown_usage_positions=len(unknown),
                metrics={k: sum(r["grade"][k] for r in called) for k in keys},
                model_calls=sum(r["model_calls"] for r in called) if not unknown else None,
                input_tokens=sum(r["input_tokens"] for r in called)
                    if not unknown and all(r["usage_known"] for r in called) else None,
                output_tokens=sum(r["output_tokens"] for r in called)
                    if not unknown and all(r["usage_known"] for r in called) else None,
                total_tokens=sum(r["total_tokens"] for r in called)
                    if not unknown and all(r["usage_known"] for r in called) else None,
                business_requests=sum(r["business_requests"] for r in called),
                investigation_requests=sum(r["investigation_requests"] for r in called),
                position_elapsed_ms=round(sum(r["position_elapsed_ms"] for r in called), 3))
    return dict(classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED", human_reviewed=0,
        formal_eval_v2_score=None,
        grading_version=GRADING_VERSION, source=source, scheduled=len(schedule),
        attempted=sum(bool(r.get("run_id") or r.get("attempted_unknown")) for r in rows),
        groups=groups,
        real_business_http=0, database_writes=0, human_time_saved=None, generalization=None)


def run_nl(output: Path, split: str, *, model_env=None, mock_acceptance=None, prior_phase=None,
           candidate_selection=None):
    output = output.resolve()
    model_env = model_env.resolve() if model_env else None
    private_output(PROJECT_ROOT, output)
    identity = checkout_identity(PROJECT_ROOT)
    if model_env and identity["worktree_status"]:
        raise ValueError("Paid schedule requires clean committed source")
    selected = cases(split)
    if (split == "development" and len(selected) > 6
            or split == "validation" and len(selected) != 12):
        raise ValueError("Task count exceeds frozen stage scope")
    if model_env:
        if mock_acceptance is None:
            raise ValueError("Matching completed offline acceptance required")
        verify_nl(mock_acceptance)
        mock_freeze = read_json(mock_acceptance / "freeze.json")
        mock_result = read_json(mock_acceptance / "results.json")
        if (mock_freeze["git"] != identity or mock_freeze["cases"] != selected
            or mock_result["source"] != "mock_transport"
            or mock_result["attempted"] != mock_result["scheduled"]
            or any(g["metrics"].get("product_complete", 0) != g["denominator"]
                   for g in mock_result["groups"].values())):
            raise ValueError("Mock acceptance differs or did not complete every product target")
    if split == "validation":
        if prior_phase is None:
            raise ValueError("Completed real development phase required before validation")
        verify_nl(prior_phase)
        prior = read_json(prior_phase / "results.json")
        if (prior["source"] != "real" or prior["attempted"] != prior["scheduled"]
            or any(r.get("attempted_unknown") or not r.get("usage_known")
                   for r in prior["results"])):
            raise ValueError("Development phase is incomplete")
    baseline_files = baseline_snapshot()
    runtime_files = runtime_files_identity()
    selection = selected_candidate(prior_phase, candidate_selection, runtime_files) \
        if split == "validation" else None
    files = {**baseline_files, **runtime_files}
    if model_env:
        files[str(model_env)] = fingerprint(model_env.read_bytes().hex())

    def guard():
        if checkout_identity(PROJECT_ROOT) != identity:
            raise ValueError("Frozen source changed")
        if any(fingerprint(Path(p).read_bytes().hex()) != v for p, v in files.items()):
            raise ValueError("Frozen engine or model configuration changed")

    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(files, output / "execution-identity.json")
    for case in selected:
        save_evaluation(case, output / (case["id"] + ".json"))
    settings = ModelSettings.from_env_file(model_env) if model_env else ModelSettings(
        model="offline-nl-reliability", base_url="https://offline.invalid",
        api_key="synthetic-nl-only")
    settings = settings.model_copy(update={"timeout": 60.0})
    prepared = {}
    for case in selected:
        for arm in ("baseline", "candidate"):
            directory = output / "preflight" / (case["id"] + "-" + arm)
            run_worker(output / (case["id"] + ".json"), directory, arm, "prepare",
                       output / "execution-identity.json", model_env)
            prepared[case["id"] + "/" + arm] = read_json(directory / "prepared.json")
        left, right = (prepared[case["id"] + "/" + a] for a in ("baseline", "candidate"))
        if any(left[k] != right[k] for k in left if k != "payload"):
            raise ValueError("Arms do not share identical task, context and trusted inputs")
    schedule = schedule_for(selected, 3 if split == "validation" else 1)
    freeze = dict(schema_version="agenttest.nl-reliability.freeze.v1", git=identity,
        baseline_commit=BASELINE, baseline_export_sha256=fingerprint(baseline_files),
        candidate_runtime_sha256=fingerprint(runtime_files),
        candidate_selection=selection,
        prior_phase_archive_sha256=fingerprint(read_json(prior_phase / "archive.json"))
            if prior_phase else None,
        frozen_at=datetime.now(timezone.utc).isoformat(), cases=selected, split=split,
        schedule=schedule, prepared=prepared, grading_version=GRADING_VERSION,
        model=public_model_config(settings), max_calls=len(schedule) * 4,
        max_reserved_tokens=len(schedule) * 4 * (
            settings.max_input_tokens + settings.max_output_tokens),
        per_run=dict(planning_calls=1, feedback_calls=3, readonly_requests=2, implicit_retries=0),
        intervention=dict(shared="public operator-owned policy purposes and original inputs",
            candidate="planning relations/joint schema, original-policy feedback context, "
                      "independent symptom output"))
    save_evaluation(freeze, output / "freeze.json")
    guard()
    by_id = {c["id"]: c for c in selected}
    rows, stop = [], None
    started = time.perf_counter()
    for position, slot in enumerate(schedule, 1):
        case, folder = by_id[slot["case_id"]], output / f"position-{position:03}"
        if stop:
            row = dict(**slot, run_id=None, not_attempted_reason=stop)
        else:
            guard()
            began = time.perf_counter()
            try:
                run_worker(output / (case["id"] + ".json"), folder, slot["arm"],
                    "real" if model_env else "mock", output / "execution-identity.json", model_env)
                guard()
                if read_json(folder / "prepared.json") != prepared[case["id"] + "/" + slot["arm"]]:
                    raise ValueError("Prepared model payload or trusted inputs changed")
                row = row_for(case, slot, folder, round((time.perf_counter() - began) * 1000, 3))
                if row["stop_paid"]:
                    stop = "UNKNOWN_USAGE_OR_EXTERNAL_FAILURE_STOP"
            except (ValueError, OSError, subprocess.SubprocessError):
                row = dict(**slot, run_id=None, attempted_unknown=True,
                           not_attempted_reason="WORKER_OR_SOURCE_FAILURE_USAGE_UNKNOWN")
                stop = "WORKER_OR_SOURCE_FAILURE_USAGE_UNKNOWN"
        rows.append(row)
        save_evaluation(row, output / f"row-{position:03}.json")
        print(json.dumps(dict(position=position, **slot, outcome=row.get("outcome"),
            tokens=row.get("total_tokens"))), flush=True)
    result = dict(**summarize(rows, schedule, "real" if model_env else "mock_transport"),
        schedule_elapsed_ms=round((time.perf_counter() - started) * 1000, 3), results=rows)
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(json.dumps(
        {k: v for k, v in result.items() if k != "results"}, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n")
    seal(output)
    verify_nl(output)
    return {k: v for k, v in result.items() if k != "results"}


def verify_nl(output: Path):
    """Cold regrade sealed reports without starting a worker, model or business target."""
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("Natural-language archive seal changed")
    frozen, result = (read_json(output / n) for n in ("freeze.json", "results.json"))
    if frozen["grading_version"] != GRADING_VERSION:
        raise ValueError("Unknown natural-language grading version")
    by_id = {c["id"]: c for c in frozen["cases"]}
    rows, schedule = result["results"], frozen["schedule"]
    if len(rows) != len(schedule):
        raise ValueError("Natural-language denominator changed")
    ids = set()
    for position, (slot, row) in enumerate(zip(schedule, rows), 1):
        if (any(slot[k] != row[k] for k in slot)
                or row != read_json(output / f"row-{position:03}.json")):
            raise ValueError("Natural-language schedule or row changed")
        if not row.get("run_id"):
            continue
        if row["run_id"] in ids:
            raise ValueError("Target/session report reused")
        ids.add(row["run_id"])
        folder = output / f"position-{position:03}"
        computed = row_for(by_id[slot["case_id"]], slot, folder, row["position_elapsed_ms"])
        if row != computed or row["model_calls"] > 4 or row["investigation_requests"] > 2:
            raise ValueError("Natural-language cold score or safety mismatch")
    actual = summarize(rows, schedule, result["source"])
    expected = {k: v for k, v in result.items() if k not in {"results", "schedule_elapsed_ms"}}
    if actual != expected:
        raise ValueError("Natural-language summary changed")
    return dict(archive_verified=True, scheduled=len(schedule), reports=len(ids), new_model_calls=0)
