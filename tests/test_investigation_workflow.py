import asyncio
import json

import httpx
import pytest
from test_planner_input import scenario

pytest.importorskip("langgraph", reason="Install agent extra for investigation workflow")

from agenttest.investigation import FakeAnalyzer
from agenttest.investigation_demo import DEMO_TASK, investigation_inputs, run_investigation_demo
from agenttest.llm_planner import PlannerFailure
from agenttest.natural_demo import natural_inputs, run_natural_demo
from agenttest.report import Outcome, ReportStore
from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,count", [("none", 0), ("ownership", 1)])
async def test_dynamic_investigation_demo(tmp_path, fault, count):
    store = ReportStore(tmp_path)
    report = await run_investigation_demo(store, fault=fault)
    assert len(report.investigations) == count
    assert report.outcome == (Outcome.PASS if fault == "none" else Outcome.ASSERTION_FAILED)
    assert report.workflow_completed and report == store.read(report.run_id)
    assert sum(s.operation_id == "createCourse" for s in report.steps) == 1
    if count:
        assert report.investigations[0].outcome == Outcome.PASS
        assert report.investigations[0].parent_step_id == "other_read"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["invalid_json", "policy", "intent", "abstain", "tool_budget"])
async def test_preflight_rejection_and_abstention_skip_investigation(tmp_path, kind):
    plan, config, capabilities = investigation_inputs()
    planner = FakeLLM(plan.model_dump_json())
    budget = WorkflowBudget()
    if kind == "invalid_json":
        planner.response = "invalid-private-output"
    elif kind == "policy":
        plan.steps[-1].policy_id = "forged_policy"
        planner.response = plan.model_dump_json()
    elif kind == "intent":
        planner.response = scenario(True, name="wrong-name").model_dump_json()
    elif kind == "abstain":
        class Abstainer:
            async def generate(self, context):
                raise PlannerFailure(Outcome.NEEDS_REVIEW, "MODEL_NO_APPROVED_PLAN")

        planner = Abstainer()
    else:
        budget.max_tool_calls = 0
    analyzer = FakeAnalyzer()
    report = await run_natural_demo(DEMO_TASK, planner, ReportStore(tmp_path), budget=budget,
        fault="ownership", investigation=config, capabilities=capabilities, analyzer=analyzer)
    assert not report.steps and not report.investigations and analyzer.calls == 0
    assert report.outcome != Outcome.PASS


@pytest.mark.asyncio
async def test_workflow_timeout_during_analysis_preserves_business_failure(tmp_path, monkeypatch):
    from timeout_control import controlled_workflow_timeout

    expire = controlled_workflow_timeout(monkeypatch, 2.0)

    class SlowAnalyzer(FakeAnalyzer):
        async def generate(self, context):
            expire()
            await asyncio.Event().wait()

    plan, config, capabilities = investigation_inputs()
    report = await run_natural_demo(DEMO_TASK, FakeLLM(plan.model_dump_json()),
        ReportStore(tmp_path), fault="ownership", investigation=config,
        capabilities=capabilities, analyzer=SlowAnalyzer(),
        budget=WorkflowBudget(workflow_timeout=2.0))
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.investigation_error == "WORKFLOW_TIMEOUT"
    assert not report.investigations[0].attempts
    assert report.investigations[0].reason == "INVESTIGATION_CANCELLED"
    assert len(report.steps) == 5 and not report.workflow_completed


@pytest.mark.asyncio
async def test_parent_cancellation_during_analysis_preserves_business_failure(tmp_path):
    entered = asyncio.Event()

    class InterruptedAnalyzer(FakeAnalyzer):
        async def generate(self, context):
            entered.set()
            await asyncio.Event().wait()

    plan, config, capabilities = investigation_inputs()
    store = ReportStore(tmp_path)
    execution = asyncio.create_task(run_natural_demo(
        DEMO_TASK, FakeLLM(plan.model_dump_json()), store, fault="ownership",
        investigation=config, capabilities=capabilities, analyzer=InterruptedAnalyzer()))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
    report = store.read(next(tmp_path.iterdir()).name)
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.analysis_code == "REVIEW_FAILURE_EVIDENCE"
    assert report.investigation_error == "WORKFLOW_CANCELLED"
    assert report.investigations[0].reason == "INVESTIGATION_CANCELLED"
    assert len(report.steps) == 5 and not report.workflow_completed
    assert report.task_satisfied is False


@pytest.mark.asyncio
@pytest.mark.parametrize("permanent", [False, True])
async def test_workflow_report_failure_does_not_replay_or_change_original(tmp_path, permanent):
    class FailingStore(ReportStore):
        failed = False

        def save(self, report):
            if report.workflow_nodes[-1:] == ["report"] and (permanent or not self.failed):
                self.failed = True
                raise OSError("private-report-path")
            return super().save(report)

    target, target_config, registry, environ = natural_inputs("ownership")
    calls = []

    @target.middleware("http")
    async def count_calls(request, next_handler):
        calls.append((request.method, request.url.path))
        return await next_handler(request)

    plan, config, capabilities = investigation_inputs()
    store = FailingStore(tmp_path)
    report = await Workflow(json.dumps(target.openapi()).encode(), target_config, registry,
        FakeLLM(plan.model_dump_json()), store, httpx.ASGITransport(target), environ,
        user_task=DEMO_TASK, investigation=config, capabilities=capabilities,
        analyzer=FakeAnalyzer()).run()
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert sum(m == "POST" and p == "/api/courses" for m, p in calls) == 1
    assert len(calls) == 6 and not report.workflow_completed
    assert report.investigation_error == ("INVESTIGATION_REPORT_ERROR" if permanent
                                         else "WORKFLOW_ERROR")
    assert "private-report-path" not in report.model_dump_json()
    assert store.read(report.run_id).outcome == Outcome.ASSERTION_FAILED
