"""Bounded retry advice for explicit gateway rejections."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

MAX_RETRY_DELAY = 8.0
MAX_GATEWAY_RETRIES = 2
TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})


def retry_after_seconds(header: str | None) -> float | None:
    if not header:
        return None
    header = header.strip()
    if header.isascii() and header.isdecimal():
        return float(int(header)) if len(header) <= 8 else MAX_RETRY_DELAY + 1
    try:
        instant = parsedate_to_datetime(header)
        if instant.tzinfo is None:
            return None
        return max(0.0, (instant - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def retry_delay(attempt: int, retry_after: float | None) -> float | None:
    """Return no retry if the provider asks us to wait outside this run's retry window."""
    if attempt >= MAX_GATEWAY_RETRIES or (
        retry_after is not None and retry_after > MAX_RETRY_DELAY
    ):
        return None
    return max(float(2 ** attempt), retry_after or 0)
