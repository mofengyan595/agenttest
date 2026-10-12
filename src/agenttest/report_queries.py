"""Bounded, flat report catalog for the local read-only console."""

import os
import stat
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from agenttest.credentials import sensitive_field
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, ReportStore, RunReport, fingerprint, safe_value

MAX_CATALOG_ENTRIES = 2000
MAX_CATALOG_BYTES = 32_000_000


class CatalogLimit(ValueError):
    pass


class RunSummary(BaseModel):
    run_id: str
    started_at: str | None
    environment: str
    outcome: Outcome | None
    steps: int | None
    workflow_completed: bool | None


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    offset: int
    limit: int
    skipped: int


def public_report(report: RunReport, registry: PolicyRegistry | None = None) -> RunReport:
    """Only an independently loaded operator policy can approve stored numeric fields.

    A digest/flag in an old report alone is not approval. Instantiated policies that
    cannot be matched to the supplied snapshot stay redacted; no approval is inferred.
    """
    policies = {fingerprint(p.model_dump()): p for p in registry.definitions()} if registry else {}
    result = report.model_copy(deep=True)
    for step in [*result.steps, *result.investigations]:
        policy = policies.get(step.policy_sha256)
        for check in step.checks:
            codes = check.expected
            if (check.kind == "http_status" and check.selector is None
                    and isinstance(codes, list) and 0 < len(codes) <= 100
                    and all(type(code) is int and 100 <= code <= 599 for code in codes)):
                check.expected = list(codes)
                check.actual = (check.actual if type(check.actual) is int
                                and 100 <= check.actual <= 599 else "[REDACTED]")
            else:
                approved = bool(
                    policy and policy.operation_id == step.operation_id
                    and policy.assertions.reviewed and policy.assertions.source.strip()
                    and check.selector in policy.assertions.public_evidence
                    and not sensitive_field(check.selector or "")
                    and any(a.select == check.selector and a.operator == check.kind
                            and type(a.expected) is type(codes) and a.expected == codes
                            for a in policy.assertions.fields)
                )
                sensitive = sensitive_field(check.selector or "")
                check.expected = "[REDACTED]" if sensitive else safe_value(codes, approved)
                check.actual = "[REDACTED]" if sensitive else safe_value(check.actual, approved)
    return result


def list_runs(store: ReportStore, *, limit: int, offset: int, outcome: Outcome | None,
              order: Literal["asc", "desc"]) -> RunPage:
    """Scan one operator-owned namespace with hard entry and aggregate-byte budgets.

    Invalid/incomplete files are skipped. A budget overflow fails explicitly; no
    partial-page count is presented as a global count. Nested namespaces are not scanned.
    """
    rows = []
    skipped = used = 0
    if not store.root.exists():
        return RunPage(items=[], total=0, offset=offset, limit=limit, skipped=0)
    with os.scandir(store.root) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_CATALOG_ENTRIES:
                raise CatalogLimit("Catalog entry budget exceeded")
            if len(entry.name) != 32 or any(c not in "0123456789abcdef" for c in entry.name):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise ValueError("Linked or non-directory report")
                used += (store.root / entry.name / "report.json").lstat().st_size
                if used > MAX_CATALOG_BYTES:
                    raise CatalogLimit("Catalog byte budget exceeded")
                report = store.read(entry.name)
                recorded_time = (report.started_at
                                 if "started_at" in report.model_fields_set else None)
                timestamp = (datetime.fromisoformat(recorded_time) if recorded_time
                             else datetime.min.replace(tzinfo=timezone.utc))
                if timestamp.tzinfo is None:
                    raise ValueError("Timestamp requires timezone")
            except CatalogLimit:
                raise
            except (ValueError, OSError):
                skipped += 1
                continue
            recorded_outcome = report.outcome if "outcome" in report.model_fields_set else None
            if outcome is None or recorded_outcome == outcome:
                rows.append((timestamp, RunSummary(
                    run_id=report.run_id, started_at=recorded_time,
                    environment=report.environment, outcome=recorded_outcome,
                    steps=len(report.steps) if "steps" in report.model_fields_set else None,
                    workflow_completed=report.workflow_completed,
                )))
    rows.sort(key=lambda row: (row[0], row[1].run_id), reverse=order == "desc")
    return RunPage(items=[row[1] for row in rows[offset:offset + limit]], total=len(rows),
                   offset=offset, limit=limit, skipped=skipped)
