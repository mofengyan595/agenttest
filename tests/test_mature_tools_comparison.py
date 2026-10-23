"""The independent optional generator cannot bypass executor budgets or authority."""

import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/compare_contract_tools.py"
spec = importlib.util.spec_from_file_location("contract_comparison", MODULE)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


@pytest.mark.asyncio
async def test_common_subset_same_budget_and_business_faults(tmp_path):
    pytest.importorskip("langgraph")
    result = await comparison.compare(tmp_path, include_schemathesis=False)
    assert len(result["rows"]) == 6
    for row in result["rows"]:
        assert row["http_requests"] == row["request_budget"] == 9
        assert row["business_checks_passed"] == (9 if row["fault"] == "none" else 1)
        assert row["schema_checks_passed"] == (1 if row["fault"] == "accept_invalid" else 9)
        assert row["model_calls"] == (9 if row["arm"] == "agenttest_mock" else 0)
    assert result["real_model_calls"] == 0
    json.dumps(result)


def test_optional_schemathesis_generation_has_no_execution(monkeypatch):
    pytest.importorskip("schemathesis")
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("Generation must not dispatch HTTP")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    p = comparison.load_goal_project(comparison.PROJECT)
    bodies = comparison.generated_data(p.document)
    assert len(bodies["positive"]) == len(bodies["negative"]) == 9
    json.dumps(comparison.data_evidence(bodies))
