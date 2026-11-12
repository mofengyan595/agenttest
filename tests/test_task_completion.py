"""Strict, auditable completion: termination is not evidence and evidence is not completion."""

import pytest
from test_product_supervisor import lifecycle_project

from agenttest.policies import PolicyRegistry
from agenttest.task_completion import (
    FUNCTIONAL,
    INVESTIGATION,
    PRE_RELEASE,
    REGRESSION,
    classify_task,
    evaluate,
    public_view,
    subject_scope,
)

GOALS = {
    "帮我测一下包裹管理功能。": FUNCTIONAL,
    "删除功能刚改过，帮我回归一下。": REGRESSION,
    "这个服务准备上线了，帮我测一轮。": PRE_RELEASE,
    "包裹接口偶尔报错，帮我看看。": INVESTIGATION,
}

OPS = ("createParcel", "readParcel", "deleteParcel")


def project_with_absence():
    from dataclasses import replace

    project, _ = lifecycle_project()
    definitions = list(project.registry.definitions())
    read = next(policy for policy in definitions if policy.operation_id == "readParcel")
    absence = read.model_copy(update=dict(
        id="web_rule_7",
        assertions=read.assertions.model_copy(update=dict(
            status=read.assertions.status.model_copy(update=dict(codes=[404])),
            source="operator: after deleteParcel the same parcel must answer 404"))))
    return replace(project, registry=PolicyRegistry(definitions + [absence]))


def policy_of(project, operation_id):
    return next(policy.id for policy in project.registry.definitions()
                if policy.operation_id == operation_id and policy.negative is None)


def summary_for(project, *, outcome="PASS", measured=OPS, lifecycle=True, questions=(),
                group_status="DESIGNED", absence_satisfied=False, executed=OPS):
    steps = []
    for operation_id in measured:
        policy_id = policy_of(project, operation_id)
        failed = outcome == "ASSERTION_FAILED" and operation_id == "readParcel"
        checks = [dict(kind="status", selector="status",
                       expected=404 if failed else 201 if operation_id == "createParcel" else 200,
                       actual=200 if failed else 201 if operation_id == "createParcel" else 200,
                       passed=not failed)]
        steps.append(dict(operation_id=operation_id, policy_id=policy_id, executed=True,
                          outcome="ASSERTION_FAILED" if failed else "PASS", checks=checks))
    absence_rows = []
    if absence_satisfied:
        # The absence policy is an ordinary approved obligation: it needs its own measured
        # execution, not just a flag on the case.
        absence_rows = [dict(policy_id="web_rule_7", satisfied=True)]
        steps.append(dict(operation_id="readParcel", policy_id="web_rule_7", executed=True,
                          outcome="PASS",
                          checks=[dict(kind="status", selector="status", expected=404,
                                       actual=404, passed=True)]))
    case = dict(case_id="case_1", run_id="run_1", status="FINISHED", outcome=outcome,
                executed_operations=list(executed), executed_steps=len(steps),
                planned_steps=len(steps), http_requests=3, evidence=steps,
                absence_expectations=absence_rows,
                resource_lifecycle=([dict(create_step="step_1", status="CLEANED",
                                          cleanup_result="PASS")] if lifecycle else []))
    return dict(groups=[dict(id="group_1", primary=list(OPS), operations=list(OPS),
                             supported=True, status=group_status)],
                inventory=dict(total=len(OPS), operations=[
                    dict(operation_id=name, state="READY", reason="", next_step="")
                    for name in OPS]),
                cases=[case] if measured else [], design_records=[],
                questions=list(questions), not_covered=[])


def criterion(report, identifier):
    return next(row for row in report["criteria"] if row["id"] == identifier)


@pytest.mark.parametrize("text,kind", GOALS.items())
def test_the_goal_text_decides_the_completion_kind(text, kind):
    classification = classify_task(text)
    assert classification["kind"] == kind
    assert classification["reason"] and classification["cues"]


def test_a_regression_subject_follows_approved_bindings_not_endpoint_counts():
    project, _ = lifecycle_project()
    subject = subject_scope(project, "删除功能刚改过，帮我回归一下。", REGRESSION)
    assert subject["resolved"] is True
    assert subject["operations"] == sorted(OPS)
    assert policy_of(project, "deleteParcel") in subject["policy_ids"]
    assert policy_of(project, "createParcel") in subject["policy_ids"]


def test_a_complete_functional_round_requires_every_obligation_and_a_real_lifecycle():
    project, _ = lifecycle_project()
    summary = summary_for(project)
    report = evaluate(project, summary, "帮我测一下包裹管理功能。",
                      stop=dict(reason="ANSWER"))
    assert report["complete"] is True
    assert report["checks_passed"] is True
    assert report["unmet"] == []
    assert report["coverage"] == dict(obligations=3, measured=3, missing=0, blocked=0,
                                      operations=sorted(OPS))
    assert criterion(report, "WRITE_LIFECYCLE_EXERCISED")["status"] == "MET"


def test_one_unexercised_obligation_keeps_the_task_incomplete():
    project, _ = lifecycle_project()
    summary = summary_for(project, measured=("createParcel", "readParcel"))
    report = evaluate(project, summary, "帮我测一下包裹管理功能。",
                      stop=dict(reason="ANSWER"))
    assert report["complete"] is False
    assert report["flags"]["evidence_produced"] is True
    assert report["flags"]["obligations_covered"] is False
    assert "SCOPE_OBLIGATIONS_MEASURED" in report["unmet"]
    assert report["pending"] == [dict(item="obligation", detail=policy_of(project, "deleteParcel"),
                                      operation_id="deleteParcel")]


def test_a_measured_assertion_failure_is_complete_testing_and_a_reported_anomaly():
    project, _ = lifecycle_project()
    summary = summary_for(project, outcome="ASSERTION_FAILED")
    report = evaluate(project, summary, "帮我测一下包裹管理功能。",
                      stop=dict(reason="ANSWER"))
    assert report["complete"] is True
    assert report["checks_passed"] is False
    assert report["flags"]["anomalies_found"] is True
    assert report["anomalies"]["confirmed"][0]["failed"][0]["kind"] == "status"
    assert criterion(report, "ANOMALIES_EVIDENCED")["status"] == "MET"
    assert public_view(report)["checks_passed"] is False


def test_no_executed_request_is_never_reported_as_a_business_defect():
    project, _ = lifecycle_project()
    summary = summary_for(project, measured=OPS, outcome="PASS")
    summary["cases"] = [dict(case_id="case_pre", run_id=None, status="NOT_EXECUTED",
                             outcome="TEST_INFRA_ERROR", executed_operations=[],
                             executed_steps=0, planned_steps=2, http_requests=0,
                             evidence=[], reason="PLAN_PREFLIGHT_REJECTED")]
    report = evaluate(project, summary, "帮我测一下包裹管理功能。",
                      stop=dict(reason="ANSWER"))
    assert report["complete"] is False
    assert report["flags"]["evidence_produced"] is False
    assert report["anomalies"]["confirmed"] == []
    assert report["anomalies"]["unconfirmed"][0]["outcome"] == "TEST_INFRA_ERROR"
    assert "EVIDENCE_PRODUCED" in report["unmet"]


def test_regression_requires_the_delete_and_an_approved_absence_read():
    plain, _ = lifecycle_project()
    without_policy = evaluate(plain, summary_for(plain), "删除功能刚改过，帮我回归一下。",
                              stop=dict(reason="ANSWER"))
    assert without_policy["complete"] is True
    assert criterion(without_policy, "SUBJECT_DELETE_OBSERVED")["status"] == "MET"
    assert criterion(without_policy, "POST_DELETE_ABSENCE_VERIFIED")["status"] == "EXEMPT"

    project = project_with_absence()
    unverified = evaluate(project, summary_for(project), "删除功能刚改过，帮我回归一下。",
                          stop=dict(reason="ANSWER"))
    assert unverified["complete"] is False
    assert "POST_DELETE_ABSENCE_VERIFIED" in unverified["unmet"]

    verified = evaluate(project, summary_for(project, absence_satisfied=True),
                        "删除功能刚改过，帮我回归一下。", stop=dict(reason="ANSWER"))
    assert verified["complete"] is True
    assert criterion(verified, "POST_DELETE_ABSENCE_VERIFIED")["status"] == "MET"


def test_budget_exhaustion_terminates_the_agent_without_completing_the_task():
    project, _ = lifecycle_project()
    summary = summary_for(project, measured=("createParcel",))
    report = evaluate(project, summary, "帮我测一下包裹管理功能。",
                      stop=dict(reason="MODEL_CALL_LIMIT"))
    assert report["flags"]["agent_terminated"] is True
    assert report["flags"]["task_complete"] is False
    assert report["complete"] is False
    assert report["pending"]


def test_pre_release_needs_every_group_decided_and_no_open_question():
    project, _ = lifecycle_project()
    undecided = summary_for(project, group_status="PENDING")
    report = evaluate(project, undecided, "这个服务准备上线了，帮我测一轮。",
                      stop=dict(reason="ANSWER"))
    assert report["complete"] is False
    assert "ALL_GROUPS_DECIDED" in report["unmet"]

    questioned = summary_for(project, questions=[dict(
        operation_id="readParcel", question="删除后应返回什么状态码？")])
    report = evaluate(project, questioned, "这个服务准备上线了，帮我测一轮。",
                      stop=dict(reason="ANSWER"))
    assert report["complete"] is False
    assert "NO_OPEN_QUESTIONS" in report["unmet"]


def test_an_investigation_is_complete_when_the_subject_was_actually_probed():
    project, _ = lifecycle_project()
    summary = summary_for(project, measured=("readParcel",), executed=("readParcel",))
    report = evaluate(project, summary, "包裹接口偶尔报错，帮我看看。",
                      stop=dict(reason="ANSWER"))
    assert report["kind"] == INVESTIGATION
    assert report["complete"] is True
    assert criterion(report, "SUBJECT_REPRODUCED")["status"] == "MET"
    assert criterion(report, "OBSERVATIONS_RECORDED")["status"] == "MET"
    assert criterion(report, "INVESTIGATION_BOUNDARY")["status"] == "MET"
    boundary = criterion(report, "INVESTIGATION_BOUNDARY")["evidence"]
    assert boundary["probed"] == ["readParcel"]
    assert boundary["unprobed"] == ["createParcel", "deleteParcel"]
    assert boundary["out_of_scope"] == []
    assert report["checks_passed"] is True


def test_an_investigation_without_a_request_proves_nothing():
    project, _ = lifecycle_project()
    summary = summary_for(project, measured=(), executed=())
    report = evaluate(project, summary, "包裹接口偶尔报错，帮我看看。",
                      stop=dict(reason="ANSWER"))
    assert report["complete"] is False
    assert "SUBJECT_REPRODUCED" in report["unmet"]
    assert report["flags"]["evidence_produced"] is False


def test_an_executed_create_without_a_cleanup_receipt_is_pending_cleanup():
    """Ten pre-release parcels leaked while completion reported "created 2, pending 0"."""
    project, _ = lifecycle_project()
    summary = summary_for(project, lifecycle=False)
    summary["cases"][0]["evidence"][0].update(
        method="POST", request=dict(method="POST", path="/parcels"))
    report = evaluate(project, summary, "帮我测一下包裹管理功能。", stop=dict(reason="ANSWER"))
    row = criterion(report, "RESOURCES_RESOLVED")
    assert row["status"] == "UNMET"
    assert row["evidence"] == dict(created=1, pending=1, lifecycle=0, unproven=1)
    assert "没有清理凭据" in row["detail"]
    assert report["complete"] is False
    assert any(item["item"] == "resource" for item in report["pending"])
