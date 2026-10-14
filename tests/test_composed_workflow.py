"""Same catalog, full trusted graph, false-success guards and sealed offline rescore."""

import json

import httpx
import pytest

pytest.importorskip("langgraph", reason="Composed NL workflow uses the existing agent extra")

from agenttest.composed_check import SUITE_PATH, check_composition, verify_composition
from agenttest.composed_demo import ComposedDraftPlanner, run_composed_demo
from agenttest.composed_evaluation import (
    CompositionSuite,
    capture_composition,
    reference_draft,
    score_composed_report,
)
from agenttest.composed_plan import composed_registry
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import Outcome, ReportStore
from agenttest.workflow import FakeLLM

SUITE = CompositionSuite.model_validate(read_json(SUITE_PATH))
COMPLETE = SUITE.cases[4]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [c for c in SUITE.cases if c.expected], ids=lambda c: c.case_id)
async def test_goal_combinations_execute_with_one_untrusted_fixture_generation(tmp_path, case):
    draft, registry = reference_draft(case.expected), composed_registry()
    fake = FakeLLM(draft.model_dump_json())
    store = ReportStore(tmp_path)
    report = await run_composed_demo(case.user_task, ComposedDraftPlanner(fake), store)
    result = score_composed_report(case, report, capture_composition(draft.scenario(), registry),
                                   registry)
    assert result["end_to_end_success"] and result["mechanism_verified"]
    assert report.outcome == Outcome.PASS and report == store.read(report.run_id)
    assert fake.calls == 1 and report.planner_calls == 1
    assert len(report.steps) == len(draft.steps)
    assert sum(len(s.attempts) for s in report.steps) <= 10
    assert len([s for s in report.steps if s.operation_id == "createCourse"]) == 1


@pytest.mark.asyncio
async def test_mock_structured_adapter_has_same_catalog_and_no_answer_injection(tmp_path):
    draft, requests = reference_draft(COMPLETE.expected), []

    def handler(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        assert len(context["operations"]) == 6 and len(context["policies"]) == 7
        assert context["user_task"] == COMPLETE.user_task
        assert not {"rubric", "expected", "goals", "rejection_code"} & context.keys()
        schema = payload["response_format"]["json_schema"]["schema"]
        properties = schema["$defs"]["ComposedDraftStep"]["properties"]
        assert properties["role"] == {"type": "string", "enum": ["alice", "bob"]}
        assert "csv_fixture_id" in properties and "poll_policy_id" in properties
        assert "Use exactly" not in payload["messages"][0]["content"]
        requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": draft.model_dump_json()}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    planner = OpenAICompatiblePlanner(ModelSettings(model="offline-fixture",
        base_url="https://model.invalid/v1", api_key="synthetic-offline-key"),
        httpx.MockTransport(handler), profile="mini_composed")
    report = await run_composed_demo(COMPLETE.user_task, planner, ReportStore(tmp_path))
    assert report.task_satisfied and report.workflow_completed and report.outcome == Outcome.PASS
    assert len(requests) == 1 and report.model_call.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [c for c in SUITE.cases if not c.expected],
                         ids=lambda c: c.case_id)
async def test_valid_dsl_cannot_hide_unapproved_user_goals(tmp_path, case):
    draft = reference_draft(COMPLETE.expected)
    report = await run_composed_demo(case.user_task, FakeLLM(draft.scenario().model_dump_json()),
                                     ReportStore(tmp_path))
    assert report.outcome == Outcome.NEEDS_REVIEW
    assert report.diagnostic.code == case.rejection_code
    assert not report.steps and report.tool_calls == 0 and report.task_satisfied is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing_goal", "extra_goal", "wrong_resource",
                                      "name", "results_before_poll", "csv", "retry"])
async def test_valid_operations_do_not_substitute_for_exact_goals(tmp_path, mutation):
    draft = reference_draft(COMPLETE.expected)
    plan, task = draft.scenario(), COMPLETE.user_task
    steps = {s.id: s for s in plan.steps}
    if mutation == "missing_goal":
        plan.steps.remove(steps["denied"])
    elif mutation == "extra_goal":
        task = SUITE.cases[0].user_task
    elif mutation == "wrong_resource":
        steps["results"].path_parameters["batchId"].from_step = "poll"
    elif mutation == "name":
        steps["create"].body["name"].value = "wrong name"
    elif mutation == "results_before_poll":
        plan.steps[-1], plan.steps[-2] = plan.steps[-2], plan.steps[-1]
    elif mutation == "csv":
        steps["upload"].upload.content += "third row\n"
    else:
        steps["read"].read_retries = 1
    report = await run_composed_demo(task, FakeLLM(plan.model_dump_json()), ReportStore(tmp_path))
    assert report.outcome in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}
    assert not report.steps and report.tool_calls == 0 and report.task_satisfied is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,failed_operation", [("ownership", "getCourse"),
    ("terminal", "getBatch"), ("result_count", "getBatchResults")])
async def test_faults_keep_assertion_evidence_and_never_become_success(tmp_path, fault,
                                                                    failed_operation):
    plan, registry = reference_draft(COMPLETE.expected).scenario(), composed_registry()
    report = await run_composed_demo(COMPLETE.user_task, FakeLLM(plan.model_dump_json()),
                                     ReportStore(tmp_path), fault=fault)
    assert report.outcome == Outcome.ASSERTION_FAILED and not report.task_satisfied
    assert report.workflow_completed and report.steps[-1].operation_id == failed_operation
    assert any(not c.passed for c in report.steps[-1].checks)
    result = score_composed_report(COMPLETE, report, capture_composition(plan, registry), registry)
    assert result["intent"]["passed"] and not result["end_to_end_success"]


@pytest.mark.asyncio
async def test_report_flags_without_complete_bound_evidence_cannot_score_success(tmp_path):
    plan, registry = reference_draft(COMPLETE.expected).scenario(), composed_registry()
    evidence = capture_composition(plan, registry)
    report = await run_composed_demo(COMPLETE.user_task, FakeLLM(plan.model_dump_json()),
                                     ReportStore(tmp_path))
    for field, value in [("user_task_sha256", "0" * 64), ("scenario_sha256", "0" * 64),
                         ("plan_structure", []), ("workflow_completed", False),
                         ("completed", False), ("task_satisfied", False),
                         ("intent_verified", False),
                         ("dsl_valid", False), ("policy_authorized", False), ("steps", []),
                         ("task_mode", "operator_plan"), ("investigation_error", "IO_ERROR")]:
        changed = report.model_copy(deep=True, update={field: value})
        assert not score_composed_report(COMPLETE, changed, evidence, registry)[
            "end_to_end_success"], field
    missing_attempts = report.model_copy(deep=True)
    missing_attempts.steps[-1].attempts = []
    assert not score_composed_report(COMPLETE, missing_attempts, evidence, registry)[
        "end_to_end_success"]
    for checks in ([], [report.steps[-1].checks[0].model_copy(update={"passed": False})]):
        missing_checks = report.model_copy(deep=True)
        missing_checks.steps[-1].checks = checks
        assert not score_composed_report(COMPLETE, missing_checks, evidence, registry)[
            "end_to_end_success"]


@pytest.mark.asyncio
async def test_sealed_archive_rescores_without_current_sources_and_detects_tampering(tmp_path,
                                                                                   monkeypatch):
    directory = tmp_path / "composition"
    summary = await check_composition(directory)
    assert summary["cases"] == summary["mechanism_verified"] == 12
    assert summary["formal_eval_v2_score"] is None and summary["real_model_calls"] == 0
    assert summary["human_reviewed"] == 0
    with pytest.raises(FileExistsError):
        await check_composition(directory)
    monkeypatch.setattr("agenttest.composed_check.composed_inputs",
                        lambda: pytest.fail("Rescore must not reconstruct a current target"))
    assert verify_composition(directory)["rescored_cases"] == 12
    path = directory / "P005.plan.json"
    data = read_json(path)
    next(s for s in data["steps"] if s["step_id"] == "poll")["execution"][
        "poll_policy_sha256"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        verify_composition(directory)
    manifest = read_json(directory / "archive.json")
    manifest["files"] = inventory(directory)
    (directory / "archive.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="rescore"):
        verify_composition(directory)
