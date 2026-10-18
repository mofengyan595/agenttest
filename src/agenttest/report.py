"""Typed, allowlisted evidence: never persist request/response bodies or headers."""

import hashlib
import json
import math
import os
import stat
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_serializer

from agenttest.diagnostics import Diagnostic
from agenttest.report_files import pinned_directory, writer_lock

MAX_REPORT_BYTES = 2_000_000


class Outcome(str, Enum):
    PASS = "PASS"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    TEST_INFRA_ERROR = "TEST_INFRA_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class CheckResult(BaseModel):
    kind: str
    selector: str | None = None
    passed: bool
    expected: Any = None
    actual: Any = None


class AttemptEvidence(BaseModel):
    attempt: int
    attempt_id: str = Field(default_factory=lambda: uuid4().hex)
    status_code: int | None = None
    elapsed_ms: float
    response_bytes: int = 0
    response_sha256: str | None = None
    error_code: str | None = None


class StepResult(BaseModel):
    step_id: str
    step_run_id: str = Field(default_factory=lambda: uuid4().hex)
    operation_id: str
    method: str
    path_template: str
    outcome: Outcome
    reason: str
    policy_sha256: str
    policy_template_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    checks: list[CheckResult] = Field(default_factory=list)
    attempts: list[AttemptEvidence] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_template_identity(self, handler):
        data = handler(self)
        if self.policy_template_sha256 is None:
            data.pop("policy_template_sha256", None)
        return data


class InvestigationResult(StepResult):
    parent_step_id: str
    capability_id: str | None = None
    role: str | None = None
    source_step_id: str | None = None
    resource_ref_sha256: str | None = None
    requests_used: int = 0
    rounds_used: int = 0
    model_calls_used: int = 0
    elapsed_ms: float = 0


class ModelCallStats(BaseModel):
    diagnostic: Diagnostic | None = None
    calls: int = 0
    model_name: str | None = None
    model_sha256: str | None = None
    http_status: int | None = None
    elapsed_ms: float = 0
    request_bytes: int = 0
    request_sha256: str | None = None
    response_bytes: int = 0
    input_token_upper_bound: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    output_sha256: str | None = None
    reason: str | None = None


class FeedbackDecisionEvidence(BaseModel):
    index: int = Field(ge=0, le=2)
    action: Literal["pending", "investigate", "finish", "rejected"] = "pending"
    reason: str = Field(default="FEEDBACK_PENDING", pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    model_call: ModelCallStats | None = None
    elapsed_ms: float = 0


class FeedbackResult(BaseModel):
    state: Literal["PENDING", "SUPPORTED", "NEEDS_REVIEW", "BUDGET_EXHAUSTED",
                   "TOOL_ERROR", "TEST_INFRA_ERROR", "TIMEOUT", "CANCELLED"] = "PENDING"
    reason: str = Field(default="FEEDBACK_PENDING", pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    finding: Literal["ACCESS_BOUNDARY_VIOLATION", "PERSISTENT_READBACK_MISMATCH",
                     "EVIDENCE_INSUFFICIENT"] = "EVIDENCE_INSUFFICIENT"
    basis: Literal["证据不足，需人工复核。", "受限角色读取成功，同资源的所有者读取通过。",
                   "同角色对同一资源的复核重复出现字段断言失败。"] = "证据不足，需人工复核。"
    evidence_refs: list[str] = Field(default_factory=list, max_length=3)
    decisions: list[FeedbackDecisionEvidence] = Field(default_factory=list, max_length=3)
    requests_used: int = Field(default=0, ge=0, le=2)
    model_calls_used: int = Field(default=0, ge=0, le=3)
    elapsed_ms: float = 0


class PlanStepEvidence(BaseModel):
    step_id: str
    operation_id: str
    role: str | None
    policy_id: str
    argument_sha256: dict[str, str] = Field(default_factory=dict)
    dependencies: dict[str, str] = Field(default_factory=dict)


class RunReport(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    revision: int = Field(default=0, ge=0, strict=True)
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scenario_sha256: str
    contract_sha256: str
    outcome: Outcome = Outcome.NEEDS_REVIEW
    steps: list[StepResult] = Field(default_factory=list)
    completed: bool = False
    reason: str = ""
    environment: str
    target_origin: str
    target_config_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    policy_registry_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    workflow_nodes: list[str] = Field(default_factory=list)
    planner_calls: int = 0
    tool_calls: int = 0
    analysis_code: str | None = None
    workflow_completed: bool | None = None
    user_task_sha256: str | None = None
    plan_valid: bool = False
    dsl_valid: bool = False
    policy_authorized: bool = False
    selected_policy_ids: list[str] = Field(default_factory=list)
    model_call: ModelCallStats | None = None
    plan_structure: list[PlanStepEvidence] = Field(default_factory=list)
    validation_issue: str | None = None
    diagnostic: Diagnostic | None = None
    intent_verified: bool = False
    # None preserves unknown semantics in legacy reports; manual PASS is not an NL claim.
    task_mode: Literal["natural_language", "operator_plan"] | None = None
    task_satisfied: bool | None = None
    investigations: list[InvestigationResult] = Field(default_factory=list)
    investigation_error: str | None = None
    feedback: FeedbackResult | None = None
    cleanup: str = "In-memory demo state discarded; external target cleanup is operator-owned"

    @model_serializer(mode="wrap")
    def serialize_execution_identity(self, handler):
        data = handler(self)
        for name in ("target_config_sha256", "policy_registry_sha256"):
            if getattr(self, name) is None:
                data.pop(name, None)
        if self.feedback is None:
            data.pop("feedback", None)
        return data


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def safe_value(value: Any, public_numeric: bool = False) -> Any:
    """Only explicitly approved numeric evidence may leave the verifier."""
    if value is None or type(value) is bool or (public_numeric and (
        type(value) is int or type(value) is float and math.isfinite(value)
    )):
        return value
    return "[REDACTED]"


class ReportStore:
    def __init__(self, root: Path = Path("runs")) -> None:
        # Do not resolve away evidence of pre-planted links.
        self.root = Path(os.path.abspath(root))

    @staticmethod
    def validate_id(run_id: str) -> None:
        if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("Invalid run id")

    def save(self, report: RunReport) -> Path:
        """Single publisher per ID, compare-and-swap revision; never replay execution.

        Independent callers must read the latest report before updating. Conflicts
        fail explicitly. Legacy reports without a revision start at version zero.
        """
        self.validate_id(report.run_id)
        with pinned_directory(self.root, report.run_id, create=True) as directory:
            with writer_lock(directory):
                try:
                    current = self._read(directory, report.run_id)
                except FileNotFoundError:
                    current = None
                expected = current.revision if current else 0
                if report.revision != expected:
                    raise ValueError("Report revision conflict")
                snapshot = report.model_copy(deep=True, update={"revision": expected + 1})
                raw = snapshot.model_dump_json(indent=2).encode("utf-8")
                if len(raw) > MAX_REPORT_BYTES:
                    raise ValueError("Report exceeds size limit")
                temporary = f"report-{uuid4().hex}.tmp"
                descriptor = directory.open(temporary, create=True)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    for attempt in range(5):
                        try:
                            directory.replace(temporary, "report.json")
                            break
                        except PermissionError as exc:
                            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                                raise
                            time.sleep(.005 * 2 ** attempt)
                    report.revision = snapshot.revision
                finally:
                    try:
                        directory.unlink(temporary)
                    except FileNotFoundError:
                        pass
        return self.root / report.run_id / "report.json"

    def read(self, run_id: str) -> RunReport:
        self.validate_id(run_id)
        with pinned_directory(self.root, run_id, create=False) as directory:
            return self._read(directory, run_id)

    @staticmethod
    def _read(directory, run_id: str) -> RunReport:
        directory.inspect("report.json")
        descriptor = directory.open("report.json", readonly=True)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_REPORT_BYTES:
                raise ValueError("Invalid report size or file type")
            raw = stream.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("Report exceeds size limit")
        report = RunReport.model_validate_json(raw)
        if report.run_id != run_id:
            raise ValueError("Report identity mismatch")
        return report


def aggregate(steps: list[StepResult]) -> Outcome:
    for outcome in (
        Outcome.TOOL_ERROR, Outcome.TEST_INFRA_ERROR, Outcome.ASSERTION_FAILED,
        Outcome.NEEDS_REVIEW,
    ):
        if any(step.outcome == outcome for step in steps):
            return outcome
    return Outcome.PASS if steps else Outcome.NEEDS_REVIEW
