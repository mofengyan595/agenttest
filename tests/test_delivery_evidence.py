"""Frozen evidence must retain planning failures and honest metric denominators."""

import json

from agenttest.demo import PROJECT_ROOT


def test_real_java_and_ai_evidence_are_distinguished_from_mock():
    result = json.loads((PROJECT_ROOT / "docs/stage4a-results.json").read_bytes())
    assert result["execution"] == "real school API and real isolated Java HTTP"
    actual = result["result"]
    assert actual["http_statuses"] == [200, 201, 200] and actual["checks"] == 14
    assert actual["create_requests"] == 1 and actual["model_call"]["total_tokens"] == 1794


def test_new_evaluation_preserves_failure_and_unexecuted_fault():
    data = json.loads((PROJECT_ROOT / "docs/stage4b-results.json").read_bytes())
    real = data["baselines"]["real"]
    assert real["plan_valid_rate"] == real["end_to_end_success_rate"] == .5
    assert real["not_executed_nl_faults"] == 1
    b02 = next(t for t in real["tasks"] if t["task_id"] == "B02")
    assert b02["diagnostic"]["code"] == "DSL_AUTHENTICATION_ORDER" and b02["requests"] == 0
    assert real["generation_calls"] == 4 and real["total_tokens"] == 5496
    assert data["baselines"]["manual"]["planning_detection"]["detected"] == 4
