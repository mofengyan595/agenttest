"""Fault switches are hidden, exact plans are paired, failed planning stays in the denominator."""

import json
from uuid import uuid4

import pytest

from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import read_json, seal
from agenttest.eval_v2.models import PlanEvidence
from agenttest.executor import Executor
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.report import ReportStore
from agenttest.task_contract import task_validator
from agenttest.ticket_fault_check import (
    CASE_IDS,
    PHASES,
    case_inputs,
    case_snapshot,
    check_faults,
    report_row,
    run_case,
    summarize,
    unattempted,
    verify_faults,
)


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_new_task_is_bound_and_normal_fault_have_identical_public_inputs(case_id):
    contexts = []
    for broken in (False, True):
        document, target, registry, environ, _, task, draft, _ = case_inputs(case_id, broken)
        contract = load_openapi(document)
        task_validator(task, contract, target, registry)(task.user_task, draft.scenario())
        Executor(target, contract, registry=registry, environ=environ).validate(draft.scenario())
        context = planning_context(contract, target, registry, task.user_task, {})
        assert not {"fault", "failed_check", "requirements", "goals", "rubric"} & context.keys()
        adapter = OpenAICompatiblePlanner(ModelSettings(model="offline-ticket-check",
            base_url="https://offline.invalid/v1", api_key="synthetic-fixture-key"),
            profile="generic")
        encoded = adapter.prepare_request(context)
        for value in environ.values():
            assert value.encode() not in encoded
        assert adapter.stats.input_token_upper_bound <= 20_000 and adapter.stats.calls == 0
        assert len(context["policies"]) == 6
        contexts.append(context)
    assert contexts[0] == contexts[1]


def pending():
    return [unattempted(key, phase, "MODEL_USAGE_UNAVAILABLE")
            for key in CASE_IDS for phase in PHASES]


def test_incomplete_planning_never_disappears_or_becomes_a_false_negative():
    result = summarize("real", pending())
    assert result["scheduled_tasks"] == result["scheduled_faults"] == 3
    assert result["not_executed"] == 9 and result["inconclusive_faults"] == 3
    assert result["inconclusive_controls"] == 3
    assert result["false_negatives"] == result["false_positives"] == 0
    assert result["paired_detections"] == result["real_model_calls"] == 0
    with pytest.raises(ValueError, match="denominator"):
        summarize("real", pending()[:-1])
    rows = pending()
    rows[2]["model_calls"] = 1
    with pytest.raises(ValueError, match="call count"):
        summarize("real", rows)


@pytest.mark.asyncio
async def test_invalid_generation_prevents_both_replays_and_all_requests(tmp_path):
    workflow = pytest.importorskip("agenttest.workflow")
    _, _, _, _, _, _, draft, _ = case_inputs("G701")
    draft.steps[-1].arguments[0].select = "$.courseId"
    rows = await run_case(case_snapshot("G701"), workflow.FakeLLM(
        draft.scenario().model_dump_json()), ReportStore(tmp_path), lambda: None)
    assert rows[0]["outcome"] == "NEEDS_REVIEW" and rows[0]["requests"] == 0
    assert all(not r["verified"] for r in rows)
    assert [r["outcome"] for r in rows[1:]] == ["NOT_ATTEMPTED", "NOT_ATTEMPTED"]


@pytest.mark.asyncio
async def test_fixed_pairs_exact_failed_check_and_archive_regrading():
    pytest.importorskip("agenttest.workflow")
    output = PROJECT_ROOT / "runs" / ("pytest-ticket-fault-" + uuid4().hex)
    result = await check_faults(output)
    assert result["generation_successes"] == result["paired_detections"] == 3
    assert result["controls_passed"] == 3 and result["model_calls"] == 3
    assert result["real_model_calls"] == result["not_executed"] == 0
    assert result["false_negatives"] == result["false_positives"] == 0
    assert verify_faults(output)["rescored_runs"] == 9
    rows = read_json(output / "results.json")["results"]
    frozen = read_json(output / "freeze.json")
    for index, key in enumerate(CASE_IDS):
        generation, control, fault = rows[index * 3:index * 3 + 3]
        assert generation["plan"] == control["plan"] == fault["plan"]
        assert control["outcome"] == "PASS" and fault["outcome"] == "ASSERTION_FAILED"
        assert control["model_calls"] == fault["model_calls"] == 0
        report = ReportStore(output / "reports").read(fault["run_id"])
        snapshot = frozen["cases"][key]
        snapshot["failed_check"] = ["readTicket", "equals", "$.not_the_fault"]
        wrong = report_row(snapshot, "fault", report, PlanEvidence.model_validate(
            fault["plan"]), generation["plan"]["scenario_sha256"])
        assert not wrong["verified"]
    data = read_json(output / "results.json")
    data["paired_detections"] = 0
    (output / "results.json").write_text(json.dumps(data), encoding="utf-8")
    (output / "archive.json").unlink()
    seal(output)
    with pytest.raises(ValueError, match="summary mismatch"):
        verify_faults(output)


@pytest.mark.asyncio
async def test_output_is_restricted_to_new_private_runs_directory(tmp_path):
    pytest.importorskip("agenttest.workflow")
    with pytest.raises(ValueError, match="under repository runs"):
        await check_faults(PROJECT_ROOT / "examples/no-ticket-evidence-here")
