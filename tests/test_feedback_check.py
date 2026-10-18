"""The feedback protocol grades business findings separately and rejects archive drift."""

import json
from uuid import uuid4

import pytest

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.feedback_check import check_feedback, verify_feedback
from agenttest.llm_planner import ModelSettings


@pytest.mark.asyncio
async def test_live_feedback_requires_clean_source_before_any_model_call(monkeypatch):
    pytest.importorskip("langgraph")
    import agenttest.feedback_check as protocol

    monkeypatch.setattr(protocol, "checkout_identity", lambda root: {"worktree_status": " M dirty"})
    output = PROJECT_ROOT / "runs" / ("pytest-feedback-dirty-" + uuid4().hex)
    with pytest.raises(ValueError, match="clean committed"):
        await check_feedback(output, ModelSettings(model="ecnu-plus", protocol="chatecnu",
            base_url="https://chat.ecnu.edu.cn/open/api/v1", api_key="synthetic-feedback-only"))


@pytest.mark.asyncio
async def test_offline_feedback_controls_trials_and_cold_regrading():
    pytest.importorskip("langgraph")
    output = PROJECT_ROOT / "runs" / ("pytest-feedback-" + uuid4().hex)
    result = await check_feedback(output)
    assert result["controls_passed"] == 3
    assert result["business_correct"] == result["finding_correct"] == 3
    assert result["real_model_calls"] == 0 and result["model_calls"] <= 12
    assert result["usage_status"] == "SIMULATED" and result["not_attempted"] == 0
    assert verify_feedback(output)["rescored_runs"] == 6
    results = read_json(output / "results.json")
    results["results"][-1]["finding_correct"] = False
    # Direct artifact modification remains visible even if the manifest is resealed.
    (output / "results.json").write_text(json.dumps(results), encoding="utf-8")
    manifest = read_json(output / "archive.json")
    manifest["files"] = inventory(output)
    (output / "archive.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="regrading"):
        verify_feedback(output)
