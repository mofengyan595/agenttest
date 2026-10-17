"""Only reviewed policies may produce PASS or ASSERTION_FAILED."""

from typing import Any

from agenttest.credentials import sensitive_field
from agenttest.dsl import AssertionPolicy
from agenttest.json_values import json_equal
from agenttest.report import CheckResult, Outcome, safe_value
from agenttest.selectors import select


def verify(
    policy: AssertionPolicy, status: int, body: Any,
    sensitive_fields: list[str] = (),
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
            passed = json_equal(actual, assertion.expected)
        elif found and assertion.operator == "in":
            passed = any(json_equal(actual, item) for item in assertion.expected)
        sensitive = sensitive_field(assertion.select, sensitive_fields)
        public_numeric = assertion.select in policy.public_evidence
        checks.append(CheckResult(
            kind=assertion.operator, selector=assertion.select, passed=passed,
            expected="[REDACTED]" if sensitive else safe_value(assertion.expected, public_numeric),
            actual="[REDACTED]" if sensitive else safe_value(actual, public_numeric),
        ))
    outcome = Outcome.PASS if all(check.passed for check in checks) else Outcome.ASSERTION_FAILED
    return outcome, checks
