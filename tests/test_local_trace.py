"""Trace correlation excludes values and cannot infer real-model provenance."""

import json

import pytest

from agenttest.local_trace import build_trace, export_trace
from agenttest.report import (
    CheckResult,
    FeedbackDecisionEvidence,
    FeedbackResult,
    InvestigationResult,
    ModelCallStats,
    Outcome,
    RunReport,
    StepResult,
)


def report():
    return RunReport(scenario_sha256="a" * 64, contract_sha256="b" * 64,
        environment="isolated", target_origin="http://127.0.0.1:8123",
        outcome=Outcome.ASSERTION_FAILED, dsl_valid=True, policy_authorized=True,
        intent_verified=True, steps=[StepResult(step_id="read", operation_id="readResource",
            method="GET", path_template="/private-resource/{resource_id}",
            outcome=Outcome.ASSERTION_FAILED, reason="POLICY_EVALUATED", policy_sha256="c" * 64,
            checks=[CheckResult(kind="equals", selector="$.private_field", passed=False,
                                expected="private-business-secret",
                                actual="private-auth-secret")])],
        model_call=ModelCallStats(calls=1, input_tokens=100, output_tokens=50, total_tokens=150),
        feedback=FeedbackResult(state="SUPPORTED", finding="PERSISTENT_READBACK_MISMATCH",
            reason="FEEDBACK_FINDING_SUPPORTED", evidence_refs=["original", "inspect"],
            decisions=[FeedbackDecisionEvidence(index=0, action="investigate",
                model_call=ModelCallStats(calls=1, input_tokens=70,
                                          output_tokens=30, total_tokens=100))]),
        investigations=[InvestigationResult(step_id="inspect", operation_id="readResource",
            parent_step_id="read", method="GET", path_template="/private-resource/{resource_id}",
            outcome=Outcome.ASSERTION_FAILED, reason="POLICY_EVALUATED", policy_sha256="c" * 64)])


def test_trace_has_separate_business_and_feedback_outcomes_and_no_raw_values(tmp_path):
    original = report()
    trace = export_trace(original, tmp_path / "trace.json")
    assert trace.business_outcome == Outcome.ASSERTION_FAILED
    assert trace.feedback_state == "SUPPORTED"
    assert {span.phase for span in trace.spans} == {
        "planning", "validation", "execution", "investigation", "conclusion"}
    assert trace.recorded_calls == 2 and trace.total_tokens == 250
    assert trace.external_upload is False
    assert [span.phase for span in trace.spans].index("execution") < [
        span.phase for span in trace.spans].index("investigation")
    assert trace.real_model_provenance == "NOT_INFERRED_FROM_REPORT"
    data = (tmp_path / "trace.json").read_text(encoding="utf-8")
    for private in ("private-business-secret", "private-auth-secret", "private-resource",
                    "private_field", "127.0.0.1"):
        assert private not in data
    assert original.steps[0].checks[0].actual == "private-auth-secret"
    inspection = next(span for span in trace.spans if span.step_id == "inspect")
    assert inspection.parent_span_id == next(span.span_id for span in trace.spans
                                             if span.step_id == "read")
    with pytest.raises(FileExistsError):
        export_trace(original, tmp_path / "trace.json")


def test_unknown_and_missing_usage_are_not_manufactured_zero_or_real_calls():
    source = report()
    source.feedback.decisions[0].model_call.total_tokens = None
    trace = build_trace(source)
    assert trace.usage_status == "UNKNOWN" and trace.total_tokens is None
    source.model_call = source.feedback = None
    trace = build_trace(source)
    assert trace.usage_status == "UNRECORDED" and trace.recorded_calls is None
    assert trace.total_tokens is None


def test_unknown_error_text_is_not_repeated_in_trace():
    source = report()
    source.model_call.reason = "password=private-auth-secret"
    assert "private-auth-secret" not in build_trace(source).model_dump_json()


def test_trace_cli_does_not_upload_or_change_report(tmp_path, capsys):
    from agenttest.__main__ import main
    from agenttest.report import ReportStore

    store = ReportStore(tmp_path / "reports")
    source = report()
    store.save(source)
    assert main(["export-trace", "--run-id", source.run_id, "--runs-dir", str(store.root),
                 "--output", str(tmp_path / "trace.json")]) == 0
    assert json.loads(capsys.readouterr().out)["external_upload"] is False
    assert store.read(source.run_id) == source
