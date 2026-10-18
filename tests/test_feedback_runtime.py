"""Feedback uses original authority and preserves evidence on every bounded exit."""

import asyncio
import json

import httpx
import pytest
from test_planner_input import scenario
from test_readonly_runtime import assert_original_preserved, setup

pytest.importorskip("langgraph")

from agenttest.feedback import FeedbackAction, FeedbackModel
from agenttest.investigation import FeedbackBudget, InvestigationConfig
from agenttest.llm_planner import ModelSettings
from agenttest.report import Outcome


class Decider:
    def __init__(self, kind="supported"):
        self.kind, self.calls, self.contexts, self.first = kind, 0, [], None

    async def generate(self, context):
        self.calls += 1
        self.contexts.append(json.loads(json.dumps(context)))
        if self.kind == "timeout":
            await asyncio.sleep(10)
        if self.kind == "insufficient":
            return json.dumps(dict(action="finish", selection=None,
                finding="EVIDENCE_INSUFFICIENT", evidence_refs=[]))
        if self.kind == "unsupported":
            return json.dumps(dict(action="finish", selection=None,
                finding="ACCESS_BOUNDARY_VIOLATION", evidence_refs=["original"]))
        if self.calls == 1 or self.kind in {"duplicate", "two_reads"} and self.calls == 2:
            if self.calls == 1:
                choice = next((c for c in context["choices"] if c["role"] == "alice"),
                    {"capability_id": "course_owner_read", "role": "alice",
                     "resource_ref": "a" * 32})
                self.first = dict(choice)
            elif self.kind == "duplicate":
                choice = self.first
            else:
                choice = context["choices"][0]
            if self.kind == "unknown_tool":
                choice = {**choice, "capability_id": "unapproved"}
            if self.kind == "bad_role":
                choice = {**choice, "role": "unapproved"}
            if self.kind == "bad_parameter":
                choice = {**choice, "url": "private-sensitive-url"}
            return json.dumps(dict(action="investigate", selection=choice,
                                   finding=None, evidence_refs=[]))
        return json.dumps(dict(action="finish", selection=None,
            finding=("PERSISTENT_READBACK_MISMATCH" if self.kind == "readback"
                     else "ACCESS_BOUNDARY_VIOLATION"),
            evidence_refs=["original", context["evidence"][0]["ref"]]))


async def run(tmp_path, kind="supported", *, budget=None, fault="ownership", tool_fault=None):
    analyzer = Decider(kind)
    executor, calls, _ = setup(tmp_path, analyzer=analyzer, fault=fault,
                                investigation_fault=tool_fault)
    executor.investigation = InvestigationConfig(enabled=True,
        feedback=budget or FeedbackBudget(timeout=3.0))
    report = await executor.run(scenario(True))
    return report, executor, calls, analyzer


@pytest.mark.asyncio
async def test_feedback_observes_read_evidence_then_finishes_without_changing_verdict(tmp_path):
    report, executor, calls, analyzer = await run(tmp_path)
    assert_original_preserved(executor, report, calls)
    assert report.feedback.state == "SUPPORTED" and analyzer.calls == 2
    assert report.feedback.finding == "ACCESS_BOUNDARY_VIOLATION"
    assert report.feedback.requests_used == 1
    assert report.feedback.evidence_refs == ["original", report.investigations[0].step_id]
    assert report.investigations[0].outcome == Outcome.PASS
    assert analyzer.contexts[0]["evidence"] == []
    assert analyzer.contexts[1]["evidence"][0]["outcome"] == "PASS"
    assert report == executor.store.read(report.run_id)
    serialized = json.dumps(analyzer.contexts)
    assert "private-wrong-name" not in serialized and "session-private" not in serialized
    assert "resource_id" not in serialized and "http://" not in serialized


@pytest.mark.asyncio
async def test_two_reads_and_final_decision_reserve_fourth_run_model_slot(tmp_path):
    report, executor, calls, analyzer = await run(tmp_path, "two_reads")
    assert_original_preserved(executor, report, calls)
    assert report.feedback.state == "SUPPORTED" and analyzer.calls == 3
    assert report.feedback.requests_used == 2 and len(report.investigations) == 2
    assert analyzer.contexts[-1]["finish_required"]
    assert analyzer.contexts[-1]["choices"] == []
    assert report.feedback.model_calls_used == 3  # plus original Workflow planning: four.


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,reason", [
    ("unknown_tool", "FEEDBACK_SELECTION_REJECTED"),
    ("bad_role", "FEEDBACK_SELECTION_REJECTED"),
    ("bad_parameter", "FEEDBACK_ACTION_REJECTED"),
    ("unsupported", "FEEDBACK_UNSUPPORTED_FINDING"),
    ("insufficient", "FEEDBACK_EVIDENCE_INSUFFICIENT"),
    ("duplicate", "FEEDBACK_DUPLICATE_INVESTIGATION"),
])
async def test_rejections_never_trigger_extra_business_or_replace_original(tmp_path, kind, reason):
    report, executor, calls, analyzer = await run(tmp_path, kind)
    assert_original_preserved(executor, report, calls)
    assert report.feedback.reason == reason and report.feedback.state == "NEEDS_REVIEW"
    assert report.feedback.requests_used == (1 if kind == "duplicate" else 0)
    assert "private-sensitive-url" not in report.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["max_model_calls", "max_requests"])
async def test_zero_budget_stops_without_tool_io(tmp_path, field):
    report, executor, calls, analyzer = await run(tmp_path,
        "insufficient" if field == "max_requests" else "supported",
        budget=FeedbackBudget(**{field: 0}, timeout=3.0))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.requests_used == 0
    assert report.feedback.state == ("BUDGET_EXHAUSTED"
                                    if field == "max_model_calls" else "NEEDS_REVIEW")
    assert analyzer.calls == (0 if field == "max_model_calls" else 1)


@pytest.mark.asyncio
async def test_last_decision_cannot_be_spent_on_tool_without_final_conclusion_budget(tmp_path):
    report, executor, calls, analyzer = await run(tmp_path,
        budget=FeedbackBudget(max_model_calls=1, timeout=3.0))
    assert_original_preserved(executor, report, calls)
    # Deliberately disobey finish_required while retaining a syntactically valid choice.
    assert report.feedback.state == "BUDGET_EXHAUSTED"
    assert report.feedback.requests_used == 0 and analyzer.calls == 1


@pytest.mark.asyncio
async def test_timeout_and_tool_error_have_distinct_exits_and_preserve_failure(tmp_path):
    report, executor, calls, _ = await run(tmp_path / "timeout", "timeout",
        budget=FeedbackBudget(timeout=0.05))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.state == "TIMEOUT" and report.feedback.requests_used == 0

    async def unavailable(request):
        raise httpx.ConnectError("private-network-detail")

    report, executor, calls, _ = await run(tmp_path / "tool", tool_fault=unavailable)
    assert_original_preserved(executor, report, calls)
    assert report.feedback.state == "TEST_INFRA_ERROR" and report.feedback.requests_used == 1
    assert report.investigations[0].outcome == Outcome.TEST_INFRA_ERROR
    assert "private-network-detail" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_pass_skips_the_entire_feedback_loop(tmp_path):
    report, _, _, analyzer = await run(tmp_path, fault="none")
    assert report.outcome == Outcome.PASS and report.feedback is None and analyzer.calls == 0


@pytest.mark.asyncio
async def test_repeated_readback_failure_is_a_symptom_not_a_repaired_assertion(tmp_path):
    report, executor, calls, analyzer = await run(tmp_path, "readback", fault="name")
    assert_original_preserved(executor, report, calls)
    assert report.feedback.state == "SUPPORTED" and analyzer.calls == 2
    assert report.feedback.finding == "PERSISTENT_READBACK_MISMATCH"
    assert report.investigations[0].outcome == Outcome.ASSERTION_FAILED


@pytest.mark.asyncio
async def test_http_error_is_not_corroboration_of_a_field_mismatch(tmp_path):
    async def error_response(request):
        return httpx.Response(500, json={})

    report, executor, calls, _ = await run(tmp_path, "readback", fault="name",
                                           tool_fault=error_response)
    assert_original_preserved(executor, report, calls)
    assert report.feedback.reason == "FEEDBACK_UNSUPPORTED_FINDING"
    assert report.feedback.finding == "EVIDENCE_INSUFFICIENT"


@pytest.mark.asyncio
async def test_unknown_model_usage_stops_before_another_decision_or_tool(tmp_path):
    analyzer = FeedbackModel(ModelSettings(model="offline-unknown-feedback",
        base_url="https://offline.invalid/v1", api_key="synthetic-feedback-only"),
        httpx.MockTransport(lambda request: httpx.Response(503)))
    executor, calls, _ = setup(tmp_path, analyzer=analyzer)
    executor.investigation = InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=3.0))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.reason == "FEEDBACK_MODEL_USAGE_UNKNOWN"
    assert report.feedback.model_calls_used == 1 and report.feedback.requests_used == 0
    assert report.feedback.decisions[0].model_call.calls == 1
    assert report.feedback.decisions[0].model_call.total_tokens is None


@pytest.mark.asyncio
async def test_authority_mutation_in_model_callback_is_rejected_and_restored(tmp_path):
    analyzer = Decider()
    executor, calls, _ = setup(tmp_path, analyzer=analyzer)
    executor.investigation = InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=3.0))
    original = executor.config.model_dump()
    generate = analyzer.generate

    async def corrupt(context):
        response = await generate(context)
        executor.config.max_requests = 200
        return response

    analyzer.generate = corrupt
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.reason == "FEEDBACK_AUTHORITY_CHANGED"
    assert report.feedback.requests_used == 0 and executor.config.model_dump() == original


@pytest.mark.asyncio
async def test_workflow_timeout_in_first_decision_retains_original_failure_without_reads(
    tmp_path, monkeypatch,
):
    from timeout_control import controlled_workflow_timeout

    from agenttest.demo import PROJECT_ROOT
    from agenttest.dsl import Scenario
    from agenttest.investigation import ReadOnlyCapabilityRegistry
    from agenttest.natural_demo import run_natural_demo
    from agenttest.report import ReportStore
    from agenttest.workflow import FakeLLM, WorkflowBudget

    expire = controlled_workflow_timeout(monkeypatch, 2.0)

    class ExpiringAnalyzer:
        async def generate(self, context):
            expire()
            await asyncio.Event().wait()

    plan = Scenario.model_validate_json(
        (PROJECT_ROOT / "examples/nl-ownership-plan.json").read_bytes())
    plan.steps[2].body["name"].value = "首轮调查超时核对"
    store = ReportStore(tmp_path)
    report = await run_natural_demo('alice 创建“首轮调查超时核对”并查询核对 ID 和名称，'
        '再让 bob 查询同一课程被拒绝。', FakeLLM(plan.model_dump_json()), store,
        fault="ownership", budget=WorkflowBudget(workflow_timeout=2.0),
        investigation=InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=10.0)),
        capabilities=ReadOnlyCapabilityRegistry.from_json(
            (PROJECT_ROOT / "examples/nl-readonly-capabilities.json").read_bytes()),
        analyzer=ExpiringAnalyzer())
    assert report.outcome == Outcome.ASSERTION_FAILED and not report.investigations
    assert report.feedback.state == "CANCELLED"
    assert report.investigation_error == "WORKFLOW_TIMEOUT"
    assert not report.workflow_completed and report.task_satisfied is False
    assert report == store.read(report.run_id)


@pytest.mark.asyncio
async def test_typed_model_adapter_reuses_usage_and_privacy_checks(tmp_path):
    response = dict(action="finish", selection=None, finding="EVIDENCE_INSUFFICIENT",
                    evidence_refs=[])
    seen = []

    def completion(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(response)}}], "usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    analyzer = FeedbackModel(ModelSettings(model="offline-feedback",
        base_url="https://offline.invalid/v1", api_key="synthetic-feedback-only"),
        httpx.MockTransport(completion))
    executor, calls, _ = setup(tmp_path, analyzer=analyzer)
    executor.investigation = InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=3.0))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.reason == "FEEDBACK_EVIDENCE_INSUFFICIENT"
    assert report.feedback.decisions[0].model_call.total_tokens == 150
    assert seen[0]["response_format"]["json_schema"]["name"] == "agenttest_feedback"
    assert FeedbackAction.model_validate(response).action == "finish"
