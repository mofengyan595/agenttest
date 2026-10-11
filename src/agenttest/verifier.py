"""Only reviewed policies may produce PASS or ASSERTION_FAILED."""

import re
from typing import Any

from agenttest.dsl import AssertionPolicy
from agenttest.report import CheckResult, Outcome, safe_value
from agenttest.selectors import select


def verify(
    policy: AssertionPolicy, status: int, body: Any,
) -> tuple[Outcome, list[CheckResult]]:
    if (not policy.reviewed or not policy.source.strip()
            or (policy.status is None and not policy.fields)):
        return Outcome.NEEDS_REVIEW, []
    checks = []
    if policy.status:
        checks.append(CheckResult(
            kind="http_status", passed=status in policy.status.codes,
            expected=policy.status.codes, actual=status,
        ))
    for assertion in policy.fields:
        try:
            actual = select(body, assertion.select)
            found = True
        except (KeyError, IndexError, TypeError):
            actual, found = None, False
        passed = False
        if assertion.operator == "exists":
            passed = found
        elif found and assertion.operator == "count":
            actual = len(actual) if isinstance(actual, (list, dict)) else None
            passed = actual is not None and actual == assertion.expected
        elif found and assertion.operator == "equals":
            passed = type(actual) is type(assertion.expected) and actual == assertion.expected
        elif found and assertion.operator == "in":
            passed = any(
                type(actual) is type(item) and actual == item for item in assertion.expected
            )
        sensitive = bool(re.search(
            r"password|passwd|token|secret|authorization|cookie|api.?key", assertion.select,
            re.IGNORECASE,
        ))
        checks.append(CheckResult(
            kind=assertion.operator, selector=assertion.select, passed=passed,
            expected="[REDACTED]" if sensitive else safe_value(assertion.expected),
            actual="[REDACTED]" if sensitive else safe_value(actual),
        ))
    outcome = Outcome.PASS if all(check.passed for check in checks) else Outcome.ASSERTION_FAILED
    return outcome, checks
