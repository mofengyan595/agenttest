from pathlib import Path

import pytest

from agenttest.dsl import AssertionPolicy
from agenttest.report import Outcome, ReportStore, RunReport
from agenttest.verifier import verify


def policy(**overrides) -> AssertionPolicy:
    return AssertionPolicy.model_validate({
        "reviewed": True, "source": "human policy v1", "status": {"codes": [200]}, **overrides,
    })


@pytest.mark.parametrize("kwargs", [
    {"reviewed": False}, {"source": ""}, {"status": None},
])
def test_unknown_expectations_need_review(kwargs: dict) -> None:
    assert verify(policy(**kwargs), 200, {}) == (Outcome.NEEDS_REVIEW, [])


def test_reviewed_status_and_fields() -> None:
    assertions = policy(fields=[
        {"select": "$.status", "expected": "SUCCEEDED"},
        {"select": "$.items", "operator": "count", "expected": 2},
        {"select": "$.id", "operator": "exists"},
    ])
    outcome, checks = verify(assertions, 200, {"status": "SUCCEEDED", "items": [1, 2], "id": 1})
    assert outcome == Outcome.PASS and len(checks) == 4
    assert verify(assertions, 200, {"status": "FAILED", "items": [], "id": 1})[0] == (
        Outcome.ASSERTION_FAILED
    )
    assert verify(assertions, 200, {})[0] == Outcome.ASSERTION_FAILED
    assert verify(policy(), 503, {})[0] == Outcome.ASSERTION_FAILED


def test_assertions_do_not_coerce_or_mutate() -> None:
    assertions = policy(fields=[{"select": "$.count", "expected": 1}])
    before = assertions.model_dump_json()
    assert verify(assertions, 200, {"count": True})[0] == Outcome.ASSERTION_FAILED
    assert assertions.model_dump_json() == before


def test_report_persistence_and_path_traversal(tmp_path: Path) -> None:
    report = RunReport(scenario_sha256="a", contract_sha256="b", environment="isolated",
                       target_origin="http://127.0.0.1")
    store = ReportStore(tmp_path)
    assert store.save(report).is_file()
    assert store.read(report.run_id) == report
    with pytest.raises(ValueError):
        store.read("../../.env")


def test_secret_and_personal_data_not_in_evidence() -> None:
    assertions = policy(fields=[
        {"select": "$.access_token", "expected": "secret-token"},
        {"select": "$.user", "expected": {"email": "private@example.test"}},
    ])
    _, checks = verify(assertions, 200, {
        "access_token": "secret-token", "user": {"email": "private@example.test"},
    })
    evidence = str([check.model_dump() for check in checks])
    assert "secret-token" not in evidence
    assert "private@example.test" not in evidence
