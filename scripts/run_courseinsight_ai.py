"""Operator-only, one-shot Chinese -> school planner -> isolated Java acceptance."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from run_courseinsight_http import JAVA_ARGS, launch, stop, wait_health

from agenttest.courseinsight_env import PreflightError, preflight
from agenttest.courseinsight_nl import DEFAULT_TASK, approved_inputs, run
from agenttest.demo import PROJECT_ROOT
from agenttest.evaluation_freeze import _git, public_model_config
from agenttest.llm_planner import COURSEINSIGHT_PROMPT, ModelSettings, OpenAICompatiblePlanner
from agenttest.report import Outcome, ReportStore, fingerprint


def prepare(destination: Path, task: str) -> dict:
    scenario, _, _, _ = approved_inputs(task)
    manifest = {"version": 1, "invocation_id": uuid4().hex, "user_task": task,
                "course_code": scenario.steps[1].body["code"].value}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return manifest


def load_fixture(path: Path) -> dict:
    from uuid import UUID

    with path.open("rb") as stream:
        raw = stream.read(10_001)
    if len(raw) > 10_000:
        raise ValueError("FIXTURE_SIZE")
    data = json.loads(raw)
    if (set(data) != {"version", "invocation_id", "user_task", "course_code"}
            or data["version"] != 1 or UUID(data["invocation_id"]).hex != data["invocation_id"]):
        raise ValueError("FIXTURE_FORMAT")
    approved_inputs(data["user_task"], course_code=data["course_code"])
    return data


async def live(root: Path, fixture: Path, env_file: Path, store: ReportStore) -> int:
    if _git(PROJECT_ROOT, "status", "--porcelain", "--untracked-files=normal"):
        raise PreflightError("CLEAN_COMMITTED_CODE_REQUIRED")
    code_commit = _git(PROJECT_ROOT, "rev-parse", "HEAD")
    data = load_fixture(fixture)
    bundle = approved_inputs(data["user_task"], course_code=data["course_code"])
    evidence, settings = preflight(root)
    model = ModelSettings.from_env_file(env_file).model_copy(update={
        "timeout": 60.0, "max_output_tokens": 2048, "max_total_tokens": 22048})
    if model.protocol != "chatecnu":
        raise PreflightError("SCHOOL_ENDPOINT_REQUIRED")
    # Persist BEFORE either model or business call. Existing/incomplete claims never resume.
    directory = store.root / ("courseinsight-ai-" + data["invocation_id"])
    directory.mkdir(parents=True, exist_ok=False)
    audit = {"isolation": evidence, "fixture_sha256": fingerprint(data),
             "code_commit": code_commit, "model_config": public_model_config(model),
             "prompt_sha256": fingerprint(COURSEINSIGHT_PROMPT),
             "launch_arguments_sha256": fingerprint(JAVA_ARGS),
             "model": model.model, "max_model_calls": 1, "reserved_tokens": 22048,
             "state": "CLAIMED_NO_RESUME", "java_writes_budget": 1}
    path = directory / "audit.json"
    path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (directory / "fixture.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    process = None
    try:
        process = launch(root, settings)
        audit["health_attempts"] = await wait_health(process)

        def guard():
            current, private = preflight(root)
            private.clear()
            if current != evidence:
                raise PreflightError("ISOLATION_CHANGED")
            audit["state"] = "EXECUTING_ONCE"
            path.write_text(json.dumps(audit, indent=2), encoding="utf-8")

        report = await run(data["user_task"], OpenAICompatiblePlanner(model,
                           profile="courseinsight"), store, bundle=bundle, environ={
            "COURSEINSIGHT_DEMO_USERNAME": settings["BOOTSTRAP_ADMIN_USERNAME"],
            "COURSEINSIGHT_DEMO_PASSWORD": settings["BOOTSTRAP_ADMIN_PASSWORD"]},
            authorized=True, execution_guard=guard)
        summary = {"run_id": report.run_id, "outcome": report.outcome.value,
                   "reason": report.reason, "plan_valid": report.plan_valid,
                   "intent_verified": report.intent_verified,
                   "http_statuses": [a.status_code for s in report.steps for a in s.attempts],
                   "http_elapsed_ms": [a.elapsed_ms for s in report.steps for a in s.attempts],
                   "checks": sum(len(s.checks) for s in report.steps),
                   "create_requests": sum(len(s.attempts) for s in report.steps
                                          if s.operation_id == "ciCreateCourse"),
                   "model_call": report.model_call.model_dump() if report.model_call else None,
                   "diagnostic": report.diagnostic.model_dump() if report.diagnostic else None}
        audit.update({"state": "FINISHED", "result": summary})
        print(json.dumps(summary))
        return 0 if report.outcome == Outcome.PASS else 1
    finally:
        settings.clear()
        try:
            if process is not None:
                stop(process)
        finally:
            path.write_text(json.dumps(audit, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", type=Path, help="Generate a local reviewable fixture; no HTTP")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--model-env", type=Path, default=Path(".env"))
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--execute", action="store_true",
                        help="Operator authorization for ONE NEW demo database row")
    args = parser.parse_args()
    try:
        if args.prepare:
            if args.execute or args.real:
                parser.error("prepare cannot execute")
            print(json.dumps(prepare(args.prepare, args.task), ensure_ascii=False))
            return 0
        if not args.source:
            parser.error("--source required")
        root = args.source.resolve(strict=True)
        if args.execute:
            if not args.real or not args.fixture:
                parser.error("execution requires --real and --fixture")
            return asyncio.run(live(root, args.fixture, args.model_env, ReportStore(args.runs)))
        evidence, settings = preflight(root)
        settings.clear()
        print(json.dumps(evidence))
        return 0
    except (PreflightError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"outcome": "BLOCKED", "code": "AI_ACCEPTANCE_PREFLIGHT_OR_IO_ERROR"}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"outcome": "BLOCKED", "code": "INTERRUPTED_NO_AUTO_RESUME"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
