"""Fresh-position scheduling cannot retry a run or cross an unknown-usage boundary."""

import json

import pytest

from agenttest.diagnostics import Diagnostic, Layer
from agenttest.paired_evaluation import (
    known_schema_failure,
    match_parent_protocol,
    stop_paid_schedule,
    unattempted_parent,
)
from agenttest.report import (
    FeedbackDecisionEvidence,
    FeedbackResult,
    ModelCallStats,
    Outcome,
    ReportStore,
    RunReport,
)


def failed_report():
    return RunReport(scenario_sha256="a" * 64, contract_sha256="b" * 64,
        environment="isolated", target_origin="http://127.0.0.1",
        outcome=Outcome.ASSERTION_FAILED, feedback=FeedbackResult(state="TOOL_ERROR",
            reason="FEEDBACK_MODEL_ERROR", decisions=[FeedbackDecisionEvidence(index=0,
                action="rejected", model_call=ModelCallStats(calls=1, http_status=200,
                    total_tokens=19, diagnostic=Diagnostic(layer=Layer.MODEL,
                        code="MODEL_SCHEMA_VALIDATOR_REJECTED")))]))


def test_known_format_error_stops_run_but_not_next_unused_position():
    report = failed_report()
    before = report.model_dump_json()
    assert known_schema_failure(report)
    assert not stop_paid_schedule(report)
    assert report.model_dump_json() == before
    assert report.feedback.state == "TOOL_ERROR"
    assert report.outcome == Outcome.ASSERTION_FAILED


@pytest.mark.parametrize("mutation", ["usage", "http", "read", "timeout", "infra", "cancel"])
def test_unknown_usage_or_external_failure_still_stops_every_next_paid_position(mutation):
    report = failed_report()
    if mutation == "usage":
        report.feedback.decisions[0].model_call.total_tokens = None
    elif mutation == "http":
        report.feedback.decisions[0].model_call.http_status = 503
    elif mutation == "read":
        report.feedback.reason = "FEEDBACK_READ_FAILED"
    elif mutation == "infra":
        report.outcome = Outcome.TEST_INFRA_ERROR
    else:
        report.feedback.state = {"timeout": "TIMEOUT", "cancel": "CANCELLED"}[mutation]
    assert stop_paid_schedule(report)


def test_parent_requires_sealed_known_usage_failure_and_only_returns_existing_rows(tmp_path,
                                                                                monkeypatch):
    import agenttest.paired_evaluation as module

    # Archive validation has independent positive/tamper coverage; isolate boundary decisions here.
    monkeypatch.setattr(module, "verify_paired", lambda path: {"archive_verified": True})
    store = ReportStore(tmp_path / "reports")
    report = failed_report()
    store.save(report)
    data = {"results": [{"run_id": report.run_id, "usage_known": True}, {"run_id": None}]}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    assert unattempted_parent(tmp_path) == data
    assert path.read_bytes() == before
    report.feedback.decisions[0].model_call.total_tokens = None
    store.save(report)
    with pytest.raises(ValueError, match="Unknown"):
        unattempted_parent(tmp_path)


def protocol():
    names = ("cases", "rounds", "order", "source", "tasks", "contexts", "capabilities", "model",
        "authority", "planner_prompt_sha256", "planning_requests", "feedback_prompt_sha256",
        "feedback_schema_sha256", "max_calls", "max_tokens")
    return {name: name for name in names} | {"git": {"commit": "a" * 40}}


def test_fresh_position_schedule_does_not_allow_new_prompt_tasks_or_model(monkeypatch):
    import agenttest.paired_evaluation as module

    monkeypatch.setattr(module, "_git", lambda *args: "src/agenttest/paired_evaluation.py")
    match_parent_protocol(protocol(), protocol())
    changed = protocol() | {"planner_prompt_sha256": "different"}
    with pytest.raises(ValueError, match="protocol changed"):
        match_parent_protocol(changed, protocol())


def test_fresh_position_schedule_does_not_allow_execution_engine_changes(monkeypatch):
    import agenttest.paired_evaluation as module

    monkeypatch.setattr(module, "_git", lambda *args: "src/agenttest/feedback_runtime.py")
    with pytest.raises(ValueError, match="engine changed"):
        match_parent_protocol(protocol(), protocol())
