"""Typed, allowlisted evidence: never persist request/response bodies or headers."""

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


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
    cleanup: str = "In-memory demo state discarded; external target cleanup is operator-owned"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def safe_value(value: Any) -> Any:
    """Keep numeric/boolean evidence; string values and user data stay in memory only."""
    if value is None or type(value) in {bool, int, float}:
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
        temporary.replace(path)
        return path

    def read(self, run_id: str) -> RunReport:
        if UUID(run_id).hex != run_id:
            raise ValueError("Invalid run id")
        path = self.root / run_id / "report.json"
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Run path escapes report directory")
        return RunReport.model_validate_json(path.read_bytes())


def aggregate(steps: list[StepResult]) -> Outcome:
    for outcome in (
        Outcome.TOOL_ERROR, Outcome.TEST_INFRA_ERROR, Outcome.ASSERTION_FAILED,
        Outcome.NEEDS_REVIEW,
    ):
        if any(step.outcome == outcome for step in steps):
            return outcome
    return Outcome.PASS if steps else Outcome.NEEDS_REVIEW
