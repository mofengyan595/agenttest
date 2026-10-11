"""Different fault mechanisms with fresh normal controls, never live API calls."""

import json

import pytest

from agenttest.defect_evaluation import TASKS, detected, detection_metrics, evaluate
from agenttest.demo import run_demo
from agenttest.evaluation import manual_plan
from agenttest.intent import interpret_task
from agenttest.report import Outcome, ReportStore


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["response_name", "create_status", "ownership",
                                    "terminal", "result_count"])
async def test_matched_executor_fault_and_control(tmp_path, fault):
    normal = await run_demo("none", ReportStore(tmp_path))
    broken = await run_demo(fault, ReportStore(tmp_path))
    assert normal.outcome == Outcome.PASS
    assert detected(fault, broken)
    assert sum(s.operation_id == "createCourse" for s in broken.steps) == 1


def test_new_tasks_use_supported_intents_and_do_not_edit_old_suite():
    data = json.loads(TASKS.read_bytes())
    for case in data["cases"][:2]:
        intent = interpret_task(case["user_task"])
        assert intent.creator == case["creator"] and intent.course_name == case["course_name"]
    assert {case["id"] for case in data["cases"]} == {"B01", "B02", "B03", "B04"}


def test_infrastructure_errors_are_not_bugs_or_silent_false_negatives():
    result = detection_metrics([
        {"fault": "none", "outcome": "PASS", "detected": False},
        {"fault": "response_id", "outcome": "TEST_INFRA_ERROR", "detected": False},
        {"fault": "response_name", "outcome": "PASS", "detected": False}])
    assert result["inconclusive_faults"] == 1
    assert result["false_negatives"] == 1 and result["false_positive_rate"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["manual", "fake"])
async def test_offline_baselines_have_explicitly_different_meanings(tmp_path, baseline):
    pytest.importorskip("langgraph")
    result = await evaluate(baseline, ReportStore(tmp_path))
    assert result["generation_calls"] == 0
    assert result["end_to_end_success_rate"] == (1 if baseline == "manual" else .5)
    assert result["safe_rejections"] == 2
    assert result["planning_detection"]["detected"] == (4 if baseline == "manual" else 3)
    assert result["async_executor_only"]["detected"] == 2
    assert result["planning_detection"]["false_positives"] == 0
    assert all(row["course_creates"] <= 1 for row in result["pairs"])


@pytest.mark.asyncio
async def test_real_mode_budget_zero_never_calls_planner(tmp_path):
    pytest.importorskip("langgraph")
    with pytest.raises(ValueError):
        await evaluate("real", ReportStore(tmp_path), planner=object(), max_calls=0,
                       max_tokens=1)


@pytest.mark.asyncio
async def test_one_model_plan_reused_for_pairs_without_gold_labels(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.defect_evaluation import annotation
    from agenttest.workflow import FakeLLM

    case = json.loads(TASKS.read_bytes())["cases"][0]
    planner = FakeLLM(manual_plan(annotation(case)).model_dump_json())
    planner.token_reservation = 1
    result = await evaluate("real", ReportStore(tmp_path), planner=planner,
                            max_calls=1, max_tokens=1)
    assert planner.calls == 1 and result["attempted_tasks"] == 1
    assert len(result["pairs"]) == 6
    raw = json.dumps(planner.contexts)
    assert all(key not in raw for key in ["faults", "review_status", "Golden", "B02"])
