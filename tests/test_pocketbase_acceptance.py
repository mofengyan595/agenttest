import json
from uuid import uuid4

import pytest

from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.scoring import capture_plan
from agenttest.pocketbase_acceptance import (
    CASE_IDS,
    DIRECTORY,
    PROJECT_ROOT,
    check_offline,
    credentials,
    fixture_transport,
    graded,
    inputs,
    run_trial,
    schedule,
    summarize,
    verify,
)
from agenttest.policies import NeedsReview
from agenttest.report import ReportStore
from agenttest.task_contract import task_validator


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_fixed_plans_pass_existing_gates_without_loosening_business_expectations(case_id):
    data, plan = inputs(case_id, credentials())
    assert data.target.environment == "isolated"
    assert all(not s.read_retries and not s.poll for s in plan.steps)
    assert len(capture_plan(plan).steps) == len(data.task.requirements.steps)


@pytest.mark.parametrize("case_id", ["PB903", "PB904"])
@pytest.mark.asyncio
async def test_upstream_ownership_failure_is_an_assertion_failure_not_a_pass(case_id, tmp_path):
    pytest.importorskip("agenttest.workflow")
    from agenttest.workflow import FakeLLM

    environ = credentials()
    _, plan = inputs(case_id, environ)
    row = await run_trial({"case_id": case_id, "phase": "baseline", "round": 0},
        FakeLLM(plan.model_dump_json()), ReportStore(tmp_path), environ,
        fixture_transport(environ, "ownership_leak"), lambda: None)
    assert not row["grade"]["verified"]
    assert row["outcome"] == "ASSERTION_FAILED"
    report = ReportStore(tmp_path).read(row["run_id"])
    other_steps = {s.id for s in plan.steps if s.role == "other" and s.operation_id != "pbLogin"}
    assert any(s.step_id in other_steps and any(c.kind == "http_status" and not c.passed
                                               for c in s.checks) for s in report.steps)
    assert report.task_satisfied is False
    raw = (tmp_path / row["run_id"] / "report.json").read_text(encoding="utf-8")
    assert all(value not in raw for key, value in environ.items() if key.endswith("PASSWORD"))


def test_wrong_resource_reference_and_reordered_verification_are_rejected():
    environ = credentials()
    data, plan = inputs("PB904", environ)
    validate = task_validator(data.task, data.contract, data.target, data.registry)
    plan.steps[-2], plan.steps[-1] = plan.steps[-1], plan.steps[-2]
    with pytest.raises(NeedsReview, match="PLAN_MISMATCH"):
        validate(data.task.user_task, plan)
    data, plan = inputs("PB902", environ)
    plan.steps[-1].path_parameters["recordId"].from_step = "create"
    with pytest.raises(NeedsReview, match="PLAN_MISMATCH"):
        validate = task_validator(data.task, data.contract, data.target, data.registry)
        validate(data.task.user_task, plan)


def test_catalog_and_sources_are_development_only_and_credentials_are_not_model_context():
    from agenttest.dsl import Scenario
    from agenttest.planner_input import planning_context

    environ = credentials()
    data, _ = inputs("PB901", environ)
    context = planning_context(data.contract, data.target, data.registry, data.task.user_task,
                               Scenario.model_json_schema())
    raw = json.dumps(context)
    assert "reference-plans" not in raw and "requirements" not in raw
    assert all(value not in raw for value in environ.values())
    assert data.target.base_url not in raw
    provenance = read_json(DIRECTORY / "provenance.json")
    assert provenance["human_reviewed"] == 0 and provenance["formal_eval_v2_score"] is None
    assert "not an upstream OpenAPI export" in provenance["contract_basis"]


@pytest.mark.asyncio
async def test_full_denominator_is_cold_regraded_and_resealed_wrong_results_fail():
    pytest.importorskip("agenttest.workflow")
    output = PROJECT_ROOT / "runs" / ("pytest-pocketbase-" + uuid4().hex)
    result = await check_offline(output)
    assert result["baseline_successes"] == 6 and result["trial_successes"] == 18
    assert result["real_model_calls"] == 0
    assert result["target_evidence"] == "SYNTHETIC_PROTOCOL_ONLY"
    assert verify(output)["rescored_positions"] == 24
    rows = read_json(output / "results.json")["results"]
    with pytest.raises(ValueError, match="denominator"):
        summarize(rows[:-1], "mock_transport")
    report = ReportStore(output / "reports").read(rows[0]["run_id"])
    task = inputs("PB901", credentials())[0].task
    report.target_config_sha256 = "0" * 64
    assert not graded(task, report, capture_plan(inputs("PB901", credentials())[1]))["verified"]
    changed = read_json(output / "results.json")
    changed["results"][6]["grade"]["verified"] = False
    (output / "results.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="archive changed"):
        verify(output)
    from agenttest.eval_v2.archive import inventory

    changed["summary"] = summarize(changed["results"], "mock_transport")
    (output / "results.json").write_text(json.dumps(changed), encoding="utf-8")
    (output / "planning-R1-PB901.json").write_text(
        json.dumps(changed["results"][6]), encoding="utf-8")
    (output / "archive.json").write_text(json.dumps({
        "schema_version": "agenttest.pocketbase.archive.v1", "files": inventory(output)}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="regrading mismatch"):
        verify(output)


def test_schedule_has_fixed_baseline_and_three_rotated_attempts():
    rows = schedule()
    assert [r["case_id"] for r in rows[:6]] == list(CASE_IDS)
    assert all(r["round"] == 0 and r["phase"] == "baseline" for r in rows[:6])
    assert all(sum(r["case_id"] == key for r in rows[6:]) == 3 for key in CASE_IDS)
