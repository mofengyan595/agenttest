"""The 404-after-delete precondition: three situations, three different verdicts.

P0 measured a false positive: a read expected ``404`` while the parcel still existed, and the
run reported ``ASSERTION_FAILED`` (a business defect). These tests pin the rule that
replaces it:

1. the plan itself lacks the precondition          -> refused at preflight;
2. the precondition was not established at runtime -> ``NEEDS_REVIEW``, never a defect;
3. the precondition held and the response still    -> ``ASSERTION_FAILED`` stays.

The last test also pins the negative direction: an ordinary failing status expectation is
never reclassified, so this fix cannot be used to hide real defects.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_product_supervisor import lifecycle_project  # noqa: E402

from agenttest.dsl import ArgumentBinding, Scenario, Step  # noqa: E402
from agenttest.policies import NeedsReview  # noqa: E402
from agenttest.product_preconditions import (  # noqa: E402
    ABSENCE_PRECONDITION_MISSING,
    PRECONDITION_NOT_ESTABLISHED,
    PRECONDITION_UNPROVEN,
    absence_expectation,
    phase_of,
    precondition_report,
    reclassify_unestablished,
    validate_expectation_preconditions,
)
from agenttest.report import Outcome  # noqa: E402


def policies_by_operation(project):
    return {policy.operation_id: policy for policy in project.registry.definitions()
            if policy.negative is None}


def binding(step, select="$.id"):
    """A reference binding carries exactly one source: the producing step."""
    return ArgumentBinding(from_step=step, select=select)


def create_step(policy_id, identifier="create", name="parcel-a"):
    return Step(id=identifier, operation_id="createParcel", role=None, policy_id=policy_id,
                body={"name": ArgumentBinding(value=name),
                      "quantity": ArgumentBinding(value=2)})


def delete_step(policy_id, from_step="create"):
    return Step(id="delete", operation_id="deleteParcel", role=None, policy_id=policy_id,
                path_parameters={"parcelId": binding(from_step)})


def read_step(policy_id, *, from_step="create", identifier="read"):
    return Step(id=identifier, operation_id="readParcel", role=None, policy_id=policy_id,
                path_parameters={"parcelId": binding(from_step)})


def lifecycle(project):
    """The one policy per operation the generic intake registered for this contract."""
    return policies_by_operation(project)


def test_an_absence_expectation_is_derived_from_the_approval_not_the_status_code_alone():
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    read, delete, create = (policies["readParcel"], policies["deleteParcel"],
                            policies["createParcel"])
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    # The read policy approves 200; only an operator-approved 404 would be an absence phase.
    assert absence_expectation(read) is False
    assert phase_of(read, operations["readParcel"]) == "EXISTS"
    assert phase_of(delete, operations["deleteParcel"]) == "DELETED"
    assert phase_of(create, operations["createParcel"]) == "EXISTS"


def test_preflight_refuses_an_absence_expectation_without_a_deletion(monkeypatch):
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    monkeypatch.setattr("agenttest.product_preconditions.ABSENCE_STATUSES", frozenset({200}))
    # With 200 treated as "absent", the plain create+read plan carries the expectation
    # without ever deleting anything: exactly the P0 shape that produced false positives.
    scenario = Scenario(name="read after create", steps=[
        create_step(policies["createParcel"].id), read_step(policies["readParcel"].id)])
    report = precondition_report(project, scenario)
    assert report["expectations"] == 1
    assert report["rows"][0]["status"] == "MISSING"
    assert report["contradictions"] == ["read"]
    with pytest.raises(NeedsReview) as raised:
        validate_expectation_preconditions(project, scenario)
    assert raised.value.diagnostic.code == ABSENCE_PRECONDITION_MISSING


def test_a_literal_absence_probe_runs_but_is_only_review_evidence(monkeypatch):
    """GET /parcels/never-existed is legitimate, but nothing in the plan proves absence."""
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    monkeypatch.setattr("agenttest.product_preconditions.ABSENCE_STATUSES", frozenset({200}))
    literal = Step(id="read", operation_id="readParcel", role=None,
                   policy_id=policies["readParcel"].id,
                   path_parameters={"parcelId": ArgumentBinding(value="never-existed")})
    scenario = Scenario(name="probe a literal id", steps=[literal])
    report = validate_expectation_preconditions(project, scenario)  # not refused
    assert report["rows"][0]["status"] == "UNPROVEN"
    assert report["contradictions"] == []
    executed = executed_report(project, scenario, delete_outcome=Outcome.PASS,
                               read_outcome=Outcome.ASSERTION_FAILED)
    changes = reclassify_unestablished(executed, scenario, project)
    assert [item["step_id"] for item in changes] == ["read"]
    assert changes[0]["precondition_status"] == "UNPROVEN"


def test_preflight_accepts_the_expectation_once_the_same_resource_is_deleted(monkeypatch):
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    monkeypatch.setattr("agenttest.product_preconditions.ABSENCE_STATUSES", frozenset({200}))
    scenario = Scenario(name="delete then read", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id),
        read_step(policies["readParcel"].id)])
    report = validate_expectation_preconditions(project, scenario)
    assert report["satisfied"] is True
    assert report["rows"][0]["status"] == "ESTABLISHED"
    assert report["rows"][0]["deletions"] == ["delete"]
    assert report["rows"][0]["phase"] == "VERIFY_ABSENT"


def test_a_deletion_of_a_different_resource_does_not_establish_the_precondition(monkeypatch):
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    monkeypatch.setattr("agenttest.product_preconditions.ABSENCE_STATUSES", frozenset({200}))
    # Two creates: the delete cleans the second one while the read claims the first.
    scenario = Scenario(name="crossed resources", steps=[
        create_step(policies["createParcel"].id),
        create_step(policies["createParcel"].id, identifier="create2", name="parcel-b"),
        delete_step(policies["deleteParcel"].id, from_step="create2"),
        read_step(policies["readParcel"].id)])
    report = precondition_report(project, scenario)
    assert report["rows"][0]["status"] == "MISSING"
    assert "delete" not in report["rows"][0]["deletions"]


def executed_report(project, scenario, *, delete_outcome, read_outcome):
    """Build the minimum report shape the reclassification reads."""
    from agenttest.report import AttemptEvidence, RunReport, StepResult

    steps = []
    for step in scenario.steps:
        outcome = (delete_outcome if step.id == "delete" else
                   read_outcome if step.id == "read" else Outcome.PASS)
        steps.append(StepResult(
            step_id=step.id, step_run_id=step.id, operation_id=step.operation_id,
            method="DELETE" if step.operation_id == "deleteParcel" else "GET",
            path_template="/parcels", outcome=outcome, reason="POLICY_EVALUATED",
            policy_sha256="0" * 64, checks=[], approved_violation_codes=[],
            attempts=[AttemptEvidence(attempt=1, attempt_id="a1", status_code=200,
                                      elapsed_ms=1.0, response_bytes=2)]))
    return RunReport(run_id="run-1", started_at="2026-11-11T00:00:00Z",
                     scenario_sha256="0" * 64, contract_sha256="0" * 64,
                     environment="isolated", target_origin="http://127.0.0.1:18998",
                     outcome=Outcome.ASSERTION_FAILED, steps=steps, workflow_nodes=[])


def outcomes(report):
    return {step.step_id: step.outcome for step in report.steps}


def test_a_failed_absence_check_without_a_passed_deletion_is_not_a_defect(monkeypatch):
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    monkeypatch.setattr("agenttest.product_preconditions.ABSENCE_STATUSES", frozenset({200}))
    scenario = Scenario(name="delete then read", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id),
        read_step(policies["readParcel"].id)])
    # The deletion did not establish the phase, so the readback's mismatch proves nothing.
    report = executed_report(project, scenario, delete_outcome=Outcome.TEST_INFRA_ERROR,
                             read_outcome=Outcome.ASSERTION_FAILED)
    changes = reclassify_unestablished(report, scenario, project)
    assert [item["step_id"] for item in changes] == ["read"]
    assert changes[0]["previous_outcome"] == "ASSERTION_FAILED"
    assert changes[0]["reason"] == PRECONDITION_NOT_ESTABLISHED
    assert outcomes(report)["read"] == Outcome.NEEDS_REVIEW


def test_a_satisfied_precondition_keeps_the_failure_as_real_evidence(monkeypatch):
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    monkeypatch.setattr("agenttest.product_preconditions.ABSENCE_STATUSES", frozenset({200}))
    scenario = Scenario(name="delete then read", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id),
        read_step(policies["readParcel"].id)])
    # Deletion passed (204), so a 200 on the readback is a genuine expectation violation.
    report = executed_report(project, scenario, delete_outcome=Outcome.PASS,
                             read_outcome=Outcome.ASSERTION_FAILED)
    assert reclassify_unestablished(report, scenario, project) == []
    assert outcomes(report)["read"] == Outcome.ASSERTION_FAILED


def test_an_ordinary_failing_expectation_is_never_reclassified():
    """The fix is scoped to absence expectations; it cannot hide a normal status defect."""
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    scenario = Scenario(name="ordinary read", steps=[
        create_step(policies["createParcel"].id), read_step(policies["readParcel"].id)])
    report = executed_report(project, scenario, delete_outcome=Outcome.PASS,
                             read_outcome=Outcome.ASSERTION_FAILED)
    assert reclassify_unestablished(report, scenario, project) == []
    assert outcomes(report)["read"] == Outcome.ASSERTION_FAILED


def test_preflight_refuses_a_readback_that_expects_a_resource_it_deletes():
    """The mirror image, measured in P1's first real run.

    The plan deleted a parcel and then read it expecting ``200``; the target's correct ``404``
    was reported as ``ASSERTION_FAILED``. That accusation is the plan's, so it is refused
    before execution rather than executed and blamed on the service.
    """
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    scenario = Scenario(name="delete then expect present", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id),
        read_step(policies["readParcel"].id)])
    report = precondition_report(project, scenario)
    assert report["rows"][0]["direction"] == "existence"
    assert report["rows"][0]["status"] == "MISSING"
    assert report["rows"][0]["deletions"] == ["delete"]
    assert report["contradictions"] == ["read"]
    with pytest.raises(NeedsReview) as raised:
        validate_expectation_preconditions(project, scenario)
    assert raised.value.diagnostic.code == ABSENCE_PRECONDITION_MISSING


def test_a_readback_without_any_deletion_keeps_its_failure_as_evidence():
    """Create-then-read expecting 200 is the ordinary happy path and must stay judgeable."""
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    scenario = Scenario(name="create then read", steps=[
        create_step(policies["createParcel"].id), read_step(policies["readParcel"].id)])
    report = precondition_report(project, scenario)
    assert [row["status"] for row in report["rows"]] == ["ESTABLISHED"]
    assert report["rows"][0]["direction"] == "existence"
    assert report["contradictions"] == []


def test_a_readback_whose_deletion_failed_is_downgraded_to_review():
    """Situation 2 for the existence direction: the phase is unknown, so it is review."""
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    scenario = Scenario(name="failed delete then read", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id),
        read_step(policies["readParcel"].id)])
    from agenttest.report import CheckResult

    report = executed_report(project, scenario, delete_outcome=Outcome.TEST_INFRA_ERROR,
                             read_outcome=Outcome.ASSERTION_FAILED)
    for step in report.steps:
        if step.step_id == "read":
            step.checks = [CheckResult(kind="http_status", expected=[200], actual=404,
                                       passed=False)]
    changes = reclassify_unestablished(report, scenario, project)
    assert [item["step_id"] for item in changes] == ["read"]
    assert changes[0]["direction"] == "existence"
    assert changes[0]["deletion_passed"] is False
    assert changes[0]["reason"] == PRECONDITION_UNPROVEN
    assert outcomes(report)["read"] == Outcome.NEEDS_REVIEW


def test_a_readback_without_a_planned_deletion_stays_real_evidence():
    """Create-then-read that 404s is a genuine defect: nothing removed the resource."""
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    scenario = Scenario(name="create then read", steps=[
        create_step(policies["createParcel"].id), read_step(policies["readParcel"].id)])
    report = executed_report(project, scenario, delete_outcome=Outcome.PASS,
                             read_outcome=Outcome.ASSERTION_FAILED)
    assert reclassify_unestablished(report, scenario, project) == []
    assert outcomes(report)["read"] == Outcome.ASSERTION_FAILED
