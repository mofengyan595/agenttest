"""The delivery entry reports expected fault detection as a verified demo."""

import json

import pytest

from agenttest.report import ReportStore
from agenttest.showcase import showcase


@pytest.mark.asyncio
@pytest.mark.parametrize("case,outcome,requests", [("normal", "PASS", 9),
                                                  ("fault", "ASSERTION_FAILED", 4)])
async def test_base_demonstration(tmp_path, case, outcome, requests):
    result = await showcase(case, ReportStore(tmp_path))
    assert result["verified"] and result["school_api_calls"] == result["real_java_requests"] == 0
    row = result["cases"][0]
    assert row["outcome"] == outcome and row["requests"] == requests
    assert row["course_creates"] == 1
    assert ReportStore(tmp_path).read(row["run_id"]).outcome.value == outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["investigation", "java-mock"])
async def test_agent_demonstration(tmp_path, case):
    pytest.importorskip("langgraph")
    result = await showcase(case, ReportStore(tmp_path))
    assert result["verified"]
    row = result["cases"][0]
    assert row["course_creates"] == 1
    if case == "investigation":
        assert row["outcome"] == "ASSERTION_FAILED" and row["investigations"] == 1
    else:
        assert row["requests"] == 3 and row["checks"] == 14
        assert "synthetic-mock-only" not in json.dumps(result)


@pytest.mark.asyncio
async def test_mcp_demo_preserves_readable_report_and_rejects_java(tmp_path):
    pytest.importorskip("mcp")
    result = await showcase("mcp", ReportStore(tmp_path))
    assert result["verified"] and result["school_api_calls"] == 0
    row = result["cases"][0]
    assert row["cached_without_replay"] and row["requests"] == 9
    files = list(tmp_path.glob("mcp/**/report.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_bytes())["run_id"] == row["run_id"]


def test_unknown_demo_does_not_run(tmp_path):
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(showcase("unknown", ReportStore(tmp_path)))
    assert not list(tmp_path.iterdir())
