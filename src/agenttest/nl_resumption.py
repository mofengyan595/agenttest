"""Continue only never-started evaluation slots after explicit operator instruction.

This starts fresh synthetic sessions. It does not retry a request, resume a
business workflow, reconcile unknown billing, or change an evaluated engine.
"""

import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.nl_evaluation import BASELINE_ROOT, baseline_snapshot, row_for, summarize, verify_nl
from agenttest.report import fingerprint


def suffix_for(prior, frozen):
    """Only a contiguous attempted prefix may be retained; no replacement slots."""
    schedule, rows = frozen["schedule"], prior["results"]
    if prior["source"] != "real" or len(schedule) != 72 or len(rows) != len(schedule):
        raise ValueError("Expected original complete real scheduling ledger")
    prefix = 0
    while prefix < len(rows) and rows[prefix].get("run_id"):
        prefix += 1
    if not 0 < prefix < len(rows) or any(r.get("run_id") or r.get("attempted_unknown")
                                       for r in rows[prefix:]):
        raise ValueError("Only never-attempted suffix may continue")
    if not rows[prefix - 1].get("stop_paid"):
        raise ValueError("Prior schedule has no recorded stop")
    if any(any(r.get(k) != s[k] for k in s) for r, s in zip(rows, schedule)):
        raise ValueError("Original slot identities changed")
    return rows[:prefix], schedule[prefix:]


def export_candidate(commit):
    """Git object export is immutable source data, never a branch or worktree."""
    destination = PROJECT_ROOT / "runs" / ("nlr-candidate-" + commit[:7])
    raw = subprocess.run(["git", "archive", "--format=zip", commit], cwd=PROJECT_ROOT,
                         capture_output=True, check=True, timeout=30).stdout
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if not destination.exists():
            destination.mkdir()
            archive.extractall(destination)
        for name in archive.namelist():
            if not name.endswith("/") and (destination / name).read_bytes() != archive.read(name):
                raise ValueError("Candidate export differs from original Git object")
    if (destination / ".git").exists():
        raise ValueError("Candidate source export must not be a checkout")
    return destination


def execution_files(candidate, model_env):
    files = baseline_snapshot()
    for directory in (candidate / "src", candidate / "examples/nl-reliability",
                      candidate / "examples/portability"):
        for path in directory.rglob("*"):
            if path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
                files[str(path)] = fingerprint(path.read_bytes().hex())
    for path in (candidate / "scripts/run_nl_worker.py", model_env):
        files[str(path)] = fingerprint(path.read_bytes().hex())
    return files


def usage_audit(rows):
    """Separate known subtotals from unknown usage; unstarted slots have no request."""
    unknown = [i for i, r in enumerate(rows, 1) if r.get("attempted_unknown")
               or r.get("run_id") and not r.get("usage_known")]
    known = sum(r["total_tokens"] for r in rows if r.get("run_id") and r.get("usage_known"))
    return dict(known_token_subtotal=known, unknown_usage_positions=unknown,
        total_tokens=None if unknown else known,
        reported_model_calls_subtotal=sum(r.get("model_calls") or 0 for r in rows))


def execute_worker(candidate, case_file, output, slot, mode, identity_file, model_env):
    root = BASELINE_ROOT if slot["arm"] == "baseline" else candidate
    result = subprocess.run([sys.executable, str(candidate / "scripts/run_nl_worker.py"),
        "--engine-root", str(root), "--case-file", str(case_file), "--output", str(output),
        "--arm", slot["arm"], "--mode", mode, "--identity-file", str(identity_file),
        "--model-env", str(model_env)], cwd=PROJECT_ROOT, capture_output=True, timeout=360)
    if result.returncode:
        raise ValueError("Frozen isolated worker failed")


def run_resumption(output, prior_path, authorization_path, model_env, *, real=False,
                   mock_acceptance=None):
    output, prior_path, model_env = (p.resolve() for p in (output, prior_path, model_env))
    private_output(PROJECT_ROOT, output)
    verify_nl(prior_path)
    prior, frozen = (read_json(prior_path / n) for n in ("results.json", "freeze.json"))
    prefix, suffix = suffix_for(prior, frozen)
    authorization = read_json(authorization_path)
    prior_digest = fingerprint(read_json(prior_path / "archive.json"))
    if (authorization.get("source") != "direct_user_message_in_current_goal"
        or authorization.get("operator_instruction") != "后续可以正常调用"
        or authorization.get("prior_archive_sha256") != prior_digest
        or authorization.get("preserve_first_timeout_and_unknown_usage") is not True
        or authorization.get("no_retry_or_replacement") is not True):
        raise ValueError("Explicit instruction must bind unchanged prior archive")
    controller = checkout_identity(PROJECT_ROOT)
    if real and controller["worktree_status"]:
        raise ValueError("Paid continuation requires clean committed controller")
    old_identity = read_json(prior_path / "execution-identity.json")
    if old_identity.get(str(model_env)) != fingerprint(model_env.read_bytes().hex()):
        raise ValueError("Original model configuration changed")
    # Metrics and worker inputs must still be the original implementation, not a tuned scorer.
    for name in ("nl_scoring.py", "nl_tasks.py", "nl_evaluation.py"):
        path = PROJECT_ROOT / "src/agenttest" / name
        if old_identity.get(str(path)) != fingerprint(path.read_bytes().hex()):
            raise ValueError("Original task/scoring implementation changed")
    if real:
        if mock_acceptance is None:
            raise ValueError("Matching offline continuation acceptance required")
        verify_resumption(mock_acceptance)
        mf, mr = (read_json(mock_acceptance / n) for n in ("freeze.json", "results.json"))
        if (mf["controller"] != controller or mf["prior_archive_sha256"] != prior_digest
            or mf["authorization"] != authorization or mf["suffix_schedule"] != suffix
            or mr["suffix_source"] != "mock_transport"
            or mr["suffix_completed_reports"] != len(suffix)
            or not all(r["grade"]["product_complete"] for r in mr["results"][len(prefix):])):
            raise ValueError("Offline continuation differs or failed")
    candidate = export_candidate(frozen["git"]["commit"])
    files = execution_files(candidate, model_env)

    def guard():
        if controller != checkout_identity(PROJECT_ROOT):
            raise ValueError("Controller source changed")
        if any(fingerprint(Path(p).read_bytes().hex()) != h for p, h in files.items()):
            raise ValueError("Frozen engines or model configuration changed")

    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(files, output / "execution-identity.json")
    by_id = {c["id"]: c for c in frozen["cases"]}
    for case in by_id.values():
        save_evaluation(case, output / (case["id"] + ".json"))
    for key, expected in frozen["prepared"].items():
        case_id, arm = key.split("/")
        directory = output / "preflight" / (case_id + "-" + arm)
        execute_worker(candidate, output / (case_id + ".json"), directory,
            dict(arm=arm), "prepare", output / "execution-identity.json", model_env)
        if read_json(directory / "prepared.json") != expected:
            raise ValueError("Original model payload or trusted inputs differ")
    save_evaluation(dict(schema_version="agenttest.nl-resumption.freeze.v1",
        controller=controller, prior_path=str(prior_path), prior_archive_sha256=prior_digest,
        authorization=authorization, evaluated_candidate_commit=frozen["git"]["commit"],
        candidate_export=str(candidate), prefix_count=len(prefix), suffix_schedule=suffix,
        prepared=frozen["prepared"], cases=frozen["cases"],
        grading_version=frozen["grading_version"],
        max_new_calls=len(suffix) * 4,
        max_new_reserved_tokens=len(suffix) * 4 * (
            frozen["model"]["max_input_tokens"] + frozen["model"]["max_output_tokens"]),
        per_run=frozen["per_run"], classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED",
        prior_usage_reconciled=False, first_failed_position_retried=False), output / "freeze.json")
    rows, stop = list(prefix), None
    began = time.perf_counter()
    for position, slot in enumerate(suffix, len(prefix) + 1):
        if stop:
            row = dict(**slot, run_id=None, not_attempted_reason=stop)
        else:
            guard()
            started = time.perf_counter()
            try:
                folder = output / f"position-{position:03}"
                execute_worker(candidate, output / (slot["case_id"] + ".json"), folder,
                    slot, "real" if real else "mock", output / "execution-identity.json", model_env)
                guard()
                if read_json(folder / "prepared.json") != frozen["prepared"][
                        slot["case_id"] + "/" + slot["arm"]]:
                    raise ValueError("Frozen payload changed")
                row = row_for(by_id[slot["case_id"]], slot, folder,
                              round((time.perf_counter() - started) * 1000, 3))
                if row["stop_paid"]:
                    stop = "NEW_UNKNOWN_USAGE_OR_EXTERNAL_FAILURE_STOP"
            except (ValueError, OSError, subprocess.SubprocessError):
                row = dict(**slot, run_id=None, attempted_unknown=True,
                    not_attempted_reason="NEW_WORKER_FAILURE_USAGE_UNKNOWN")
                stop = "NEW_WORKER_FAILURE_USAGE_UNKNOWN"
        rows.append(row)
        save_evaluation(row, output / f"row-{position:03}.json")
        print(json.dumps(dict(position=position, **slot, outcome=row.get("outcome"),
                              tokens=row.get("total_tokens"))), flush=True)
    result = dict(**summarize(rows, frozen["schedule"],
        "real" if real else "mock_resumption_rehearsal_with_real_prefix"),
        suffix_source="real" if real else "mock_transport", results=rows,
        suffix_completed_reports=sum(bool(r.get("run_id")) for r in rows[len(prefix):]),
        prior_unknown_usage_preserved=True,
        usage_audit=usage_audit(rows),
        suffix_elapsed_ms=round((time.perf_counter() - began) * 1000, 3))
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(json.dumps(
        {k: v for k, v in result.items() if k != "results"}, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n")
    seal(output)
    verify_resumption(output)
    return {k: v for k, v in result.items() if k != "results"}


def verify_resumption(output):
    """Regrade combined ledger from both immutable archives; no models or business calls."""
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("Continuation seal changed")
    frozen, result = (read_json(output / n) for n in ("freeze.json", "results.json"))
    prior_path = Path(frozen["prior_path"])
    verify_nl(prior_path)
    if fingerprint(read_json(prior_path / "archive.json")) != frozen["prior_archive_sha256"]:
        raise ValueError("Original archive changed")
    prior, original = (read_json(prior_path / n) for n in ("results.json", "freeze.json"))
    prefix, suffix = suffix_for(prior, original)
    rows = result["results"]
    if (rows[:len(prefix)] != prefix or len(rows) != len(original["schedule"])
            or suffix != frozen["suffix_schedule"]):
        raise ValueError("Original prefix or remaining schedule changed")
    ids = [r["run_id"] for r in rows if r.get("run_id")]
    if len(ids) != len(set(ids)):
        raise ValueError("Synthetic target/session reports reused")
    by_id = {c["id"]: c for c in original["cases"]}
    for position, (slot, row) in enumerate(zip(suffix, rows[len(prefix):]), len(prefix) + 1):
        if row != read_json(output / f"row-{position:03}.json") or any(
                row.get(k) != v for k, v in slot.items()):
            raise ValueError("Continuation row changed")
        if row.get("run_id"):
            folder = output / f"position-{position:03}"
            prepared, worker = (read_json(folder / n) for n in ("prepared.json", "worker.json"))
            engine = BASELINE_ROOT if slot["arm"] == "baseline" else Path(
                frozen["candidate_export"])
            expected_engine = engine / "src/agenttest/llm_planner.py"
            if (prepared != original["prepared"][slot["case_id"] + "/" + slot["arm"]]
                or worker["engine_file"] != str(expected_engine)
                or worker["engine_sha256"] != fingerprint(expected_engine.read_bytes().hex())):
                raise ValueError("Original payload or actual loaded engine changed")
            actual = row_for(by_id[slot["case_id"]], slot,
                folder, row["position_elapsed_ms"])
            if row != actual or row["model_calls"] > 4 or row["investigation_requests"] > 2:
                raise ValueError("Continuation cold score or budget changed")
    expected = summarize(rows, original["schedule"], result["source"])
    if any(result[k] != v for k, v in expected.items()):
        raise ValueError("Combined summary differs from frozen protocol")
    if result["usage_audit"] != usage_audit(rows):
        raise ValueError("Unknown usage or known subtotal changed")
    return dict(archive_verified=True, original_prefix_reports=len(prefix),
                suffix_reports=sum(bool(r.get("run_id")) for r in rows[len(prefix):]),
                scheduled=len(rows), new_model_calls=0)
