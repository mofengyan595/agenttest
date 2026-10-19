"""One disposable run under an explicitly selected source export; no Git mutation."""

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--case-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--mode", choices=["prepare", "mock", "real"], required=True)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--identity-file", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.engine_root / "src"))

    import httpx

    from agenttest.composed_planning import FrozenContextPlanner
    from agenttest.contracts import load_openapi
    from agenttest.dsl import Scenario
    from agenttest.evidence_context import EvidenceFeedbackModel
    from agenttest.evidence_evaluation import mock_transport
    from agenttest.investigation import FeedbackBudget, InvestigationConfig
    from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
    from agenttest.planner_input import planning_context
    from agenttest.report import ReportStore, fingerprint
    from agenttest.task_contract import task_validator
    from agenttest.workflow import Workflow, WorkflowBudget

    engine_file = Path(sys.modules["agenttest.llm_planner"].__file__).resolve()
    if engine_file != (args.engine_root / "src/agenttest/llm_planner.py").resolve():
        raise ValueError("Loaded engine does not match selected source export")
    helper = Path(__file__).resolve().parents[1] / "src/agenttest/nl_tasks.py"
    spec = importlib.util.spec_from_file_location("shared_nl_inputs", helper)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    case = json.loads(args.case_file.read_bytes())
    app, doc, target, registry, env, task, draft, caps = fixture.task_inputs_for(case)
    settings = (ModelSettings.from_env_file(args.model_env) if args.model_env else
        ModelSettings(model="offline-nl-reliability", base_url="https://offline.invalid",
                      api_key="synthetic-nl-only")).model_copy(update={"timeout": 60.0})
    candidate = args.arm == "candidate"
    extra = {"planning_constraints": True} if candidate else {}
    transport = None if args.mode == "real" else mock_transport(draft)
    delegate = OpenAICompatiblePlanner(settings, transport, profile="generic", **extra)
    context = planning_context(load_openapi(doc), target, registry, task.user_task,
                               Scenario.model_json_schema())
    payload = json.loads(delegate.prepare_request(context))

    def guard():
        if args.identity_file:
            expected = json.loads(args.identity_file.read_bytes())
            for path, digest in expected.items():
                if fingerprint(Path(path).read_bytes().hex()) != digest:
                    raise ValueError("Frozen execution source changed")

    guard()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "prepared.json").write_text(json.dumps(dict(
        task=task.model_dump(mode="json"), context=context, payload=payload,
        contract_sha256=fingerprint(load_openapi(doc).model_dump()),
        policy_sha256=registry.digest(), target_sha256=fingerprint(target.model_dump()),
        capabilities=[c.model_dump() for c in caps.entries()]),
        ensure_ascii=False, indent=2), encoding="utf-8")
    if args.mode == "prepare":
        print(json.dumps(dict(prepared=True, model_calls=0)))
        return
    planner = FrozenContextPlanner(delegate, context, guard)
    investigation = dict(enabled=True, feedback=FeedbackBudget(timeout=180))
    if candidate:
        investigation["trusted_symptoms"] = True
    started = time.perf_counter()
    report = asyncio.run(Workflow(doc, target, registry, planner,
        ReportStore(args.output / "reports"), httpx.ASGITransport(app), env,
        WorkflowBudget(model_timeout=65, workflow_timeout=300), user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(doc), target, registry),
        investigation=InvestigationConfig(**investigation), capabilities=caps,
        analyzer=EvidenceFeedbackModel(settings, transport), execution_guard=guard).run())
    guard()
    (args.output / "worker.json").write_text(json.dumps(dict(run_id=report.run_id,
        engine_file=str(engine_file), engine_sha256=fingerprint(engine_file.read_bytes().hex()),
        scenario=planner.plan.model_dump(mode="json") if planner.plan else None,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3)),
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(run_id=report.run_id, outcome=report.outcome.value)))


if __name__ == "__main__":
    main()
