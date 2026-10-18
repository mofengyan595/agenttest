"""Local spans from existing reports; no telemetry client, raw values or external upload."""

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from agenttest.onboarding import _public_metadata
from agenttest.report import ModelCallStats, Outcome, RunReport, fingerprint
from agenttest.report_files import pinned_directory
from agenttest.report_queries import public_report
from agenttest.safety import StrictModel


class TraceSpan(StrictModel):
    span_id: str
    parent_span_id: str | None = None
    phase: Literal["planning", "validation", "execution", "investigation", "conclusion"]
    label: str
    state: str
    step_id: str | None = None
    operation_id: str | None = None
    capability_id: str | None = None
    role: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str | None = None
    elapsed_ms: float | None = None
    requests: list[dict] = Field(default_factory=list)
    model: dict | None = None
    checks: list[dict] = Field(default_factory=list)
    diagnostic: dict | None = None


class LocalTrace(StrictModel):
    schema_version: Literal["agenttest.trace.v1"] = "agenttest.trace.v1"
    redaction: Literal["no-bodies-or-field-values.v1"] = "no-bodies-or-field-values.v1"
    external_upload: Literal[False] = False
    real_model_provenance: Literal["NOT_INFERRED_FROM_REPORT"] = "NOT_INFERRED_FROM_REPORT"
    run_id: str
    source_report_sha256: str
    business_outcome: Outcome
    feedback_state: str | None = None
    usage_status: Literal["UNRECORDED", "UNKNOWN", "NOT_SENT", "OBSERVED"]
    recorded_calls: int | None
    total_tokens: int | None
    spans: list[TraceSpan]


def _code(value: str | None) -> str | None:
    return (value if value is None or re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value)
            else "UNCLASSIFIED")


def _model(stat: ModelCallStats) -> dict:
    return {name: getattr(stat, name) for name in (
        "calls", "model_sha256", "http_status", "elapsed_ms", "request_sha256",
        "input_tokens", "output_tokens", "total_tokens")} | {"reason": _code(stat.reason)}


def build_trace(source: RunReport) -> LocalTrace:
    """Each phase retains its own verdict; an investigation cannot upgrade the business outcome."""
    report = public_report(source)
    spans, model_stats = [], []

    def add(phase, label, state, **extra):
        span_id = fingerprint([report.run_id, phase, len(spans)])[:16]
        span = TraceSpan(span_id=span_id, phase=phase, label=label, state=state, **extra)
        spans.append(span)
        return span_id

    if report.model_call:
        stat = report.model_call
        model_stats.append(stat)
        add("planning", "initial_plan", "OBSERVED", model=_model(stat), elapsed_ms=stat.elapsed_ms,
            reason=_code(stat.reason))
    else:
        add("planning", "initial_plan", "UNKNOWN")
    for name in ("dsl_valid", "policy_authorized", "intent_verified"):
        value = getattr(report, name) if name in report.model_fields_set else None
        add("validation", name, "PASSED" if value is True else
            "NOT_PASSED" if value is False else "UNKNOWN",
            diagnostic=report.diagnostic.model_dump(mode="json") if report.diagnostic else None)
    parents = {}

    def add_step(phase, step):
        requests = [{"attempt_id": attempt.attempt_id, "status_code": attempt.status_code,
            "elapsed_ms": attempt.elapsed_ms, "error_code": _code(attempt.error_code),
            "response_bytes": attempt.response_bytes,
            "response_sha256": attempt.response_sha256} for attempt in step.attempts]
        parent_id = parents.get(getattr(step, "parent_step_id", None))
        parents[step.step_id] = add(phase, step.step_id, step.outcome.value,
            parent_span_id=parent_id, step_id=step.step_id, operation_id=step.operation_id,
            capability_id=getattr(step, "capability_id", None),
            role=getattr(step, "role", None), reason=_code(step.reason), requests=requests,
            elapsed_ms=sum(attempt.elapsed_ms for attempt in step.attempts),
            checks=[{"kind": check.kind, "passed": check.passed,
                     "selector_sha256": fingerprint(check.selector) if check.selector else None}
                    for check in step.checks])

    for step in report.steps:
        add_step("execution", step)
    investigations = iter(report.investigations)
    if report.feedback:
        for decision in report.feedback.decisions:
            stat = decision.model_call
            if stat:
                model_stats.append(stat)
            add("investigation", f"decision_{decision.index}", decision.action.upper(),
                reason=_code(decision.reason), elapsed_ms=decision.elapsed_ms,
                model=_model(stat) if stat else None)
            if decision.action == "investigate" and (step := next(investigations, None)):
                add_step("investigation", step)
    for step in investigations:
        add_step("investigation", step)
    if report.feedback:
        add("conclusion", report.feedback.finding, report.feedback.state,
            evidence_refs=list(report.feedback.evidence_refs), reason=_code(report.feedback.reason))
    else:
        add("conclusion", "business_verdict", report.outcome.value,
            reason=_code(report.reason))
    called = [stat for stat in model_stats if stat.calls]
    status = ("UNRECORDED" if not model_stats else "NOT_SENT" if not called else
              "UNKNOWN" if any(stat.total_tokens is None for stat in called) else "OBSERVED")
    trace = LocalTrace(run_id=report.run_id, source_report_sha256=fingerprint(source.model_dump()),
        business_outcome=report.outcome,
        feedback_state=report.feedback.state if report.feedback else None,
        usage_status=status,
        recorded_calls=sum(stat.calls for stat in model_stats) if model_stats else None,
        total_tokens=sum(stat.total_tokens for stat in called)
            if status in {"OBSERVED", "NOT_SENT"} else None, spans=spans)
    _public_metadata(trace.model_dump(mode="json"))
    return trace


def export_trace(report: RunReport, output: Path) -> LocalTrace:
    trace = build_trace(report)
    raw = (json.dumps(trace.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n").encode()
    if len(raw) > 2_000_000:
        raise ValueError("TRACE_SIZE_LIMIT")
    path = output.absolute()
    with pinned_directory(path.parent, ".", create=True) as directory:
        with os.fdopen(directory.open(path.name, create=True), "wb") as stream:
            stream.write(raw)
    return trace
