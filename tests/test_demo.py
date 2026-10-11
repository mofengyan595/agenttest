from pathlib import Path

import pytest

from agenttest.demo import run_demo
from agenttest.report import Outcome, ReportStore


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,failed_step", [
    ("none", None), ("ownership", "cross_user"), ("terminal", "poll_batch"),
])
async def test_full_chain_and_ground_truth(
    tmp_path: Path, fault: str, failed_step: str | None,
) -> None:
    report = await run_demo(fault, ReportStore(tmp_path))
    assert report.completed
    if failed_step:
        assert report.outcome == Outcome.ASSERTION_FAILED
        assert report.steps[-1].step_id == failed_step
        assert any(not check.passed for check in report.steps[-1].checks)
    else:
        assert report.outcome == Outcome.PASS, report.model_dump_json(indent=2)
        assert len(report.steps) == 8
        assert len(report.steps[6].attempts) == 2
        assert report.steps[-1].checks[-1].actual == 2
    assert ReportStore(tmp_path).read(report.run_id) == report


@pytest.mark.asyncio
async def test_repeat_runs_have_isolated_state(tmp_path: Path) -> None:
    first = await run_demo(store=ReportStore(tmp_path))
    second = await run_demo(store=ReportStore(tmp_path))
    assert first.run_id != second.run_id
    assert first.outcome == second.outcome == Outcome.PASS
    assert first.steps[2].checks[-1].actual == second.steps[2].checks[-1].actual == 1
