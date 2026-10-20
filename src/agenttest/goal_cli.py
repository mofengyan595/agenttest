"""CLI-first project goal entry; legacy operator TaskContract commands stay compatible."""

import asyncio
import json
import os

from agenttest.goal_derivation import derive_goal
from agenttest.project_goals import check_goal_credentials, load_goal_project
from agenttest.report import Outcome, ReportStore


def goal_command(args) -> int:
    project = load_goal_project(args.project)
    identity = project.identity()

    def guard():
        if load_goal_project(args.project).identity() != identity:
            raise ValueError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")

    derived = derive_goal(project, args.user_task)
    summary = derived.summary()
    summary["budget"] = dict(project.registration.budget.model_dump(),
                             max_requests=project.target.max_requests)
    suite_mode = args.suite or derived.scope == "project"
    if suite_mode and args.preview:
        from agenttest.goal_suite import build_suite

        print(json.dumps(build_suite(project, args.user_task, environ=dict(os.environ)).preview(),
                         ensure_ascii=False))
        return 0
    if derived.task is None and not suite_mode:
        print(json.dumps(summary, ensure_ascii=False))
        return 1
    environ = dict(os.environ)
    transport = None
    if args.synthetic_target:
        environ, transport = synthetic_runtime(project)
    if not suite_mode:
        try:
            check_goal_credentials(project, derived.task, environ)
        except ValueError:
            print(json.dumps(dict(summary, status="NEEDS_REVIEW",
                reason="PROJECT_CREDENTIAL_PREFLIGHT_FAILED"), ensure_ascii=False))
            return 2
    if args.preview:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if project.registration.require_plan_review and not args.review_approved:
        print(json.dumps(dict(summary, status="NEEDS_REVIEW", reason="PLAN_REVIEW_REQUIRED"),
                         ensure_ascii=False))
        return 1
    from agenttest.goal_runtime import run_goal
    from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
    from agenttest.project import _read
    from agenttest.workflow import FakeLLM, WorkflowBudget

    budget = WorkflowBudget()
    capture = None
    if args.export_regression:
        from agenttest.regression import validate_destination

        if suite_mode:
            raise ValueError("Suite exports select individual completed runs in the task API")
        validate_destination(args.export_regression)
    if args.real:
        settings = (ModelSettings.from_env_file(args.model_env) if args.model_env
                    else ModelSettings.from_environment())
        settings = settings.model_copy(update={
            name: getattr(project.registration.budget, name) for name in (
                "max_input_tokens", "max_output_tokens", "max_total_tokens")})
        settings = ModelSettings.model_validate({**settings.model_dump(),
                                                "timeout": args.model_timeout})
        if project.registration.native_function_calling:
            from agenttest.native_tools import NativeFunctionPlanner

            planner = NativeFunctionPlanner(settings, provider_supported=True)
        else:
            planner = OpenAICompatiblePlanner(settings, profile="generic",
                                               planning_constraints=True)
        budget = WorkflowBudget(model_timeout=settings.timeout + 1,
                                workflow_timeout=min(settings.timeout + 90, 300))
    else:
        planner = FakeLLM(_read(args.fake_plan).decode("utf-8"))
    if args.export_regression:
        from agenttest.regression import RegressionCapture

        capture = RegressionCapture(planner)
        planner = capture
    store = ReportStore(args.runs_dir)
    analyzer = None
    if project.capabilities.entries():
        from agenttest.evidence_context import EvidenceFeedbackModel
        from agenttest.feedback import FakeFeedback

        analyzer = EvidenceFeedbackModel(settings) if args.real else FakeFeedback()
    if suite_mode:
        from agenttest.goal_suite import run_suite

        result = asyncio.run(run_suite(project, args.user_task, planner, store,
            environ=environ, budget=budget, analyzer=analyzer, transport=transport,
            execution_guard=guard))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["complete"] else 1
    derived, report = asyncio.run(run_goal(project, args.user_task, planner, store,
        environ=environ, budget=budget, analyzer=analyzer, transport=transport,
        execution_guard=guard))
    exported = None
    if capture is not None and report.outcome == Outcome.PASS and report.task_satisfied:
        from agenttest.regression import RegressionRuntime, export_regression

        runtime = RegressionRuntime(project.document, project.target, project.registry,
                                    derived.task, environ, None, store)
        export_regression(args.export_regression, runtime, capture.scenario, report)
        exported = str(args.export_regression.absolute())
    print(json.dumps(dict(derived.summary(), run_id=report.run_id,
        outcome=report.outcome.value, task_satisfied=report.task_satisfied,
        regression_directory=exported,
        report=str(store.root / report.run_id / "report.json")), ensure_ascii=False))
    return 0 if report.outcome == Outcome.PASS else 1


def synthetic_runtime(project, fault="none"):
    """Only versioned disposable targets; cannot authorize an external service."""
    import httpx

    from agenttest.contracts import load_openapi
    from agenttest.portability_demo import project_inputs

    app, document, _, _, environ = project_inputs(project.registration.id, fault)
    if load_openapi(document).model_dump() != project.contract.model_dump():
        raise ValueError("SYNTHETIC_CONTRACT_MISMATCH")
    return environ, httpx.ASGITransport(app=app)
