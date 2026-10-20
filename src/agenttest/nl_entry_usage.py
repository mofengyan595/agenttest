"""One untouched demand through the goal CLI; export only after real trusted success."""

import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agenttest.__main__ import main as cli
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.nl_entry_evaluation import _settings, _stats, prepare_profiles, verify_requests
from agenttest.regression import load_regression
from agenttest.report import Outcome, ReportStore, fingerprint

FIXTURE = '''"""Operator ASGI fixture; TaskContract is derived from original text."""
import json
from pathlib import Path

import httpx
import pytest

from agenttest.evidence_context import EvidenceFeedbackModel
from agenttest.goal_cli import synthetic_runtime
from agenttest.goal_derivation import derive_goal
from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionRuntime
from agenttest.report import ReportStore


@pytest.fixture
def agenttest_regression_runtime(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NO_MODEL_OR_BUSINESS_NETWORK_IN_REGRESSION")

    monkeypatch.setattr(OpenAICompatiblePlanner, "generate", forbidden)
    monkeypatch.setattr(EvidenceFeedbackModel, "generate", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    root = Path(__file__).parent
    request = json.loads((root / "request.json").read_bytes())
    project = load_goal_project(root / "projects" / request["project"] / "registered.project.json")
    goal = derive_goal(project, request["user_task"])
    env, transport = synthetic_runtime(project)
    return RegressionRuntime(project.document, project.target, project.registry,
        goal.task, env, transport, ReportStore(tmp_path / "reports"))
'''


def replay_export(output, temporary):
    """Model-disabled child process exercises the original fixed pytest export template."""
    (output / "conftest.py").write_text(FIXTURE, encoding="utf-8", newline="\n")
    child_env = {k: v for k, v in os.environ.items() if k not in {
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "AGENTTEST_MODEL"}}
    replay = subprocess.run([sys.executable, "-m", "pytest", str(output / "bundle"),
        "-q", "--basetemp", str(temporary), "--junitxml", str(output / "replay.xml")],
        env=child_env, capture_output=True, timeout=90)
    (output / "replay.stdout.txt").write_bytes(replay.stdout)
    (output / "replay.stderr.txt").write_bytes(replay.stderr)
    reports = []
    for path in temporary.glob("*/reports/*/report.json"):
        report = ReportStore(path.parent.parent).read(path.parent.name)
        reports.append(report)
        snapshot = output / "replay-reports" / report.run_id
        snapshot.mkdir(parents=True, exist_ok=False)
        save_evaluation(report.model_dump(mode="json"), snapshot / "report.json")
    verified = bool(replay.returncode == 0 and len(reports) == 1
        and reports[0].outcome == Outcome.PASS and reports[0].task_mode == "operator_plan"
        and reports[0].task_satisfied is None and reports[0].model_call is None)
    return dict(exit=replay.returncode, verified=verified,
                run_id=reports[0].run_id if len(reports) == 1 else None)


def run_novel(output, evaluation_archive, model_env, request_path):
    """No retry, replacement plan or fallback. Failure is a complete recorded first attempt."""
    verified = verify_requests(evaluation_archive)
    previous = read_json(evaluation_archive / "freeze.json")
    prior_result = read_json(evaluation_archive / "results.json")
    identity = checkout_identity(PROJECT_ROOT)
    settings = _settings(model_env)
    if (identity["worktree_status"] or previous["source"] != "real"
            or previous["code"] != identity or verified["attempted"] != 36
            or prior_result["unknown_usage_calls"] or prior_result["total_tokens"] is None
            or any(r["stop_paid"] for r in prior_result["results"])
            or previous["model"] != public_model_config(settings)):
        raise ValueError("Frozen completed real schedule with known usage required")
    request = read_json(request_path)
    if request["user_task"] in {q["user_task"] for q in previous["requests"]}:
        raise ValueError("Novel demand overlaps evaluation")
    output = output.absolute()
    temporary = output.parent / (output.name + "-pytest-temp")
    private_output(PROJECT_ROOT, output)
    private_output(PROJECT_ROOT, temporary)
    if os.path.lexists(output) or os.path.lexists(temporary):
        raise ValueError("First attempt destination already exists")
    output.mkdir()
    prepare_profiles(output / "projects")
    if inventory(output / "projects") != previous["profile_inventory"]:
        raise ValueError("Novel demand project differs from frozen reusable configuration")
    save_evaluation(request, output / "request.json")
    frozen = dict(schema_version="agenttest.nl-entry-novel.v1", code=identity, request=request,
        model=public_model_config(settings), project_inventory=inventory(output / "projects"),
        evaluation_archive_sha256=fingerprint(read_json(evaluation_archive / "archive.json")),
        fixture_sha256=fingerprint(FIXTURE), classification="DEVELOPMENT_ONLY",
        review_status="UNREVIEWED", independent_human_reviewed=0,
        max_calls=4, max_readonly=2, max_total_tokens=100_000, model_timeout=60, retries=0)
    save_evaluation(frozen, output / "freeze.json")
    began, captured = time.perf_counter(), io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = cli(["goal", "--project", str(output / "projects" / request["project"] /
            "registered.project.json"), "--user-task", request["user_task"],
            "--real", "--model-env", str(model_env), "--model-timeout", "60",
            "--synthetic-target", "--runs-dir", str(output / "source-reports"),
            "--export-regression", str(output / "bundle")])
    result = json.loads(captured.getvalue())
    save_evaluation(result, output / "cli.json")
    report = ReportStore(output / "source-reports").read(result["run_id"])
    stats = [s for s in _stats(report) if s.calls]
    known = all(s.total_tokens is not None for s in stats)
    calls = sum(s.calls for s in stats)
    unchanged = identity == checkout_identity(PROJECT_ROOT) and inventory(
        output / "projects") == frozen["project_inventory"]
    passed = bool(code == 0 and report.outcome == Outcome.PASS and report.task_satisfied
        and result["regression_directory"] and known and unchanged)
    replay = replay_export(output, temporary) if passed else None
    bundle_hashes = ({p.name: fingerprint(p.read_bytes().hex()) for p in
                     (output / "bundle").iterdir() if p.is_file()} if passed else {})
    summary = dict(schema_version="agenttest.nl-entry-novel-result.v1",
        classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED",
        source_run_id=report.run_id, business_outcome=report.outcome.value,
        task_satisfied=report.task_satisfied, required_steps=result["required_steps"],
        cli_exit=code, exported=passed, replay=replay, source_unchanged=unchanged,
        product_complete=passed and replay["verified"], bundle_hashes=bundle_hashes,
        usage_known=known, model_calls=calls, real_model_calls=calls,
        total_tokens=sum(s.total_tokens for s in stats) if known else None,
        readonly_requests=sum(len(i.attempts) for i in report.investigations),
        elapsed_ms=(time.perf_counter() - began) * 1000, human_time_saved=None,
        independent_human_reviewed=0, real_business_http=0, database_writes=0)
    if calls > 4 or summary["readonly_requests"] > 2 or sum(
            s.total_tokens or 0 for s in stats) > 100_000:
        raise ValueError("Novel demand budget exceeded")
    save_evaluation(summary, output / "results.json")
    (output / "summary.md").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
    seal(output)
    verify_novel(output)
    return summary


def verify_novel(output):
    output = Path(output)
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("Novel archive changed")
    frozen, result = [read_json(output / name) for name in ("freeze.json", "results.json")]
    report = ReportStore(output / "source-reports").read(result["source_run_id"])
    stats = [s for s in _stats(report) if s.calls]
    known = all(s.total_tokens is not None for s in stats)
    if (result["model_calls"] != sum(s.calls for s in stats) or result["usage_known"] != known
            or result["total_tokens"] != (sum(s.total_tokens for s in stats) if known else None)
            or frozen["project_inventory"] != inventory(output / "projects")):
        raise ValueError("Novel usage or configuration changed")
    if result["exported"]:
        load_regression(output / "bundle")
        actual = {p.name: fingerprint(p.read_bytes().hex()) for p in
                  (output / "bundle").iterdir() if p.is_file()}
        if actual != result["bundle_hashes"] or fingerprint(
                (output / "conftest.py").read_text(encoding="utf-8")) != frozen["fixture_sha256"]:
            raise ValueError("Novel regression bundle or fixture changed")
    if result["replay"] and result["replay"]["verified"]:
        replay = ReportStore(output / "replay-reports").read(result["replay"]["run_id"])
        if (replay.outcome != Outcome.PASS or replay.task_mode != "operator_plan"
                or replay.task_satisfied is not None or replay.model_call is not None
                or len(replay.steps) != len(report.steps)):
            raise ValueError("Novel model-disabled regression evidence changed")
    return dict(archive_verified=True, **result, new_model_calls=0)
