"""The four scopes are recorded separately; a narrowed selection cannot fake coverage."""

from test_task_completion import lifecycle_project

from agenttest.product_design import design_groups
from agenttest.scope_ledger import build, completion_rows, public_view


def focused_summary(project, *, operations):
    groups = design_groups(project, operation_ids=list(operations))
    return dict(user_task="帮我测一下包裹管理功能。", groups=groups,
                focus=dict(mode="selected", operation_ids=list(operations),
                           interpretation="只测读取包裹", not_covered=[]),
                inventory=dict(total=3, operations=[
                    dict(operation_id=name, state="READY", reason="", next_step="")
                    for name in ("createParcel", "readParcel", "deleteParcel")]),
                cases=[])


def test_a_narrowed_selection_is_reported_against_the_approved_scope():
    project, _ = lifecycle_project()
    ledger = build(project, focused_summary(project, operations=["readParcel"]),
                   "帮我测一下包裹管理功能。")
    assert ledger["schema"] == "agenttest.scope-ledger.v1"
    assert ledger["approved"]["count"] >= ledger["selected"]["count"] == 1
    assert ledger["coverage"]["selected"] == dict(measured=0, total=1, ratio=0.0)
    assert ledger["coverage"]["approved"]["total"] == ledger["approved"]["count"]
    # A requested interface that Focus dropped is never silent.
    assert [item["policy_id"] for item in ledger["unexplained"]]
    rows = {row["id"]: row for row in completion_rows(ledger)}
    assert rows["REQUESTED_SCOPE_SELECTED"]["status"] == "UNMET"
    assert rows["APPROVED_SCOPE_COVERAGE"]["status"] == "EXEMPT"
    assert "另有 2 项已批准义务不在本轮选中范围" in rows["APPROVED_SCOPE_COVERAGE"]["detail"]


def test_a_selection_that_covers_the_request_has_no_unexplained_gap():
    project, _ = lifecycle_project()
    operations = [operation.operation_id for operation in project.contract.operations
                  if operation.operation_id in project.target.allowed_operations]
    ledger = build(project, focused_summary(project, operations=operations),
                   "帮我测一下包裹管理功能。")
    assert ledger["unexplained"] == []
    rows = {row["id"]: row for row in completion_rows(ledger)}
    assert rows["REQUESTED_SCOPE_SELECTED"]["status"] == "MET"


def test_a_blocked_operation_is_excluded_with_a_reason():
    project, _ = lifecycle_project()
    summary = focused_summary(project, operations=["readParcel"])
    summary["inventory"]["operations"] = [
        dict(operation_id="createParcel", state="BLOCKED", reason="缺少身份",
             next_step="配置身份"),
        dict(operation_id="readParcel", state="READY", reason="", next_step=""),
        dict(operation_id="deleteParcel", state="READY", reason="", next_step="")]
    summary["focus"]["blocked_operation_ids"] = ["createParcel"]
    ledger = build(project, summary, "帮我测一下包裹管理功能。")
    reasons = {item["policy_id"]: item["reason"] for item in ledger["excluded"]}
    assert "FOCUS_BLOCKED" in reasons.values()


def test_no_selection_means_no_selected_scope_and_an_unresolved_request():
    project, _ = lifecycle_project()
    summary = dict(user_task="随便看看", groups=[], focus=dict(mode="needs_information"),
                   inventory=dict(total=3, operations=[]), cases=[])
    ledger = build(project, summary, "随便看看")
    assert ledger["selected"]["count"] == 0
    assert ledger["coverage"]["selected"]["ratio"] is None
    rows = {row["id"]: row for row in completion_rows(ledger)}
    # An unresolvable request is reported, never treated as a justified narrowing.
    assert rows["REQUESTED_SCOPE_SELECTED"]["status"] in {"EXEMPT", "UNMET"}
    view = public_view(ledger)
    assert view["approved"]["count"] == ledger["approved"]["count"]
    assert view["selected"]["mode"] == "needs_information"


def test_a_regression_goal_is_not_rejected_for_an_unnamed_sibling_operation():
    """'删除功能刚改过' names delete; dropping PATCH/PUT is not a silent narrowing.

    The deterministic subject closure of the frozen lab lists every approved policy of the
    parcel resource for a regression goal, so the rule is checked directly: an operation the
    goal named blocks, a sibling operation does not.
    """
    from agenttest.scope_ledger import _exclusion_reason

    row = dict(policy_id="web_rule_5", operation_id="changeParcel", kind="STATUS_EXPECTATION")
    requested = dict(operations=["changeParcel", "deleteParcel"])
    assert _exclusion_reason(row, summary={}, focus={}, requested=requested,
                             kind="REGRESSION") == "SIBLING_OPERATION_NOT_SELECTED"
    assert _exclusion_reason(row, summary={}, focus={}, requested=requested,
                             kind="INVESTIGATION") == "SIBLING_OPERATION_NOT_SELECTED"
    # A functional goal names the whole capability: the same drop must stay visible.
    assert _exclusion_reason(row, summary={}, focus={}, requested=requested,
                             kind="FUNCTIONAL") == "REQUESTED_BUT_NOT_SELECTED"
    # A policy whose operation the goal never named is simply out of the requested scope.
    assert _exclusion_reason(row, summary={}, focus={}, requested=dict(operations=[]),
                             kind="FUNCTIONAL") == "NOT_IN_REQUESTED_SCOPE"


def test_the_completion_report_publishes_the_scope_ledger():
    from agenttest.task_completion import evaluate

    project, _ = lifecycle_project()
    summary = focused_summary(project, operations=["readParcel"])
    report = evaluate(project, summary, "帮我测一下包裹管理功能。", stop=dict(reason="ANSWER"))
    # The selected scope is closed over the approved producer the read's identity needs.
    assert report["scope_ledger"]["selected"]["count"] == 2
    assert report["scope_ledger"]["coverage"]["selected"]["total"] == 2
    assert report["coverage"]["scopes"]["approved"]["total"] >= 1
    assert report["scope_ledger"]["schema"] == "agenttest.scope-ledger.v1"
    assert report["complete"] is False
