"""A retest must add information; a semantically identical repeat must not run writes.

P0's second confirmed defect: the supervisor produced follow-up designs with new target ids
that were the same test, so the agent re-created and re-deleted a parcel and only reproduced
the earlier conclusion. These tests pin the deterministic rule that replaces "the model said
it is different": the fingerprint ignores ids and titles, so an equivalent plan is refused
before any HTTP, while a real change (a fixed precondition, another operation, another role,
another approved expectation) is allowed through.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_product_preconditions import (  # noqa: E402
    binding,
    create_step,
    delete_step,
    lifecycle,
    lifecycle_project,
    read_step,
)

from agenttest.dsl import Scenario, Step  # noqa: E402
from agenttest.product_fingerprint import (  # noqa: E402
    duplicate_attempt,
    failure_signature,
    scenario_fingerprint,
    scenario_shape,
)
from agenttest.product_preconditions import precondition_report  # noqa: E402
from agenttest.report import Outcome  # noqa: E402


def lifecycle_plan(project, *, read_from="create", delete_from="create"):
    policies = lifecycle(project)
    return Scenario(name="创建、删除后读取", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id, from_step=delete_from),
        read_step(policies["readParcel"].id, from_step=read_from)])


def test_the_fingerprint_ignores_step_ids_and_case_titles():
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    first = Scenario(name="场景一", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id),
        read_step(policies["readParcel"].id)])
    # Same semantics, every id and the title changed: a rename is not a new test.
    renamed = Scenario(name="场景二", steps=[
        create_step(policies["createParcel"].id, identifier="make", name="parcel-z"),
        Step(id="remove", operation_id="deleteParcel", role=None,
             policy_id=policies["deleteParcel"].id,
             path_parameters={"parcelId": binding("make")}),
        read_step(policies["readParcel"].id, from_step="make", identifier="fetch")])
    assert scenario_fingerprint(project, first) == scenario_fingerprint(project, renamed)


def test_a_different_operation_order_is_a_different_test():
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    forward = lifecycle_plan(project)
    reordered = Scenario(name="先读后删", steps=[
        create_step(policies["createParcel"].id),
        read_step(policies["readParcel"].id),
        delete_step(policies["deleteParcel"].id)])
    assert scenario_fingerprint(project, forward) != scenario_fingerprint(project, reordered)


def test_a_different_resource_binding_is_a_different_test():
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    same = lifecycle_plan(project)
    # The read claims a resource the delete cleaned: a genuinely different scenario.
    crossed = Scenario(name="交叉资源", steps=[
        create_step(policies["createParcel"].id),
        create_step(policies["createParcel"].id, identifier="create2", name="parcel-b"),
        delete_step(policies["deleteParcel"].id, from_step="create2"),
        read_step(policies["readParcel"].id, from_step="create")])
    assert scenario_fingerprint(project, same) != scenario_fingerprint(project, crossed)


def test_a_different_role_is_a_different_test():
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    anonymous = lifecycle_plan(project)
    authorised = Scenario(name="以登录身份读取", steps=[
        create_step(policies["createParcel"].id),
        delete_step(policies["deleteParcel"].id),
        Step(id="read", operation_id="readParcel", role="admin",
             policy_id=policies["readParcel"].id,
             path_parameters={"parcelId": binding("create")})])
    assert scenario_fingerprint(project, anonymous) != scenario_fingerprint(project, authorised)


def test_fixing_the_missing_precondition_changes_the_fingerprint(monkeypatch):
    project, _ = lifecycle_project()
    policies = lifecycle(project)
    monkeypatch.setattr("agenttest.product_preconditions.ABSENCE_STATUSES", frozenset({200}))
    without_delete = Scenario(name="缺少删除", steps=[
        create_step(policies["createParcel"].id), read_step(policies["readParcel"].id)])
    with_delete = lifecycle_plan(project)
    broken = scenario_fingerprint(project, without_delete,
                                  precondition_report(project, without_delete))
    repaired = scenario_fingerprint(project, with_delete,
                                    precondition_report(project, with_delete))
    # This is the whole point of the rule: the repaired plan is *not* the old failing plan,
    # so the round that fixes the precondition is never suppressed as a duplicate.
    assert broken != repaired


def case_row(identifier, digest, outcome, signature=None):
    return dict(id=identifier, scenario_fingerprint=digest, outcome=outcome,
                failure_signature=signature or [])


def test_a_failed_equivalent_plan_is_refused_but_a_passing_one_is_not():
    cases = [case_row("c1", "abc", "ASSERTION_FAILED", ["readParcel|http_status|None|200|404"])]
    assert duplicate_attempt(cases, "abc") is not None
    # Nothing to suppress when the plan changed...
    assert duplicate_attempt(cases, "def") is None
    # ...and a closed (passing) obligation is not a reason to refuse anything either.
    assert duplicate_attempt([case_row("c2", "abc", "PASS")], "abc") is None


def test_a_skipped_duplicate_is_not_itself_a_duplicate_source():
    cases = [case_row("c1", "abc", "ASSERTION_FAILED"),
             dict(id="c2", scenario_fingerprint="abc", outcome=None,
                  status="SKIPPED_DUPLICATE")]
    match = duplicate_attempt(cases, "abc")
    assert match is not None and match["case_id"] == "c1"


def test_the_shape_is_replay_stable_and_carries_the_approved_expectation():
    project, _ = lifecycle_project()
    plan = lifecycle_plan(project)
    shape = scenario_shape(project, plan, precondition_report(project, plan))
    assert shape["order"] == ["createParcel", "deleteParcel", "readParcel"]
    read = shape["steps"][2]
    assert read["phase"] == "EXISTS"
    assert read["expectation"]["statuses"] == [200]
    assert read["bindings"] == [["path_parameters", "parcelId", 0, "$.id"]]
    # The delete's phase is documented, so a plan that drops it cannot look identical.
    assert shape["steps"][1]["phase"] == "DELETED"


def test_failure_signature_names_what_actually_failed():
    from agenttest.report import AttemptEvidence, RunReport, StepResult

    check = dict(kind="http_status", selector=None, expected="200", actual="404", passed=False,
                 detail=None)
    step = StepResult(step_id="read", step_run_id="read", operation_id="readParcel",
                      method="GET", path_template="/parcels/{parcelId}",
                      outcome=Outcome.ASSERTION_FAILED, reason="POLICY_EVALUATED",
                      policy_sha256="0" * 64, approved_violation_codes=[],
                      checks=[check], attempts=[AttemptEvidence(
                          attempt=1, attempt_id="a1", status_code=404, elapsed_ms=1.0,
                          response_bytes=2)])
    report = RunReport(run_id="r", started_at="2026-11-11T00:00:00Z",
                       scenario_sha256="0" * 64, contract_sha256="0" * 64,
                       environment="isolated", target_origin="http://127.0.0.1:18998",
                       outcome=Outcome.ASSERTION_FAILED, steps=[step], workflow_nodes=[])
    assert failure_signature(report) == ["readParcel|http_status|None|200|404"]
