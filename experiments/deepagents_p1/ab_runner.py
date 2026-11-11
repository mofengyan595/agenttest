"""A/B runner: A = LangGraph Round Supervisor, B = Deep Agents minimal loop.

Both arms get the same isolated lab target, the same configured model, the same four natural
language goals and the same deterministic machinery (Focus -> Planning IR -> preflight ->
Executor -> Report). Only the *decision layer* differs:

* A: the product's cyclic LangGraph supervisor (``RoundSupervisor``) with a closed action enum;
* B: a Deep Agents agent whose only tools are the eight logical product tools.

This script never prints or stores credentials. It writes one JSON record per arm with the
measured facts (requests, outcomes, tool calls, token usage, stop reason), so the comparison in
the report can be checked against real evidence rather than intent.

Usage (PowerShell, from the repo root):

    $env:PYTHONUTF8='1'; $env:PYTHONPATH='src'
    .\.venv-deepagents\Scripts\python.exe experiments/deepagents_p1/ab_runner.py \
        --arm B --goal "帮我测一下包裹管理功能。" --out .eduwork/runs/p1/b-goal1.json

Arm A must run in the production ``.venv`` (it needs langgraph 1.2.14, not the experiment
environment); arm B runs in ``.venv-deepagents``.
"""

import argparse
import asyncio
import importlib.util
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
TARGET_SCRIPTS = ROOT / "scripts" / "serve_colleague_targets.py"
TERMINAL = {"FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "CANCELLED"}


def load_targets():
    """The repository's own isolated lab target, loaded by path (not by import side effect)."""
    spec = importlib.util.spec_from_file_location("colleague_targets", TARGET_SCRIPTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serve(port: int) -> threading.Thread:
    """Run the lab target on loopback in a background thread; never a real service."""
    import uvicorn

    targets = load_targets()
    app = targets.create_target("parcels")

    class _Server(uvicorn.Server):
        def install_signal_handlers(self):  # a thread has no signal handlers to install
            return

    config = uvicorn.Config(app, host="127.0.0.1", port=port, proxy_headers=False,
                            access_log=False, log_level="warning")
    server = _Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return thread
        time.sleep(0.05)
    raise SystemExit("lab target did not start")


def settings():
    from agenttest.llm_planner import ModelSettings

    return ModelSettings.from_env_file(ROOT / ".env")


def model_settings(project):
    """The configured model, bounded by the *project's* registered budget.

    Measured: the design envelope is ~20.7 kB even for a one-operation scope (instructions plus
    the plan schema dominate), so the ``.env`` default ``max_input_tokens=20000`` rejects every
    design call with ``MODEL_TOKEN_BUDGET``. The product therefore plans with the project
    budget; an experiment that ignored it would report a harness failure that is really a
    misconfiguration.
    """
    budget = project.registration.budget
    return settings().model_copy(update=dict(
        max_input_tokens=budget.max_input_tokens or 60_000,
        max_output_tokens=budget.max_output_tokens or 8_192,
        max_total_tokens=budget.max_total_tokens or 70_000))


def project(base_url: str):
    """Build the same generic project the product builds from the operator's choices."""
    import json

    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project

    targets = load_targets()
    document = json.dumps(targets.document("parcels"), ensure_ascii=False)
    contract = load_openapi(document)
    suggestions = analyze_contract(contract)
    operations = [dict(operation_id=item["operation_id"], alias=item["alias"],
                       statuses=item["statuses"],
                       arguments=[{key: value for key, value in argument.items()
                                   if key in {"location", "name", "value", "source_operation",
                                              "selector", "generated", "needs_input"}}
                                  for argument in item["arguments"]])
                  for item in suggestions]
    setup = GenericSetup.model_validate(dict(
        base_url=base_url, isolated_confirmed=True, allow_mutations=True, assisted=True,
        approve_negative_tests=False, operations=operations))
    return build_generic_project("parcels", document, contract, setup), setup, document


def service(root: Path):
    """The product task service, wired with the real design and supervisor models."""
    from agenttest.product_design import TestDesignModel
    from agenttest.product_supervisor import SupervisorModel
    from agenttest.report import ReportStore
    from agenttest.task_service import TaskService
    from agenttest.task_store import TaskStore

    root.mkdir(parents=True, exist_ok=True)
    return TaskService({}, TaskStore(root / "tasks.sqlite3"), ReportStore(root / "reports"),
        lambda _: None,
        designer_factory=lambda project: TestDesignModel(model_settings(project),
                                                         profile="generic"),
        generic_intake=True, environ={},
        model_status=lambda: "CONFIGURED_MODEL",
        supervisor_factory=lambda project: SupervisorModel(model_settings(project),
                                                           profile="generic"))


def with_rounds(svc, rounds: int):
    """Opt the project into supervision exactly as a registration budget would."""
    original = svc.project

    def patched(identifier):
        loaded = original(identifier)
        budget = loaded.registration.budget.model_copy(
            update=dict(supervisor_max_rounds=rounds))
        return replace(loaded, registration=loaded.registration.model_copy(
            update=dict(budget=budget)))

    svc.project = patched


async def wait_for(svc, row, timeout: float = 900.0):
    from agenttest.task_store import TERMINAL as TERMINAL_STATUSES

    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        current = svc.db.get(row["id"], "operator")
        if current["status"] in TERMINAL_STATUSES:
            return current
        await asyncio.sleep(0.25)
    raise SystemExit("task did not terminate within the timeout")


async def arm_a(args, base_url: str) -> dict:
    from agenttest.project_intake import IntakeInput

    proj, setup, _ = project(base_url)
    root = Path(args.out).parent / "arm-a-state"
    svc = service(root)
    if args.rounds:
        with_rounds(svc, args.rounds)
    started = time.perf_counter()
    try:
        saved = svc.intake.save(IntakeInput(name=args.goal[:24], document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], args.goal, uuid4().hex)
        done = await wait_for(svc, row)
        summary = done.get("summary") or {}
        return dict(arm="A", goal=args.goal, status=done["status"],
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                    outcome=summary.get("outcome"), complete=summary.get("complete"),
                    checks_passed=summary.get("checks_passed"),
                    supervision=summary.get("supervision"),
                    cases=[{key: case.get(key) for key in
                            ("case_id", "status", "outcome", "http_requests",
                             "executed_operations", "scenario_fingerprint", "duplicate_of",
                             "precondition_changes", "absence_expectations", "reason")}
                           for case in summary.get("cases") or ()],
                    design_records=[{key: record.get(key) for key in
                                     ("group_id", "planning_engine", "planning_engine_reason",
                                      "supervision_round", "supervision_target",
                                      "supervision_planning_ir", "model_call")}
                                    for record in summary.get("design_records") or ()],
                    coverage=summary.get("coverage"),
                    summary_keys=sorted(summary),
                    task=done)
    finally:
        await svc.close()


def accounting_model(settings):
    """The configured adapter, with per-call stats summed instead of overwritten.

    ``EcnuxChatModel`` resets ``model_stats`` on every call on purpose: a report cites one
    call. An A/B comparison needs the total, so the totals are accumulated beside it without
    touching the adapter or its accounting.
    """
    from agenttest.deep_harness.chat_model import EcnuxChatModel

    totals = dict(calls=0, input_tokens=0, output_tokens=0, total_tokens=0,
                  request_bytes=0, response_bytes=0, elapsed_ms=0.0)

    class _Accounting(EcnuxChatModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            result = await super()._agenerate(messages, stop, run_manager, **kwargs)
            stats = self.model_stats
            for key in ("input_tokens", "output_tokens", "total_tokens", "request_bytes",
                        "response_bytes"):
                totals[key] += getattr(stats, key, None) or 0
            totals["calls"] += getattr(stats, "calls", 0) or 0
            totals["elapsed_ms"] += getattr(stats, "elapsed_ms", 0.0) or 0.0
            return result

    return _Accounting(settings=settings), totals


async def arm_b(args, base_url: str) -> dict:
    from agenttest.deep_harness.loop import run_goal
    from agenttest.deep_harness.product_tools import ProductSession
    from agenttest.product_design import TestDesignModel
    from agenttest.report import ReportStore

    proj, _, _ = project(base_url)
    root = Path(args.out).parent / "arm-b-state"
    root.mkdir(parents=True, exist_ok=True)
    configured = model_settings(proj)
    designer = TestDesignModel(configured, profile="generic")
    session = ProductSession(project=proj, store=ReportStore(root / "reports"),
                             text=args.goal, environ={}, designer=designer,
                             max_executed_plans=args.max_plans,
                             max_requests=args.max_requests)
    model, totals = accounting_model(configured)
    started = time.perf_counter()
    result = await run_goal(session, model, recursion_limit=args.recursion_limit)
    result.update(arm="B", elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                  model_calls=totals["calls"], usage=totals,
                  summary_cases=session.summary.get("cases") or [])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=["A", "B"], required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18991)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--max-plans", type=int, default=4)
    parser.add_argument("--max-requests", type=int, default=24)
    parser.add_argument("--recursion-limit", type=int, default=40)
    parser.add_argument("--no-serve", action="store_true", help="use an already running target")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    base_url = f"http://127.0.0.1:{args.port}"
    if not args.no_serve:
        serve(args.port)
    payload = asyncio.run(arm_a(args, base_url) if args.arm == "A"
                          else arm_b(args, base_url))
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(json.dumps({key: payload.get(key) for key in
                      ("arm", "goal", "status", "outcome", "complete", "stopped",
                       "requests", "elapsed_ms")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
