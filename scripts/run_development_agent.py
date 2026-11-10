"""Run one independent, budgeted development task through the production product chain.

This is a development entry, not an acceptance freeze. It starts a fresh disposable
``parcels`` fixture for every invocation, prepares a fresh generic-intake workspace, and
then uses the same ``TaskService``/``ProductTasks`` path as the product. No model or target
result is synthesized. A failed or unknown run is never retried automatically.
"""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
from prepare_colleague_workspace import prepare

from agenttest.goal_server import product_probe
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.product_design import TestDesignModel
from agenttest.product_focus import TestFocusModel
from agenttest.product_tasks import aggregate_usage
from agenttest.project_goals import ProjectBudget
from agenttest.report import ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TERMINAL, TaskStore

ROOT = Path(__file__).resolve().parents[1]
KIND_OFFSET = {"parcels": 1, "reservations": 2, "directory": 3}


def free_port_block(start):
    for base in range(start, start + 100, 4):
        sockets = []
        try:
            for port in range(base, base + 4):
                probe = socket.socket()
                sockets.append(probe)
                probe.bind(("127.0.0.1", port))
            return base
        except OSError:
            continue
        finally:
            for probe in sockets:
                probe.close()
    raise RuntimeError("NO_FREE_LOOPBACK_PORT_BLOCK")


def wait_health(port, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"TARGET_HEALTH_TIMEOUT:{port}")


class RecordingModel:
    """Keep the decoded model response and the exact request context beside the run."""

    def __init__(self, delegate, name, path):
        self.delegate, self.name, self.path = delegate, name, path
        decode = getattr(delegate, "_decode_output", None)
        if decode is not None:
            def recording_decode(content, *args, **kwargs):
                record = dict(at=time.time(), model=self.name, stage="decode", raw_output=content)
                with self.path.open("a", encoding="utf-8") as sink:
                    sink.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                return decode(content, *args, **kwargs)
            delegate._decode_output = recording_decode

    def __getattr__(self, item):
        return getattr(self.delegate, item)

    @property
    def stats(self):
        return self.delegate.stats

    @property
    def token_reservation(self):
        return self.delegate.token_reservation

    async def generate(self, context):
        try:
            output = await self.delegate.generate(context)
        except Exception as exc:
            record = dict(at=time.time(), model=self.name, context=context,
                          error=type(exc).__name__, error_text=str(exc))
            with self.path.open("a", encoding="utf-8") as sink:
                sink.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            raise
        record = dict(at=time.time(), model=self.name, context=context, output=output,
                      stats=self.delegate.stats.model_dump(mode="json"))
        with self.path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return output


def model_settings(path, timeout, max_input, max_output):
    settings = ModelSettings.from_env_file(path)
    return settings.model_copy(update=dict(
        timeout=timeout, max_input_tokens=max_input, max_output_tokens=max_output,
        max_total_tokens=max_input + max_output))


def limited_project(project, *, max_requests, max_model_calls, max_input,
                    max_output, max_seconds):
    """Apply a development-only loop guard, not a project-wide spend ceiling.

    ``max_model_calls`` caps one task's planning/design calls so a malformed model
    can never loop forever. It is deliberately independent of the number of
    independent development tasks and of their aggregate token use.
    """
    budget = ProjectBudget(
        max_model_calls=min(max_model_calls, 20), max_readonly_investigations=2,
        max_input_tokens=max_input, max_output_tokens=max_output,
        max_total_tokens=min(120_000, max_input + max_output),
        suite_max_model_calls=min(max_model_calls, 20),
        suite_max_total_tokens=500_000)
    target = project.target.model_copy(update=dict(max_requests=max_requests,
                                                   run_timeout=min(float(max_seconds), 300.0)))
    registration = project.registration.model_copy(update=dict(budget=budget))
    return replace(project, target=target, registration=registration)


def collect_reports(service, summary):
    rows = {}
    for case in summary.get("cases") or []:
        run_id = case.get("run_id")
        if not run_id or run_id in rows:
            continue
        try:
            report = service.reports.read(run_id)
        except (OSError, ValueError):
            continue
        rows[run_id] = report.model_dump(mode="json")
    return rows


def evidence_metrics(summary, task_row, model_calls, reports, target_events,
                      max_input_tokens):
    records = summary.get("design_records") or []
    engine_groups = summary.get("planning_engine_groups") or []
    http_attempts = []
    outcomes = []
    for run_id, report in reports.items():
        outcomes.append(dict(run_id=run_id, outcome=report.get("outcome"),
                             reason=report.get("reason")))
        for step in report.get("steps") or []:
            for attempt in step.get("attempts") or []:
                http_attempts.append(dict(method=step.get("method"),
                                          path=step.get("path_template"),
                                          status=attempt.get("status_code"),
                                          attempt=attempt.get("attempt"),
                                          step_id=step.get("step_id")))
    focus = next((record for record in records if record.get("group_id") == "focus"), {})
    actual_inputs = {record.get("group_id"): (record.get("model_call") or {}).get(
        "input_tokens") for record in records}
    known_inputs = [value for value in actual_inputs.values() if value is not None]
    input_limit = dict(limit=max_input_tokens, actual_by_group=actual_inputs,
                       known=len(known_inputs), passed=(bool(known_inputs)
                                                       and all(value <= max_input_tokens
                                                               for value in known_inputs)))
    return dict(
        task_status=task_row.get("status"),
        focus_status=focus.get("status"),
        focus_succeeded=bool(focus.get("status") in {"SELECTED", "PROPOSED"}),
        planning_ir_present=summary.get("planning_ir") is not None,
        planning_instances=(summary.get("planning_ir") or {}).get("coverage", {}).get(
            "instances"),
        planning_ambiguities=len((summary.get("planning_ir") or {}).get(
            "ambiguities") or []),
        planning_engine=summary.get("planning_engine"),
        planning_engine_reason=summary.get("planning_engine_reason"),
        planning_engine_groups=engine_groups,
        design_groups=[dict(group_id=r.get("group_id"), status=r.get("status"),
                            reason=r.get("reason"), plan_construction=r.get("plan_construction"))
                       for r in records],
        http_attempt_count=len(http_attempts),
        http_attempts=http_attempts,
        target_event_count=len(target_events),
        target_events=target_events,
        case_outcomes=outcomes,
        complete=summary.get("complete"),
        task_satisfied=summary.get("task_satisfied"),
        model_calls=model_calls.get("calls"),
        model_tokens=model_calls.get("total_tokens"),
        model_input_tokens=model_calls.get("input_tokens"),
        model_output_tokens=model_calls.get("output_tokens"),
        model_elapsed_ms=model_calls.get("elapsed_ms"),
        input_token_limit=input_limit,
        uncovered_risks=summary.get("not_covered") or [],
        questions=summary.get("questions") or [])


async def run(args):
    run_dir = args.run_dir.resolve()
    if run_dir.exists():
        raise SystemExit(f"RUN_DIRECTORY_EXISTS:{run_dir}")
    run_dir.mkdir(parents=True)
    data = run_dir / "lab"
    evidence = run_dir / "evidence"
    evidence.mkdir()
    base = free_port_block(args.port)
    target_port = base + KIND_OFFSET[args.project_kind]
    events_path = evidence / "target-events.jsonl"
    target_log = (run_dir / "target.log").open("wb")
    env = dict(os.environ, PYTHONUTF8="1", AGENTTEST_LAB_INSTANCE_ID=uuid4().hex)
    environment = dict(env)
    target = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts/serve_colleague_targets.py"),
         "--project", args.project_kind, "--port", str(target_port),
         "--events", str(events_path)],
        cwd=ROOT, env=environment, stdout=target_log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    service = None
    result = dict(schema="agenttest.development-run.v1", attempt=args.attempt,
                  text=args.text, project_kind=args.project_kind,
                  head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                               text=True).strip(),
                  started_at=time.time(), budgets=dict(
                      max_requests=args.max_requests, max_model_calls=args.max_model_calls,
                      max_seconds=args.max_seconds, model_timeout=args.model_timeout,
                      max_input_tokens=args.max_input_tokens,
                      model_input_bytes=args.model_input_bytes,
                      max_output_tokens=args.max_output_tokens))
    try:
        wait_health(target_port)
        await prepare(data, base, environment)
        model_env = (ROOT / args.model_env).resolve() if not Path(args.model_env).is_absolute() \
            else Path(args.model_env)
        settings = model_settings(model_env, args.model_timeout, args.model_input_bytes,
                                  args.max_output_tokens)
        project_service = TaskService(
            {}, TaskStore(data / "workspace/tasks.sqlite3"),
            ReportStore(data / "workspace/reports"),
            lambda project: OpenAICompatiblePlanner(settings, profile="generic",
                                                    planning_constraints=True),
            generic_intake=True, environ={}, model_status=lambda: "CONFIGURED_MODEL",
            focus_factory=lambda project: RecordingModel(
                TestFocusModel(settings, profile="generic"), "focus",
                evidence / "model-outputs.jsonl"),
            designer_factory=lambda project: RecordingModel(
                TestDesignModel(settings, profile="generic"), "design",
                evidence / "model-outputs.jsonl"),
            product_probe_factory=lambda project: product_probe(
                settings.model_copy(update={"timeout": args.model_timeout})))
        service = project_service
        records = service.intake.list()
        matching = [record for record in records if (record.get("generic") or {}).get(
            "base_url") == f"http://127.0.0.1:{target_port}"]
        if len(matching) != 1:
            raise RuntimeError("DEV_PROJECT_NOT_FOUND")
        project_id = matching[0]["id"]
        original_project = service.project

        def bounded_project(identifier):
            project = original_project(identifier)
            return (limited_project(project, max_requests=args.max_requests,
                                    max_model_calls=args.max_model_calls,
                                    max_input=args.model_input_bytes,
                                    max_output=args.max_output_tokens,
                                    max_seconds=args.max_seconds)
                    if identifier == project_id else project)

        service.project = bounded_project
        service.dev_max_seconds = args.max_seconds
        row = await service.submit("dev_operator", project_id, args.text, uuid4().hex)
        task_id = row["id"]
        deadline = time.monotonic() + args.max_seconds + 10
        while time.monotonic() < deadline:
            row = service.db.get(task_id, "dev_operator")
            if row["status"] in TERMINAL:
                break
            await asyncio.sleep(0.5)
        else:
            row = await service.cancel(task_id, "dev_operator")
        summary = row.get("summary") or {}
        reports = collect_reports(service, summary)
        model_calls, usage = aggregate_usage(summary.get("design_records") or [])
        target_events = []
        if events_path.exists():
            target_events = [json.loads(line) for line in events_path.read_text(
                encoding="utf-8").splitlines() if line.strip()]
        result.update(
            task_id=task_id, project_id=project_id, target_port=target_port,
            task_status=row.get("status"), task_reason=summary.get("reason"),
            summary=summary, reports=reports,
            model_calls=model_calls, usage=usage.model_dump(mode="json") if usage else None,
            metrics=evidence_metrics(summary, row, dict(
                calls=model_calls, total_tokens=usage.total_tokens if usage else None,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                elapsed_ms=usage.elapsed_ms if usage else None),
                reports, target_events, args.max_input_tokens),
            target_health=wait_health(target_port), model=dict(
                name=settings.model, base_url=settings.base_url, protocol=settings.protocol,
                timeout=settings.timeout, max_input_tokens=args.max_input_tokens,
                model_input_bytes=settings.max_input_tokens,
                max_output_tokens=settings.max_output_tokens))
        result["finished_at"] = time.time()
        (run_dir / "run.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    except Exception as exc:
        result.update(error=type(exc).__name__, error_message=str(exc),
                      finished_at=time.time())
        try:
            (run_dir / "run.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        raise
    finally:
        if service is not None:
            try:
                await service.close()
            except Exception:
                pass
        if target.poll() is None:
            target.terminate()
        try:
            target.wait(timeout=10)
        except subprocess.TimeoutExpired:
            target.kill()
            target.wait(timeout=10)
        target_log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True, help="Natural-language development request")
    parser.add_argument("--attempt", required=True, help="Unique attempt label")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--project-kind", choices=sorted(KIND_OFFSET), default="parcels")
    parser.add_argument("--port", type=int, default=18300)
    parser.add_argument("--max-requests", type=int, default=24)
    parser.add_argument("--max-model-calls", type=int, default=8)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--model-timeout", type=float, default=45.0)
    parser.add_argument("--max-input-tokens", type=int, default=20_000,
                        help="Reported actual provider token cap; the transport's conservative "
                             "byte envelope is separately controlled by --model-input-bytes")
    parser.add_argument("--model-input-bytes", type=int, default=60_000,
                        help="Conservative UTF-8 request envelope used by the transport")
    parser.add_argument("--max-output-tokens", type=int, default=4_096)
    parser.add_argument("--model-env", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if not 0 < args.max_seconds <= 600:
        parser.error("development deadline must be between 1 and 600 seconds")
    if not 1 <= args.max_model_calls <= 20:
        parser.error("development per-task model-call loop guard must be between 1 and 20")
    if not 1 <= args.max_requests <= 40:
        parser.error("development request loop guard must be between 1 and 40")
    if not 0 < args.model_timeout <= 45:
        parser.error("model timeout must be at most 45 seconds")
    if not 1 <= args.max_input_tokens <= 20_000:
        parser.error("reported model input token cap must be at most 20000 tokens")
    if not args.max_input_tokens <= args.model_input_bytes <= 60_000:
        parser.error("model byte envelope must be between the token cap and 60000")
    if not 1 <= args.max_output_tokens <= 4_096:
        parser.error("model output budget must be at most 4096 tokens")
    result = asyncio.run(run(args))
    print(json.dumps(dict(task_id=result.get("task_id"), status=result.get("task_status"),
                          metrics=result.get("metrics"), run_dir=str(args.run_dir),
                          usage=result.get("usage")), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
