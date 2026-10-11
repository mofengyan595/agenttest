import pytest

pytest.importorskip("langgraph", reason="Agent evaluation needs optional graph runtime")

from agenttest.demo import PROJECT_ROOT
from agenttest.evaluation import TaskSuite, evaluate, manual_plan
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import ReportStore


def suite():
    return TaskSuite.load(PROJECT_ROOT / "examples/nl-tasks.json")


def test_human_review_and_unique_tasks_are_recorded():
    tasks = suite()
    assert tasks.reviewed and tasks.review_source
    assert len(tasks.cases) == len({case.user_task for case in tasks.cases}) == 18
    assert sum(c.category == "consistency" for c in tasks.cases) == 9
    for case in tasks.cases:
        plan = manual_plan(case)
        assert plan.steps[-1].path_parameters["courseId"].from_step == "create"


@pytest.mark.asyncio
async def test_manual_and_fixed_fake_are_distinct_baselines(tmp_path):
    manual = await evaluate(suite(), "manual", ReportStore(tmp_path / "manual"))
    fake = await evaluate(suite(), "fake", ReportStore(tmp_path / "fake"))
    assert manual["attempted"] == fake["attempted"] == 18
    for field in ("plan_valid_rate", "intent_satisfied_rate", "policy_selection_correct_rate",
                  "end_to_end_success_rate"):
        assert manual[field] == 1
    assert fake["plan_valid_rate"] == 1
    assert fake["intent_satisfied_rate"] == fake["end_to_end_success_rate"] == 1 / 18
    assert fake["policy_selection_correct_rate"] == 5 / 18
    assert fake["failure_classes"] == {"PASS": 1, "NEEDS_REVIEW": 17}
    assert fake["by_category"]["ownership"]["end_to_end_success_rate"] == 0
    assert manual["generation_calls"] == fake["generation_calls"] == 0


@pytest.mark.asyncio
async def test_real_benchmark_cannot_run_without_review_and_budget(tmp_path):
    tasks = suite()
    tasks.reviewed = False
    with pytest.raises(ValueError):
        await evaluate(tasks, "real", ReportStore(tmp_path), planner=object(),
                       max_live_calls=18, max_total_tokens=60_000)
    with pytest.raises(ValueError):
        await evaluate(suite(), "real", ReportStore(tmp_path))


@pytest.mark.asyncio
async def test_evaluation_budget_reserves_before_api(tmp_path):
    import httpx

    requests = []
    planner = OpenAICompatiblePlanner(ModelSettings(model="fixture-model",
        base_url="https://model.invalid/v1", api_key="synthetic-model-key"),
        httpx.MockTransport(lambda request: requests.append(request)))
    result = await evaluate(suite(), "real", ReportStore(tmp_path), planner=planner,
                            max_live_calls=18, max_total_tokens=1)
    assert result["attempted"] == 0 and result["not_run"] == 18 and not requests
    assert result["plan_valid_rate"] is None and result["stop_reason"] == "EVALUATION_BUDGET"


@pytest.mark.asyncio
async def test_golden_labels_never_sent_and_call_limit_applies(tmp_path):
    import json

    import httpx
    from test_llm_planner import completion
    from test_natural_workflow import wire

    requests = []
    tasks = suite()

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(json.dumps(wire(manual_plan(tasks.cases[0])))))

    planner = OpenAICompatiblePlanner(ModelSettings(model="fixture-model",
        base_url="https://model.invalid/v1", api_key="synthetic-model-key"),
        httpx.MockTransport(handler))
    result = await evaluate(tasks, "real", ReportStore(tmp_path), planner=planner,
                            max_live_calls=1, max_total_tokens=60_000)
    assert result["attempted"] == 1 and result["not_run"] == 17 and len(requests) == 1
    context = json.loads(requests[0]["messages"][1]["content"])
    assert set(context) == {"user_task", "operations", "policies", "roles", "security_schemes"}
    for label in ("category", "creator", "course_name", "reviewed", "review_source", "cases"):
        assert label not in context
    assert result["intent_satisfied_rate"] == 1


@pytest.mark.asyncio
async def test_auth_error_stops_entire_suite_without_retry(tmp_path):
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401)

    planner = OpenAICompatiblePlanner(ModelSettings(model="fixture-model",
        base_url="https://model.invalid/v1", api_key="synthetic-model-key"),
        httpx.MockTransport(handler))
    result = await evaluate(suite(), "real", ReportStore(tmp_path), planner=planner,
                            max_live_calls=18, max_total_tokens=60_000)
    assert len(requests) == result["attempted"] == 1
    assert result["stop_reason"] == "CHECK_MODEL_CONFIGURATION"
    assert result["results"][0]["failure_class"] == "TOOL_ERROR"
