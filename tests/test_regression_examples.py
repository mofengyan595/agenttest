"""Published exported assets preserve all three ticket fault mechanisms without a model."""

import pytest

from agenttest.demo import PROJECT_ROOT
from agenttest.regression import RegressionRuntime, assert_regression_passed, run_regression
from agenttest.report import Outcome, ReportStore
from agenttest.ticket_fault_check import CASE_IDS, case_inputs


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", CASE_IDS)
async def test_exported_asset_fails_at_the_original_business_check(case_id, tmp_path):
    document, target, registry, environ, transport, task, _, item = case_inputs(case_id, True)
    runtime = RegressionRuntime(document, target, registry, task, environ, transport,
                                ReportStore(tmp_path / "reports"))
    report = await run_regression(PROJECT_ROOT / "examples/regressions" / case_id.lower(), runtime)
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.task_mode == "operator_plan" and report.task_satisfied is None
    assert report.model_call is None and report.policy_registry_sha256 == registry.digest()
    assert any([s.operation_id, c.kind, c.selector] == item["failed_check"] and not c.passed
               for s in report.steps for c in s.checks)
    with pytest.raises(AssertionError, match="REGRESSION_ASSERTION_FAILED"):
        assert_regression_passed(report)
