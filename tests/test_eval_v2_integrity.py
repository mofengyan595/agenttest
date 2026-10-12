"""Regression evidence for mechanism matching and archived regrading without graph or HTTP."""

import json

import pytest
from test_eval_v2 import report, suite
from test_eval_v2_review import bound_report

from agenttest.eval_v2.archive import seal, verify_archive
from agenttest.eval_v2.audit import rescore
from agenttest.eval_v2.faults import pair_result, summarize_faults
from agenttest.eval_v2.models import CaseResult
from agenttest.evaluation import save_evaluation
from agenttest.report import CheckResult, Outcome, PlanStepEvidence, ReportStore, fingerprint


def test_owner_status_failure_is_not_ownership_detection():
    normal, fault = report(), report()
    fault.outcome = Outcome.ASSERTION_FAILED
    fault.steps[0].checks = [
        CheckResult(kind="http_status", passed=False, expected=[200], actual=500)
    ]
    pair = pair_result("C013", "ownership", normal, fault, True)
    assert pair["status"] == "NOT_EXECUTED" and not pair["executed"]
    assert summarize_faults([pair])["coverage"]["numerator"] == 0


def test_partial_control_and_assertion_without_requests_are_inconclusive():
    normal, fault = report(), report()
    fault.outcome = Outcome.ASSERTION_FAILED
    fault.steps[0].checks = [CheckResult(kind="equals", selector="$.name", passed=False)]
    normal.plan_structure.append(PlanStepEvidence(
        step_id="missing", operation_id="login", role="alice", policy_id="nl_login_v1"
    ))
    assert pair_result("C016", "response_name", normal, fault, True)["status"] == (
        "INCONCLUSIVE_CONTROL"
    )
    normal.steps, normal.outcome = [], Outcome.ASSERTION_FAILED
    pair = pair_result("C016", "response_name", normal, fault, True)
    assert not pair["false_positive"]
    assert summarize_faults([pair])["executed_false_positive"]["rate"] is None


def test_partial_fault_pass_is_not_executed_false_negative():
    normal, fault = report(), report()
    missing = PlanStepEvidence(
        step_id="missing", operation_id="login", role="alice", policy_id="nl_login_v1"
    )
    normal.plan_structure.append(missing)
    fault.plan_structure.append(missing)
    completed = normal.steps[0].model_copy(deep=True)
    completed.step_id, completed.operation_id = "missing", "login"
    normal.steps.append(completed)
    pair = pair_result("C016", "response_name", normal, fault, True)
    assert pair["executed"] and pair["status"] == "INCONCLUSIVE"
    assert summarize_faults([pair])["executed_false_negative"]["rate"] is None


def synthetic_archive(directory):
    """Intentionally incorrect cached booleans, with sufficient synthetic underlying evidence."""
    tasks = suite("C004")
    case = tasks.cases[0]
    report_value, plan = bound_report(case)
    report_value.steps = [report_value.steps[0].model_copy(deep=True) for _ in plan.steps]
    for r, s in zip(report_value.steps, plan.steps):
        r.step_id, r.operation_id = s.step_id, s.operation_id
    store = ReportStore(directory / "reports")
    store.save(report_value)
    row = CaseResult(
        case_id=case.case_id, category=case.category, source="manual", variant="A",
        classification="INTENT_MISMATCH", plan_valid=True, end_to_end_success=False,
        run_id=report_value.run_id, report_sha256=fingerprint(report_value.model_dump()),
        plan_sha256=plan.scenario_sha256, plan_evidence=plan, usage_status="NOT_APPLICABLE",
    ).model_dump(mode="json")
    save_evaluation(row, directory / "C004.json")
    save_evaluation({"suite_sha256": fingerprint(tasks.model_dump()),
                     "dataset_version": tasks.dataset_version,
                     "evaluation_config": {"source": "manual", "variant": "A"}},
                    directory / "freeze.json")
    save_evaluation({"source": "manual", "variant": "A", "dataset_version": tasks.dataset_version,
                     "freeze_valid": True, "results": [row]}, directory / "results.json")
    (directory / "summary.md").write_text("Synthetic archive", encoding="utf-8")
    seal(directory)
    return tasks


def test_rescore_recomputes_from_structure_instead_of_cached_booleans(tmp_path):
    tasks = synthetic_archive(tmp_path)
    result = rescore(tasks, tmp_path)
    assert result["audit_status"] == "RECOMPUTED_FROM_SEALED_EVIDENCE"
    assert result["results"][0]["intent"]["passed"]
    assert result["results"][0]["end_to_end_success"]
    assert not result["independent_experiment"]


@pytest.mark.parametrize("artifact", ["selector", "report", "results", "freeze", "case_id"])
def test_replacing_any_scoring_artifact_is_rejected(tmp_path, artifact):
    tasks = synthetic_archive(tmp_path)
    path = tmp_path / ("C004.json" if artifact in {"selector", "case_id"}
                       else "results.json" if artifact == "results" else "freeze.json")
    if artifact == "report":
        path = next((tmp_path / "reports").glob("*/report.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    if artifact == "selector":
        value["plan_evidence"]["steps"][-1]["selectors"]["path_parameters.courseId"] = "$.other"
    elif artifact == "case_id":
        value["case_id"] = "C005"
    else:
        value["replacement"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        rescore(tasks, tmp_path)


def test_archive_is_exclusive_and_missing_seal_is_explicitly_unverifiable(tmp_path):
    tasks = synthetic_archive(tmp_path)
    with pytest.raises(FileExistsError):
        seal(tmp_path)
    # Only a synthetic test-owned seal is removed to exercise legacy/incomplete evidence.
    (tmp_path / "archive.json").unlink()
    result = rescore(tasks, tmp_path)
    assert result["audit_status"] == "UNVERIFIABLE_LEGACY_OR_INCOMPLETE_ARCHIVE"
    assert not result["results"]


def test_duplicate_archived_plan_ids_cannot_create_one_to_many_matches():
    from agenttest.eval_v2.models import PlanEvidence
    from agenttest.eval_v2.runner import oracle_plan
    from agenttest.eval_v2.scoring import capture_plan

    evidence = capture_plan(oracle_plan(suite("C004").cases[0])).model_dump()
    evidence["steps"][1]["step_id"] = evidence["steps"][0]["step_id"]
    with pytest.raises(ValueError, match="unique step"):
        PlanEvidence.model_validate(evidence)


def test_case_snapshot_and_results_cross_reference_are_checked(tmp_path):
    synthetic_archive(tmp_path)
    path = tmp_path / "C004.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["classification"] = "PASS"
    path.write_text(json.dumps(value), encoding="utf-8")
    # Simulate a faulty archiver sealing contradictory snapshots; consistency still matters.
    (tmp_path / "archive.json").unlink()
    seal(tmp_path)
    with pytest.raises(ValueError, match="identity mismatch"):
        verify_archive(tmp_path)
