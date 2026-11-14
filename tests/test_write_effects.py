"""Write outcomes are classified from evidence, never from a status code alone."""

from types import SimpleNamespace

from test_task_completion import project_with_absence, summary_for

from agenttest.task_completion import _created_resources, evaluate
from agenttest.write_effects import (
    CLEANUP_UNPROVEN,
    CREATED_AND_CLEANED,
    CREATED_CLEANUP_FAILED,
    CREATION_UNPROVEN,
    NO_SIDE_EFFECT_PROVEN,
    WRITE_OUTCOME_UNKNOWN,
    analyse,
    for_case,
)


def policy(*, negative=False, sources=()):
    return SimpleNamespace(negative=object() if negative else None,
                           binding_rules=[SimpleNamespace(source_policy_id=item)
                                          for item in sources])


def step(**overrides):
    row = dict(operation_id="createParcel", method="POST", step_id="create",
               policy_id="create_parcel", executed=True,
               request=dict(method="POST", path="/parcels"), outcome="PASS")
    row.update(overrides)
    return row


def case(*, steps=(), lifecycle=(), absence=()):
    return dict(case_id="case-1", run_id="run-1", status="FINISHED", http_requests=1,
                evidence=list(steps), resource_lifecycle=list(lifecycle),
                absence_expectations=list(absence), outcome="PASS")


def test_an_approved_rejection_without_a_read_back_is_a_write_not_a_resource():
    """The P3-A.1 defect: an expected 422 POST became CLEANUP_UNPROVEN as if it created."""
    row = case(steps=[step(policy_id="negative_parcel")])
    effects = analyse([row], {"negative_parcel": policy(negative=True)})
    assert effects["counts"][CREATION_UNPROVEN] == 1
    assert effects["pending"] == []
    assert effects["created"] == 0
    assert effects["unresolved"][0]["basis"][-1] == "APPROVED_REJECTION_OBSERVED"
    assert effects["unresolved"][0]["replay_allowed"] is False
    summary = {"cases": [dict(row, write_effects=effects["rows"])]}
    resources = _created_resources(summary)
    assert resources["total"] == 0
    assert resources["pending"] == []
    assert resources["creation_unproven"] == 1


def test_a_rejection_with_an_approved_read_back_proves_no_side_effect():
    row = case(steps=[step(policy_id="negative_parcel", status=422)],
               absence=[dict(policy_id="parcel_absence", satisfied=True)])
    effects = analyse([row], {"negative_parcel": policy(negative=True),
                              "parcel_absence": policy(sources=["negative_parcel"])})
    assert effects["counts"][NO_SIDE_EFFECT_PROVEN] == 1
    assert effects["created"] == 0


def test_a_created_and_read_back_absent_resource_is_cleaned():
    row = case(steps=[step()],
               lifecycle=[dict(create_step="create", cleanup_step="delete",
                               status="CLEANED", cleanup_result="PASS")],
               absence=[dict(policy_id="parcel_absence", satisfied=True)])
    effects = analyse([row], {"create_parcel": policy(),
                              "delete_parcel": policy(sources=["create_parcel"]),
                              "parcel_absence": policy(sources=["delete_parcel"])})
    assert effects["counts"][CREATED_AND_CLEANED] == 1
    assert effects["pending"] == []


def test_a_delete_receipt_without_a_read_back_is_not_a_cleanup_proof():
    """``delete_noop`` answers 204 and keeps the record; 204 alone must not read as clean."""
    row = case(steps=[step()],
               lifecycle=[dict(create_step="create", cleanup_step="delete",
                               status="CLEANED", cleanup_result="PASS")])
    effects = analyse([row], {"create_parcel": policy(),
                              "delete_parcel": policy(sources=["create_parcel"])})
    assert effects["counts"][CLEANUP_UNPROVEN] == 1
    assert effects["pending"][0]["diagnostic"]["basis"] == "DELETE_RECEIPT_WITHOUT_READ_BACK"


def test_a_failed_cleanup_is_distinguished_from_an_unproven_one():
    row = case(steps=[step()],
               lifecycle=[dict(create_step="create", cleanup_step="delete",
                               status="CLEANUP_PENDING", cleanup_result="ASSERTION_FAILED")])
    effects = analyse([row], {"create_parcel": policy(),
                              "delete_parcel": policy(sources=["create_parcel"])})
    assert effects["counts"][CREATED_CLEANUP_FAILED] == 1


def test_an_unknown_write_is_never_marked_for_replay():
    row = case(steps=[step(error_code="ATTEMPT_OUTCOME_UNKNOWN", outcome="FAIL",
                           request=dict(method="POST", path="/parcels"))],
               lifecycle=[dict(create_step="create", status="CREATE_OUTCOME_UNKNOWN",
                               cleanup_result="NOT_SENT")])
    effects = analyse([row], {"create_parcel": policy()})
    assert effects["counts"][WRITE_OUTCOME_UNKNOWN] == 1
    assert effects["replayed"] == []
    assert effects["unresolved"][0]["replay_allowed"] is False


def test_a_write_that_was_never_sent_has_provenly_no_side_effect():
    row = case(steps=[step(executed=False, request=None)])
    effects = analyse([row], {"create_parcel": policy()})
    assert effects["counts"][NO_SIDE_EFFECT_PROVEN] == 1
    assert effects["rows"][0]["basis"][-1] == "NOT_SENT"


def test_an_executed_write_without_a_receipt_is_pending_not_clean():
    row = case(steps=[step()])
    effects = analyse([row], {"create_parcel": policy()})
    assert effects["counts"][CLEANUP_UNPROVEN] == 1
    assert effects["pending"][0]["basis"][-1] == "EXECUTED_WRITE_WITHOUT_RECEIPT"


def test_case_effects_are_recorded_on_the_case_and_aggregated():
    project = project_with_absence()
    summary = summary_for(project)
    case_row = summary["cases"][0]
    methods = {"createParcel": "POST", "readParcel": "GET", "deleteParcel": "DELETE"}
    for step_row in case_row["evidence"]:
        step_row["step_id"] = "step_" + step_row["operation_id"]
        step_row["method"] = methods[step_row["operation_id"]]
        step_row["request"] = dict(method=step_row["method"], path="/parcels")
    case_row["resource_lifecycle"] = [dict(create_step="step_createParcel",
                                           cleanup_step="step_deleteParcel",
                                           status="CLEANED", cleanup_result="PASS")]
    case_row["absence_expectations"] = [dict(policy_id="web_rule_7", satisfied=True)]
    policies = {p.id: p for p in project.registry.definitions()}
    case_row["write_effects"] = for_case(case_row, policies)
    assert any(row["status"] == CREATED_AND_CLEANED
               for row in case_row["write_effects"]), [
        (row["status"], row["basis"]) for row in case_row["write_effects"]]
    report = evaluate(project, summary, "帮我测一下包裹管理功能。", stop=dict(reason="ANSWER"))
    assert report["write_effects"]["schema"] == "agenttest.write-effects.v1"
    assert report["write_effects"]["counts"][CREATED_AND_CLEANED] == 1
