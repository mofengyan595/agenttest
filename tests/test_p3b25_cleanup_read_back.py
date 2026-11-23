"""Cleanup read-back precheck: a deleted resource needs its approved proof of absence.

P3-B2.4 measured three plans (``createParcelB/C/D``) that created a resource, deleted it, and
stopped there. ``DELETE 204`` is not proof, an empty final inventory is not proof for one
resource, and a read that ran before the deletion proves the opposite. The runtime kept those
resources ``CLEANUP_UNPROVEN``; this round refuses the plan *before* execution when the target
actually has an approved absence read, and says exactly which proof is missing.

Everything here is offline: no model call, no network, no real target.
"""

import json
from dataclasses import replace

import pytest
from test_product_agent import session
from test_product_supervisor import lifecycle_project

from agenttest.case_diagnostics import FACTS, public_diagnostic
from agenttest.dsl import Scenario
from agenttest.model_input import diagnostic_view, safe_detail
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.product_preconditions import (
    ABSENCE_READ_BACK,
    AFTER_DELETE,
    CLEANUP_READ_BACK_MISSING,
    NO_APPROVED_READ,
    NO_DELETE,
    PROVEN,
    READ_BACK_MISSING,
    add_batch_cleanup,
    approved_absence_read,
    cleanup_read_back_report,
    validate_cleanup_read_back,
)
from agenttest.report import ReportStore
from agenttest.safety import ResourceLifecycle
from agenttest.write_effects import CLEANUP_UNPROVEN, classify

RULE = dict(create_operation="createParcel", delete_operation="deleteParcel",
            id_selector="$.id", path_parameter="parcelId",
            fresh_resource_basis="隔离靶场保证每次成功创建分配全新资源，不复用已有资源。")


# --------------------------------------------------------------------------- fixtures


def policies(project):
    return {policy.operation_id: policy for policy in project.registry.definitions()
            if policy.negative is None}


def parcels(*, absence):
    """The archived parcels shape: create/read/delete of one resource, with or without proof.

    ``absence=True`` adds the approval a cleanup proof needs: a ``GET /parcels/{parcelId}``
    policy whose reviewed expectation is ``404``. Without it no legal plan can prove cleanup,
    which is exactly the v1 configuration this round must not turn into a false rejection.
    """
    project, _ = lifecycle_project()
    target = project.target.model_copy(update={"resource_lifecycles": [
        ResourceLifecycle(**RULE)]})
    definitions = list(project.registry.definitions())
    if absence:
        read = next(policy for policy in definitions if policy.operation_id == "readParcel")
        definitions.append(read.model_copy(update=dict(
            id="web_rule_9",
            assertions=read.assertions.model_copy(update=dict(
                status=read.assertions.status.model_copy(update=dict(codes=[404])),
                source="operator: 删除后同一包裹必须回读 404")))))
    return replace(project, target=target, registry=PolicyRegistry(definitions))


def step(identifier, operation, policy_id, *, bound=True, select="$.id", from_step=None):
    return dict(id=identifier, operation_id=operation, role=None, policy_id=policy_id,
                path_parameters=({"parcelId": dict(from_step=from_step or bound,
                                                   select=select)} if bound else {}))


def scenario(*steps):
    return Scenario.model_validate(dict(name="parcels", steps=list(steps)))


def created_then_deleted(project):
    rules = policies(project)
    return scenario(
        step("create", "createParcel", rules["createParcel"].id, bound=False),
        step("delete", "deleteParcel", rules["deleteParcel"].id, from_step="create"))


def refusal(project, plan):
    with pytest.raises(NeedsReview) as caught:
        validate_cleanup_read_back(project, plan)
    return caught.value


# ------------------------------------------------------------------- the rule fires


def test_a_deleted_resource_without_its_proof_is_refused_with_locatable_facts():
    project = parcels(absence=True)
    rules = policies(project)
    plan = created_then_deleted(project)
    report = cleanup_read_back_report(project, plan)
    assert [row["status"] for row in report["rows"]] == [READ_BACK_MISSING]
    assert report["proven"] == [] and report["unprovable"] == []

    failure = refusal(project, plan)
    diagnostic = failure.diagnostic
    assert diagnostic.code == CLEANUP_READ_BACK_MISSING
    assert diagnostic.layer.value == "policy"
    # The repair attaches to the deletion: that is where the model has to add the read.
    assert diagnostic.fields == ["plan.steps.1.path_parameters.parcelId"]
    detail = diagnostic.detail
    assert detail["create_step"] == "create"
    assert detail["delete_step"] == "delete"
    assert detail["obligation_id"] == detail["read_policy"] == rules["readParcel"].id
    assert detail["lifecycle_create"] == "createParcel"
    assert detail["lifecycle_delete"] == "deleteParcel"
    assert detail["lifecycle_parameter"] == "parcelId"
    assert detail["proof_kind"] == ABSENCE_READ_BACK
    assert detail["required_position"] == AFTER_DELETE
    assert detail["missing"] == [rules["readParcel"].id]
    # The hint states the only legal repair, and refuses the two shortcuts by name.
    assert "删除步骤 delete 之后" in detail["hint"]
    assert "404/410" in detail["hint"] and "204" in detail["hint"]
    assert "库存" in detail["hint"]


def test_no_approved_absence_read_means_nothing_is_refused():
    """The v1 configuration: without 404 approval no legal read exists, so no refusal."""
    project = parcels(absence=False)
    plan = created_then_deleted(project)
    report = cleanup_read_back_report(project, plan)
    assert [row["status"] for row in report["rows"]] == [NO_APPROVED_READ]
    assert report["rows"][0]["read_policy"] is None
    assert report["missing"] == []
    # Returning the report (not raising) is the whole point: the runtime keeps the resource
    # CLEANUP_UNPROVEN instead of the precheck demanding an unapproved request.
    assert validate_cleanup_read_back(project, plan)["missing"] == []
    assert approved_absence_read(project, plan.steps[0].policy_id, None,
                                 project.target.resource_lifecycles[0],
                                 {op.operation_id: op for op in project.contract.operations},
                                 policies(project)) is None


def test_the_approved_post_delete_read_closes_the_resource():
    project = parcels(absence=True)
    rules = policies(project)
    plan = scenario(
        *created_then_deleted(project).steps,
        step("confirm", "readParcel", rules["readParcel"].id, from_step="create"))
    report = validate_cleanup_read_back(project, plan)
    assert [row["status"] for row in report["rows"]] == [PROVEN]
    assert report["rows"][0]["read_step"] == "confirm"


# ------------------------------------------------------- the boundaries that stay closed


def test_a_read_before_the_deletion_is_not_a_cleanup_proof():
    project = parcels(absence=True)
    rules = policies(project)
    plan = scenario(
        step("create", "createParcel", rules["createParcel"].id, bound=False),
        step("confirm", "readParcel", rules["readParcel"].id, from_step="create"),
        step("delete", "deleteParcel", rules["deleteParcel"].id, from_step="create"))
    assert [row["status"] for row in cleanup_read_back_report(project, plan)["rows"]] == [
        READ_BACK_MISSING]
    assert refusal(project, plan).diagnostic.code == CLEANUP_READ_BACK_MISSING


def test_a_read_of_another_resource_cannot_prove_this_one():
    """Two creations, both deleted, one proof: only the proven one is accepted."""
    project = parcels(absence=True)
    rules = policies(project)
    plan = scenario(
        step("createA", "createParcel", rules["createParcel"].id, bound=False),
        step("createB", "createParcel", rules["createParcel"].id, bound=False),
        step("deleteA", "deleteParcel", rules["deleteParcel"].id, from_step="createA"),
        step("deleteB", "deleteParcel", rules["deleteParcel"].id, from_step="createB"),
        step("confirmB", "readParcel", rules["readParcel"].id, from_step="createB"))
    report = cleanup_read_back_report(project, plan)
    statuses = {row["create_step"]: row["status"] for row in report["rows"]}
    assert statuses == {"createA": READ_BACK_MISSING, "createB": PROVEN}
    assert [row["create_step"] for row in report["missing"]] == ["createA"]
    assert refusal(project, plan).diagnostic.detail["create_step"] == "createA"


def test_a_read_approved_for_a_different_expectation_is_not_a_proof():
    """A 200 read-back is an ordinary read: it cannot stand in for the absence proof."""
    project = parcels(absence=False)
    rules = policies(project)
    plan = scenario(
        *created_then_deleted(project).steps,
        step("confirm", "readParcel", rules["readParcel"].id, from_step="create"))
    report = cleanup_read_back_report(project, plan)
    assert [row["status"] for row in report["rows"]] == [NO_APPROVED_READ]
    assert report["proven"] == []


def test_a_creation_without_a_deletion_is_a_different_gap():
    project = parcels(absence=True)
    rules = policies(project)
    plan = scenario(step("create", "createParcel", rules["createParcel"].id, bound=False))
    report = cleanup_read_back_report(project, plan)
    assert [row["status"] for row in report["rows"]] == [NO_DELETE]
    validate_cleanup_read_back(project, plan)      # cleanup selection is another rule's job


def test_read_only_plans_and_projects_without_lifecycles_are_untouched():
    plain = parcels(absence=True)
    rules = policies(plain)
    only_read = scenario(step("read", "readParcel", rules["readParcel"].id, bound=False))
    assert cleanup_read_back_report(plain, only_read)["rows"] == []
    no_lifecycle = replace(plain, target=plain.target.model_copy(
        update={"resource_lifecycles": []}))
    assert cleanup_read_back_report(no_lifecycle, created_then_deleted(plain))["rows"] == []


def test_a_complete_plan_stays_legal_and_the_proof_is_added_when_missing():
    """`add_batch_cleanup` already completes a delete-only plan when a proof is approved."""
    project = parcels(absence=True)
    plan = created_then_deleted(project)
    completed = add_batch_cleanup(project, plan)
    added = [item.id for item in completed.steps[len(plan.steps):]]
    assert added and added[0].startswith("batch_cleanup_read_")
    report = validate_cleanup_read_back(project, completed)
    assert [row["status"] for row in report["rows"]] == [PROVEN]


def test_the_report_is_deterministic():
    project = parcels(absence=True)
    plan = created_then_deleted(project)
    assert cleanup_read_back_report(project, plan) == cleanup_read_back_report(project, plan)


def test_the_design_path_refuses_before_anything_is_dispatched(tmp_path):
    """The real trigger point: a proposed case, compiled and checked, never dispatched."""
    from agenttest.product_design import DesignCase, validate_design_case

    project = parcels(absence=True)
    rules = policies(project)
    proposed = DesignCase.model_validate(dict(
        title="创建后清理", kind="normal",
        plan=dict(name="parcels", steps=[
            dict(id="create", operation_id="createParcel", role=None,
                 policy_id=rules["createParcel"].id, arguments=[
                     dict(location="body", name="name", value="cleanup",
                          from_step=None, select=None),
                     dict(location="body", name="quantity", value=1,
                          from_step=None, select=None)]),
            dict(id="delete", operation_id="deleteParcel", role=None,
                 policy_id=rules["deleteParcel"].id, arguments=[
                     dict(location="path_parameters", name="parcelId", value=None,
                          from_step="create", select="$.id")])])))
    with pytest.raises(NeedsReview) as caught:
        validate_design_case(project, proposed, ReportStore(tmp_path / "reports"), environ={})
    assert caught.value.diagnostic.code == CLEANUP_READ_BACK_MISSING
    assert caught.value.diagnostic.detail["delete_step"] == "delete"
    # Nothing was written: no report, no run directory.
    assert list((tmp_path / "reports").glob("**/*")) == []


# ------------------------------------------------- send nothing, and keep the audit raw


def test_the_runtime_still_judges_the_same_unproven_cleanup():
    """The precheck is new; the runtime's cleanup verdict and its basis are unchanged."""
    step_row = dict(step_id="create", policy_id="web_rule_1", operation_id="createParcel",
                    method="POST", executed=True, outcome="PASS", status=201,
                    request=dict(values={}), checks=[])
    row = dict(create_step="create", cleanup_step="delete", status="CLEANED",
               cleanup_result="PASS")
    effect = classify(case=dict(evidence=[step_row], absence_expectations=[]),
                      step=step_row, lifecycle_row=row, policies={})
    assert effect["status"] == CLEANUP_UNPROVEN
    assert effect["cleanup_proven"] is False and effect["replay_allowed"] is False
    assert effect["diagnostic"]["basis"] == "DELETE_RECEIPT_WITHOUT_READ_BACK"


@pytest.mark.asyncio
async def test_the_refusal_reaches_the_model_without_any_business_request(tmp_path):
    project = parcels(absence=True)
    plan = created_then_deleted(project)
    diagnostic = refusal(project, plan).diagnostic.model_dump(mode="json")
    stored = json.loads(json.dumps(diagnostic))

    session_, tasks, _project, _policies, summary = session(tmp_path)

    def rejected(summary, group, supervision):
        summary["design_records"].append(dict(
            group_id=group["id"], status="REJECTED", reason=CLEANUP_READ_BACK_MISSING,
            diagnostic=stored))
        group["status"] = "NOT_EXECUTED"
        group["reason"] = CLEANUP_READ_BACK_MISSING

    tasks.effect = rejected
    payload = await session_.run_group("group_1")

    error = payload["error"]
    assert payload["ok"] is False
    assert error["code"] == CLEANUP_READ_BACK_MISSING
    assert error["fields"] == ["plan.steps.1.path_parameters.parcelId"]
    assert error["detail"]["create_step"] == "create"
    assert error["detail"]["delete_step"] == "delete"
    assert error["detail"]["proof_kind"] == ABSENCE_READ_BACK
    assert error["detail"]["required_position"] == AFTER_DELETE
    assert error["retryable"] is True
    assert "缺失回读" in error["hint"]
    assert payload["requests"] == 0 and session_.ledger.requests == 0
    # The model reads a projection; the stored record keeps every raw fact for audit.
    assert session_.summary["design_records"][-1]["diagnostic"] == stored
    view = diagnostic_view(stored)
    detail, _omitted = safe_detail(stored["detail"])
    assert view["detail"] == detail
    assert detail["obligation_id"] == error["detail"]["obligation_id"]


def test_every_new_fact_survives_the_case_whitelist_and_drops_secrets():
    project = parcels(absence=True)
    plan = created_then_deleted(project)
    diagnostic = refusal(project, plan).diagnostic.model_dump(mode="json")
    assert {"create_step", "delete_step", "read_policy", "lifecycle_create", "lifecycle_delete",
            "lifecycle_parameter", "proof_kind", "required_position"} <= set(FACTS)
    public = public_diagnostic(dict(code=CLEANUP_READ_BACK_MISSING, layer="policy",
                                   fields=diagnostic["fields"], detail=diagnostic["detail"]))
    assert public["detail"]["create_step"] == "create"
    assert public["detail"]["proof_kind"] == ABSENCE_READ_BACK
    poisoned = dict(diagnostic)
    poisoned["detail"] = dict(diagnostic["detail"],
                              create_step="AKIAauditSYNTHETIC0001",
                              read_policy="Authorization: Bearer auditSYNTHETICtoken",
                              delete_step="审计测试包裹B")
    kept, omitted = safe_detail(poisoned["detail"])
    assert "create_step" not in kept and "read_policy" not in kept
    assert "delete_step" not in kept
    assert omitted["create_step"] == 1 and omitted["read_policy"] == 1
    assert kept["proof_kind"] == ABSENCE_READ_BACK      # owned enum, still delivered
    assert "404/410" in kept["hint"]                    # the repair sentence is not a value
