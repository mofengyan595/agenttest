"""Reproducible CLI -> regression path for the two approved disposable ASGI projects."""

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx

from agenttest.__main__ import main as cli
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.eval_v2.archive import seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.feedback_check import stats_for
from agenttest.llm_planner import ModelSettings
from agenttest.nl_tasks import task_inputs_for
from agenttest.portability_demo import project_inputs
from agenttest.project import check_project
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.workflow import Workflow as OriginalWorkflow

FIXTURE = '''"""Operator-owned ASGI runtime. Inputs are reviewed files, never model code."""
import json
from pathlib import Path

import httpx
import pytest

from agenttest.llm_planner import OpenAICompatiblePlanner
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import project_inputs
from agenttest.regression import RegressionRuntime
from agenttest.report import ReportStore
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract


@pytest.fixture
def agenttest_regression_runtime(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NO_MODEL_OR_BUSINESS_NETWORK_IN_REGRESSION")

    monkeypatch.setattr(OpenAICompatiblePlanner, "generate", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    root = Path(__file__).parent / "project"
    case = json.loads((root / "case.json").read_bytes())
    app, _, _, _, env = project_inputs(case["project"])
    return RegressionRuntime((root / "openapi.json").read_bytes(),
        TargetConfig.model_validate_json((root / "target.json").read_bytes()),
        PolicyRegistry.from_json((root / "policies.json").read_bytes()),
        TaskContract.model_validate_json((root / "task.json").read_bytes()), env,
        httpx.ASGITransport(app), ReportStore(tmp_path / "reports"))
'''


def prepare(case, output: Path):
    """Expose all operator-owned inputs; no reference plan is written or sent."""
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    if case["fault"] != "none" or case["category"] != "healthy":
        raise ValueError("Usage path is one new healthy demand; examples use sealed archives")
    _, doc, target, registry, env, task, _, capabilities = task_inputs_for(case)
    project = output / "project"
    project.mkdir()
    (project / "openapi.json").write_bytes(doc)
    for name, value in {
        "target.json": target.model_dump(mode="json"),
        "policies.json": [p.model_dump(mode="json") for p in registry.definitions()],
        "task.json": task.model_dump(mode="json"),
        "case.json": case,
        "capabilities.json": [c.model_dump(mode="json") for c in capabilities.entries()],
        "project.json": dict(schema_version="agenttest.project.v1", contract="openapi.json",
            target="target.json", policies="policies.json", task_contract="task.json"),
    }.items():
        save_evaluation(value, project / name)
    check = check_project(project / "project.json", env)
    save_evaluation(dict(**check.summary(),
        reviewed_files={p.name: fingerprint(p.read_bytes().hex()) for p in project.iterdir()},
        review_status="UNREVIEWED",
        review_actor="agent_static_configuration_check", independent_human_reviewed=False,
        manually_authored=dict(task_steps=len(task.requirements.steps),
            before_edges=len(task.requirements.before),
            public_policy_purposes=len(registry.definitions()),
            note="Manual case mapping and reused policy/assertion/target templates are required")),
        output / "configuration-review.json")
    if not check.ready:
        raise ValueError("Operator project inputs are not ready")
    return check.summary()


def execute(output: Path, *, model_env: Path | None = None, fake_plan: Path | None = None):
    """Single attempt via existing CLI. Never repair, replace, or replay a failed plan."""
    if bool(model_env) == bool(fake_plan):
        raise ValueError("Select exactly one model source")
    identity = checkout_identity(PROJECT_ROOT)
    if model_env and identity["worktree_status"]:
        raise ValueError("Real usage requires a clean committed checkout")
    if (output / "freeze.json").exists():
        raise ValueError("Usage already attempted; no replacement run")
    temporary = output.parent / (output.name + "-pytest-temp")
    private_output(PROJECT_ROOT, temporary)
    if os.path.lexists(temporary):
        raise ValueError("Regression temporary directory already exists")
    project = output / "project"
    case = json.loads((project / "case.json").read_bytes())
    app, _, _, _, env = project_inputs(case["project"])
    check = check_project(project / "project.json", env)
    if not check.ready:
        raise ValueError("Reviewed project inputs no longer valid")
    inputs = {p.name: fingerprint(p.read_bytes().hex()) for p in project.iterdir()}
    review = json.loads((output / "configuration-review.json").read_bytes())
    if inputs != review["reviewed_files"] or review["inputs"] != check.summary()["inputs"]:
        raise ValueError("Configuration differs from the recorded static review")
    settings = ModelSettings.from_env_file(model_env).model_copy(update={
        "timeout": 60.0, "max_output_tokens": 4096, "max_total_tokens": 25_000
    }) if model_env else None
    save_evaluation(dict(schema_version="agenttest.nl-usage.freeze.v1", git=identity,
        inputs=inputs, configuration=check.summary(),
        model=public_model_config(settings) if settings else None,
        replay_fixture_sha256=fingerprint(FIXTURE),
        classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED",
        max_model_calls=4, max_readonly_requests=2, implicit_retries=0,
        max_reserved_tokens=4 * (settings.max_input_tokens + settings.max_output_tokens)
            if settings else 0,
        planning_source="real_natural_language" if model_env else "fake_offline_mechanism",
        new_user_demand=case["user_task"],
        transport="operator ASGI injection into existing CLI Workflow; no business sockets"),
        output / "freeze.json")
    arguments = ["agent", "--project", str(project / "project.json"),
        "--export-regression", str(output / "bundle"),
        "--feedback-capabilities", str(project / "capabilities.json"),
        "--runs-dir", str(output / "source-reports")]
    arguments += ["--real", "--model-env", str(model_env), "--max-output-tokens", "4096",
                  "--max-total-tokens", "25000", "--model-timeout", "60"] if model_env else [
                      "--fake-plan", str(fake_plan)]

    def asgi(*args, **kwargs):
        kwargs.update(transport=httpx.ASGITransport(app), environ=env)
        return OriginalWorkflow(*args, **kwargs)

    captured = io.StringIO()
    began = time.perf_counter()
    with patch.dict(os.environ, env), patch("agenttest.workflow.Workflow", asgi):
        with contextlib.redirect_stdout(captured):
            code = cli(arguments)
    result = json.loads(captured.getvalue())
    save_evaluation(result, output / "cli.json")
    report = ReportStore(output / "source-reports").read(result["run_id"])
    unchanged = (identity == checkout_identity(PROJECT_ROOT) and inputs == {
        p.name: fingerprint(p.read_bytes().hex()) for p in project.iterdir()})
    stats = [s for s in stats_for(report) if s.calls]
    known = all(s.total_tokens is not None for s in stats)
    calls = sum(s.calls for s in stats)
    if calls > 4 or len(report.investigations) > 2:
        raise ValueError("Usage safety budget exceeded")
    passed = bool(code == 0 and report.outcome == Outcome.PASS and report.task_satisfied
                  and result.get("regression", {}).get("exported") and known and unchanged)
    replay_exit = None
    replay_verified, replay_run_id = False, None
    if passed:
        (output / "conftest.py").write_text(FIXTURE, encoding="utf-8", newline="\n")
        child_env = {k: v for k, v in os.environ.items() if k not in {
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "AGENTTEST_MODEL"}}
        replay = subprocess.run([sys.executable, "-m", "pytest", str(output / "bundle"),
            "-q", "--basetemp", str(temporary),
            "--junitxml", str(output / "replay.xml")],
            env=child_env, capture_output=True, timeout=90)
        (output / "replay.stdout.txt").write_bytes(replay.stdout)
        (output / "replay.stderr.txt").write_bytes(replay.stderr)
        replay_exit = replay.returncode
        replay_reports = []
        for current, directories, names in os.walk(temporary, followlinks=False):
            directories[:] = [name for name in directories
                if not (stat.S_ISLNK((Path(current) / name).lstat().st_mode)
                    or getattr((Path(current) / name).lstat(), "st_file_attributes", 0) & 0x400)]
            if "report.json" in names:
                report_path = Path(current) / "report.json"
                replay_report = ReportStore(report_path.parent.parent).read(report_path.parent.name)
                replay_reports.append(replay_report)
                snapshot = output / "replay-reports" / replay_report.run_id
                snapshot.mkdir(parents=True, exist_ok=False)
                (snapshot / "report.json").write_text(replay_report.model_dump_json(indent=2),
                                                     encoding="utf-8", newline="\n")
        if replay.returncode == 0 and len(replay_reports) == 1:
            replay_report = replay_reports[0]
            replay_verified = bool(replay_report.outcome == Outcome.PASS
                and replay_report.task_mode == "operator_plan"
                and replay_report.task_satisfied is None and replay_report.model_call is None
                and len(replay_report.steps) == len(report.steps))
            replay_run_id = replay_report.run_id
    summary = dict(schema_version="agenttest.nl-usage.result.v1",
        classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED",
        source_run_id=report.run_id, business_outcome=report.outcome.value,
        task_satisfied=report.task_satisfied, cli_exit=code, exported=passed,
        replay_exit=replay_exit, replay_verified=replay_verified, replay_run_id=replay_run_id,
        product_complete=passed and replay_verified,
        usage_known=known, model_calls=calls,
        total_tokens=sum(s.total_tokens for s in stats) if known else None,
        real_model_calls=calls if model_env else 0, readonly_requests=len(report.investigations),
        elapsed_ms=round((time.perf_counter() - began) * 1000, 3),
        human_time_saved=None, independent_human_reviewed=False,
        real_business_http=0, database_writes=0)
    save_evaluation(summary, output / "results.json")
    (output / "summary.md").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
    seal(output)
    return summary
