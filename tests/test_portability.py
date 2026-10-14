import json
from uuid import uuid4

import pytest

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import seal
from agenttest.portability_demo import run_portability, verify_portability


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fake", "mock_transport"])
async def test_cross_project_success_faults_and_zero_request_rejections(source, tmp_path):
    pytest.importorskip("agenttest.workflow")
    output = PROJECT_ROOT / "runs" / ("pytest-portability-" + uuid4().hex) / source
    result = await run_portability(output, (source,))
    assert result["verified"] and result["cases"] == 15
    assert result["projects"] == ["courses", "tickets"]
    assert (result["normal_cases"], result["fault_cases"],
            result["preflight_rejections"]) == (6, 5, 4)
    assert result["real_model_calls"] == result["human_reviewed"] == 0
    assert result["formal_eval_v2_score"] is None
    assert all(r["grade"]["requests"] == 0 for r in result["results"]
               if r["expected"] == "NEEDS_REVIEW")
    assert sum(r["model_calls"] for r in result["results"]) == (
        15 if source == "mock_transport" else 0)
    assert verify_portability(output)["rescored_cases"] == 15
    row = result["results"][0]
    row["grade"]["requests"] += 1
    (output / (source + "-" + row["case_id"] + ".json")).write_text(
        json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="archive changed"):
        verify_portability(output)
    (output / "results.json").write_text(json.dumps(result), encoding="utf-8")
    (output / "archive.json").unlink()
    seal(output)
    with pytest.raises(ValueError, match="regrading mismatch"):
        verify_portability(output)


@pytest.mark.asyncio
async def test_offline_runner_rejects_real_mode_and_unsafe_output(tmp_path):
    pytest.importorskip("agenttest.workflow")
    with pytest.raises(ValueError, match="offline only"):
        await run_portability(tmp_path / "real", ("real",))
    with pytest.raises(ValueError, match="under repository runs"):
        await run_portability(PROJECT_ROOT / "examples/never-write-here", ("fake",))
