"""Actual review findings, human-record consistency and zero-call experiment preparation."""

import json

import pytest
from test_eval_v2 import report, suite

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.models import Suite
from agenttest.eval_v2.protocol import prepare_protocol
from agenttest.eval_v2.review import (
    ReviewRecord,
    freeze_review,
    review_template,
    verify_review_lock,
)
from agenttest.eval_v2.runner import EvalConfig, oracle_plan, prepare_inputs
from agenttest.eval_v2.scoring import capture_plan, score_report
from agenttest.report import PlanStepEvidence, fingerprint


def reviewed_fixture(*ids):
    """Synthetic test-only human declarations; never published as reviewed evaluation data."""
    tasks = suite(*(ids or ("C004",)))
    tasks.split, tasks.dataset_version = "independent_holdout", "synthetic-review-v1"
    inputs = prepare_inputs(tasks, EvalConfig(development=True))[2]
    for c in tasks.cases:
        name = "Synthetic fixture " + c.case_id
        if c.labels.rubric:
            old = next(s for s in c.labels.rubric.steps if s.key == "create").arguments[0].value
            c.task.user_task = c.task.user_task.replace(old, name)
            next(s for s in c.labels.rubric.steps if s.key == "create").arguments[0].value = name
        else:
            c.task.user_task += " synthetic fixture"
        c.labels.review_status = "HUMAN_REVIEWED"
        c.labels.label_provenance = "independent_human"
        c.labels.review_source = "synthetic test record only"
        c.review_record = ReviewRecord(
            source_ref="synthetic://test-record", source_content_sha256=fingerprint(name),
            sampled_at="2026-10-12T00:00:00+00:00", sampling_method="test fixture only",
            participated_in_development=False, operator_independence_confirmed=True,
            reviewer_ref="synthetic reviewer, not a real human attestation",
            reviewed_at="2026-10-12T01:00:00+00:00", decision=c.labels.expected_behavior,
            rationale="Synthetic record for gate regression only",
            task_sha256=fingerprint(c.task.model_dump()),
            labels_sha256=fingerprint(c.labels.model_dump()),
            contract_sha256=inputs["contract_sha256"], policy_sha256=inputs["policy_sha256"],
            target_config_sha256=inputs["target_config_sha256"], fixture_version="mini-nl-v1",
            acceptable_topology="Rubric partial order only", forbidden_extras="All unlisted steps",
            rejection_evidence=["Synthetic unsupported operation"] if not c.labels.rubric else [],
        )
    return tasks


def bound_report(case):
    value = report()
    plan = oracle_plan(case)
    evidence = capture_plan(plan)
    value.scenario_sha256 = evidence.scenario_sha256
    value.user_task_sha256 = fingerprint(case.task.user_task)
    value.plan_structure = [PlanStepEvidence(**{
        k: getattr(s, k) for k in PlanStepEvidence.model_fields
    }) for s in evidence.steps]
    return value, evidence


def test_wrong_task_report_cannot_satisfy_current_task():
    case = suite("C004").cases[0]
    result, plan = bound_report(case)
    result.steps = [result.steps[0].model_copy(deep=True) for _ in plan.steps]
    for r, s in zip(result.steps, plan.steps):
        r.step_id, r.operation_id = s.step_id, s.operation_id
    assert score_report(case, result, plan)["end_to_end_success"]
    result.user_task_sha256 = fingerprint("different task")
    assert not score_report(case, result, plan)["end_to_end_success"]


def test_structural_evidence_cannot_contradict_workflow_roles_or_values():
    case = suite("C004").cases[0]
    result, evidence = bound_report(case)
    result.plan_structure[0].role = "bob"
    score = score_report(case, result, evidence)
    assert not score["plan_valid"] and "ARCHIVED_PLAN_REPORT_MISMATCH" in score["diagnostics"]


@pytest.mark.parametrize("mutation", ["renamed", "format_only", "duplicate", "record", "label",
                                     "task", "contract", "exposure", "rejection", "time"])
def test_formal_review_gate_checks_more_than_status_or_case_id(mutation):
    tasks = reviewed_fixture("C004", "C022")
    tasks.require_formal()
    if mutation in {"renamed", "format_only"}:
        original = suite("C004").cases[0]
        tasks.cases[0].task = original.task
        tasks.cases[0].case_id = "C099"
        if mutation == "format_only":
            tasks.cases[0].task.user_task = "  " + original.task.user_task.replace("，", " , ")
    elif mutation == "duplicate":
        duplicate = tasks.cases[0].model_copy(deep=True)
        duplicate.case_id = "C099"
        tasks.cases.append(duplicate)
    elif mutation == "record":
        tasks.cases[0].review_record = None
    elif mutation == "label":
        tasks.cases[0].labels.limitation = "modified after review"
    elif mutation == "task":
        tasks.cases[0].task.user_task += " changed"
    elif mutation == "contract":
        tasks.cases[0].review_record.contract_sha256 = fingerprint("wrong contract")
    elif mutation == "exposure":
        tasks.cases[0].review_record.participated_in_development = True
    elif mutation == "rejection":
        tasks.cases[1].review_record.rejection_evidence = []
    else:
        tasks.cases[0].review_record.reviewed_at = "2026-10-11T00:00:00+00:00"
    with pytest.raises(ValueError):
        tasks.require_formal()


def test_review_version_registry_is_exclusive_and_modification_requires_new_version(tmp_path):
    tasks = reviewed_fixture()
    locked = freeze_review(tasks, tmp_path)
    verify_review_lock(tasks, tmp_path / (tasks.dataset_version + ".json"))
    with pytest.raises(FileExistsError):
        freeze_review(tasks, tmp_path)
    tasks.origin += " changed"
    with pytest.raises(ValueError, match="new reviewed version"):
        verify_review_lock(tasks, tmp_path / (tasks.dataset_version + ".json"))
    with pytest.raises(FileExistsError):
        freeze_review(tasks, tmp_path)
    tasks.dataset_version = "synthetic-review-v2"
    assert freeze_review(tasks, tmp_path)["sha256"] != locked["sha256"]


def test_errors_are_not_trusted_safe_rejection_labels():
    tasks = suite("C022").model_dump(mode="json")
    tasks["cases"][0]["labels"]["rejection_basis"] = ["MODEL_TIMEOUT"]
    with pytest.raises(ValueError, match="Errors cannot"):
        Suite.model_validate_json(json.dumps(tasks))


def test_missing_intent_validator_is_configuration_not_safe_rejection():
    tasks = suite("C022").model_dump(mode="json")
    tasks["cases"][0]["labels"]["rejection_basis"] = ["INTENT_VALIDATOR_REQUIRED"]
    with pytest.raises(ValueError, match="Errors cannot"):
        Suite.model_validate_json(json.dumps(tasks))


@pytest.mark.parametrize("filename,name", [
    ("nl-tasks.json", "线性代数"), ("stage4b-tasks.json", "阶段四字段校验"),
])
def test_used_legacy_tasks_cannot_enter_holdout_under_another_case_id(filename, name):
    tasks = reviewed_fixture("C004")
    case = tasks.cases[0]
    path = PROJECT_ROOT / "examples" / filename
    case.task.user_task = json.loads(path.read_text(encoding="utf-8"))["cases"][0]["user_task"]
    for step in case.labels.rubric.steps:
        for arg in step.arguments:
            if arg.location == "body" and arg.name == "name":
                arg.value = name
    case.review_record.task_sha256 = fingerprint(case.task.model_dump())
    case.review_record.labels_sha256 = fingerprint(case.labels.model_dump())
    with pytest.raises(ValueError, match="overlap"):
        tasks.require_formal()


def test_review_cannot_freeze_labels_outside_trusted_contract_or_policy():
    tasks = reviewed_fixture()
    case = tasks.cases[0]
    case.labels.rubric.steps[1].operation_id = "unapprovedDelete"
    case.review_record.labels_sha256 = fingerprint(case.labels.model_dump())
    with pytest.raises(ValueError):
        tasks.require_formal()


@pytest.mark.parametrize("variant", ["A", "B"])
def test_all_hidden_label_changes_leave_complete_planner_context_identical(variant):
    tasks = suite()
    before = prepare_inputs(tasks, EvalConfig(variant=variant))[1]
    for c in tasks.cases:
        c.labels.limitation = "HIDDEN_SENTINEL"
        c.labels.review_source = "HIDDEN_REVIEW"
        c.labels.faults = []
        c.category = "injection"
        if c.labels.rubric:
            for step in c.labels.rubric.steps:
                step.role, step.operation_id, step.policy_id = (
                    "HIDDEN_ROLE", "HIDDEN_OP", "HIDDEN_POLICY"
                )
                for arg in step.arguments:
                    arg.value = "HIDDEN_VALUE"
                    arg.source, arg.selector = None, None
    assert prepare_inputs(tasks, EvalConfig(variant=variant))[1] == before
    assert "HIDDEN_" not in json.dumps(before)


def test_protocol_and_template_never_make_calls_or_self_approve(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Experiment preparation must not construct a network client")

    monkeypatch.setattr("httpx.AsyncHTTPTransport", forbidden)
    result = prepare_protocol(suite(), PROJECT_ROOT)
    assert not result["ready_to_run"] and not result["operator_authorized"]
    assert result["model_calls"] == result["business_http_requests"] == 0
    assert "INDEPENDENT_REVIEWED_HOLDOUT_AND_LOCK_REQUIRED" in result["blockers"]
    template = review_template()
    assert template["labels"]["review_status"] == "UNREVIEWED"
    assert template["review_record"]["operator_independence_confirmed"] is False
    assert "independent_human" not in json.dumps(template)


def test_protocol_checks_both_group_budgets_and_independent_directories(tmp_path):
    tasks = reviewed_fixture("C004", "C022")
    freeze_review(tasks, tmp_path)
    result = prepare_protocol(
        tasks, PROJECT_ROOT, review_lock=tmp_path / (tasks.dataset_version + ".json"),
        model="synthetic", endpoint="https://protocol.invalid", max_calls=1,
        max_total_tokens=22048,
    )
    assert "BUDGET_CANNOT_COVER_EVERY_SCHEDULED_CASE" in result["blockers"]
    assert result["combined_authorization_ceiling"] == {"model_calls": 2, "total_tokens": 44096}
    assert result["command_templates"]["A"][-1] != result["command_templates"]["B"][-1]
    assert result["observed_tokens"] is None
