"""Local model transport observations, with no request or response content."""

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

DeadlineSource = Literal["REQUEST_DEADLINE", "FOCUS_DEADLINE", "DESIGN_DEADLINE", "TASK_DEADLINE"]
Interruption = Literal[DeadlineSource, "HTTPX_CONNECT", "HTTPX_READ", "HTTPX_WRITE",
                       "HTTPX_POOL", "USER_CANCELLED", "SERVICE_SHUTDOWN", "UNKNOWN"]
Stage = Literal["request_prepared", "dispatch_started", "headers_received", "first_body_chunk",
                "body_complete", "json_parsed", "usage_validated", "call_ended"]
_deadlines = ContextVar[tuple[tuple[asyncio.Timeout, DeadlineSource], ...]](
    "model_deadlines", default=())


class ModelCancellation(Enum):
    USER = "USER_CANCELLED"
    SHUTDOWN = "SERVICE_SHUTDOWN"


class ModelCallTelemetry(BaseModel):
    """Elapsed milliseconds since this local call started, never provider timestamps."""

    stages_ms: dict[Stage, float] = Field(default_factory=dict)
    interruption_source: Interruption | None = None
    usage_evidence: Literal["VALIDATED", "INCOMPLETE_RESPONSE", "RESPONSE_PARSE_FAILED",
                            "USAGE_MISSING", "USAGE_INVALID", "USAGE_TOTAL_MISMATCH",
                            "UNKNOWN"] | None = None

    def mark(self, stage: Stage, *, started: float) -> None:
        if stage not in self.stages_ms:
            self.stages_ms[stage] = round((time.monotonic() - started) * 1000, 3)

    def observe_usage(self, data: object, *, started: float) -> None:
        if not isinstance(data, dict):
            return
        usage = data.get("usage")
        if usage is None:
            self.usage_evidence = "USAGE_MISSING"
            return
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
        counts = tuple(usage.get(key) for key in keys) if isinstance(usage, dict) else ()
        if not counts or any(type(n) is not int or n < 0 for n in counts):
            self.usage_evidence = "USAGE_INVALID"
        elif counts[0] + counts[1] != counts[2]:
            self.usage_evidence = "USAGE_TOTAL_MISMATCH"
        else:
            self.mark("usage_validated", started=started)
            self.usage_evidence = "VALIDATED"

    def transport_timeout(self, kind: str) -> None:
        if self.interruption_source is None:
            self.interruption_source = {
                "CONNECT": "HTTPX_CONNECT", "READ": "HTTPX_READ",
                "WRITE": "HTTPX_WRITE", "POOL": "HTTPX_POOL",
            }.get(kind, "UNKNOWN")

    def cancelled(self, exc: asyncio.CancelledError) -> None:
        if len(exc.args) == 1 and isinstance(exc.args[0], ModelCancellation):
            self.interruption_source = exc.args[0].value
        else:
            self.interruption_source = expired_deadline()


def expired_deadline() -> Interruption:
    expired = [source for timeout, source in _deadlines.get() if timeout.expired()]
    return expired[0] if len(expired) == 1 else "UNKNOWN"


@asynccontextmanager
async def model_deadline(seconds: float, source: DeadlineSource, *,
                         telemetry: ModelCallTelemetry | None = None):
    """Label existing asyncio deadlines while preserving their cancellation semantics."""
    timeout = asyncio.timeout(seconds)
    token = _deadlines.set((*_deadlines.get(), (timeout, source)))
    try:
        async with timeout:
            yield
    except TimeoutError:
        if telemetry is not None and timeout.expired():
            telemetry.interruption_source = expired_deadline()
        raise
    finally:
        _deadlines.reset(token)
