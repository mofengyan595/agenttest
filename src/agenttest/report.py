"""Typed, allowlisted evidence: never persist request/response bodies or headers."""

import hashlib
import json
import os
import stat
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from agenttest.diagnostics import Diagnostic

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
    checks: list[CheckResult] = Field(default_factory=list)
    attempts: list[AttemptEvidence] = Field(default_factory=list)


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


class PlanStepEvidence(BaseModel):
    step_id: str
    operation_id: str
    role: str | None
    policy_id: str
    argument_sha256: dict[str, str] = Field(default_factory=dict)
    dependencies: dict[str, str] = Field(default_factory=dict)


class RunReport(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scenario_sha256: str
    contract_sha256: str
    outcome: Outcome = Outcome.NEEDS_REVIEW
    steps: list[StepResult] = Field(default_factory=list)
    completed: bool = False
    reason: str = ""
    environment: str
    target_origin: str
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
    cleanup: str = "In-memory demo state discarded; external target cleanup is operator-owned"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def safe_value(value: Any, public_numeric: bool = False) -> Any:
    """Only explicitly approved numeric evidence may leave the verifier."""
    if value is None or type(value) is bool or (public_numeric and type(value) in {int, float}):
        return value
    return "[REDACTED]"


class ReportStore:
    def __init__(self, root: Path = Path("runs")) -> None:
        self.root = root.resolve()

    def save(self, report: RunReport) -> Path:
        UUID(report.run_id)
        directory = self.root / report.run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "report.json"
        temporary = directory / "report.tmp"
        temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                # Windows readers can briefly deny replacement. Retry file publication only;
                # no HTTP request or scenario is replayed, and permanent denial remains an error.
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                    raise
                time.sleep(.005 * 2 ** attempt)
        return path

    def read(self, run_id: str) -> RunReport:
        if UUID(run_id).hex != run_id:
            raise ValueError("Invalid run id")
        # Refuse links/reparse points and bound reads, including files that grow after stat.
        directory = self.root / run_id
        path = directory / "report.json"
        for candidate in (directory, path):
            info = candidate.lstat()
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise ValueError("Linked reports are forbidden")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Report must be a regular file")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Run path escapes report directory")
        # POSIX directory-relative opens also close the directory replacement race.
        if os.name == "posix":
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            root_fd = os.open(self.root, flags | os.O_DIRECTORY)
            try:
                directory_fd = os.open(run_id, flags | os.O_DIRECTORY, dir_fd=root_fd)
                try:
                    descriptor = os.open("report.json", flags, dir_fd=directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                os.close(root_fd)
        else:
            descriptor = os.open(path, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_REPORT_BYTES:
                raise ValueError("Invalid report size or file type")
            # Verify that the opened file is the inspected file on Windows as well.
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Report changed during read")
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
