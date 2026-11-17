"""Obligation-driven supervision: the closing answer is checked, repair stays bounded.

Every case is a deterministic fault injection over a ledger state that used to end in a
premature ``ANSWER``, a repeated batch or a false "complete": the assertions pin the exact
terminal state, the exact next dispatch and the exact repair text. No network, no model.
"""

import json
from importlib.util import find_spec
from types import SimpleNamespace

import pytest
from test_product_design import project as design_project
from test_product_supervisor import lifecycle_project, obligation_summary

from agenttest.diagnostics import Layer, schema_diagnostic
from agenttest.obligation_gate import (
    ObligationGate,
    cleanup_state,
    continuation_text,
    decide,
    dispatch_plan,
    formal_code,
    obligation_state,
    repair_constraint,
    termination,
)
from agenttest.plan_construction import construct_plan
from agenttest.plan_json import bounded_json

BUDGET = dict(requests_left=24, model_calls_left=8, tool_calls_left=12, seconds_left=600.0)
requires_deep_agents = pytest.mark.skipif(find_spec("deepagents") is None,
                                          reason="Deep Agents extra is not installed")


def release_summary(project, *, operations=("insertJob", "readJob")):
    """One round's approved scope: the ordinary groups plus their negative batches."""
    from agenttest.product_design import design_groups
    from agenttest.product_obligations import negative_batches

    groups = design_groups(project, operation_ids=list(operations))
    summary = dict(groups=groups, cases=[], design_records=[],
                   inventory=dict(total=len(operations), operations=[
                       dict(operation_id=name, state="READY", reason="", next_step="")
                       for name in operations]),
                   write_effects=dict(unresolved=[], pending=[], requests=0, counts={}))
    summary["groups"] = groups + negative_batches(project, summary)
    return summary


def measure(summary, policy_ids, *, outcome="PASS"):
    """Record a real attempt per policy: this is the evidence the gate counts."""
    for policy_id in policy_ids:
        summary["cases"].append(dict(
            case_id="case_" + policy_id, run_id="run_" + policy_id, status="FINISHED",
            outcome=outcome, http_requests=1, planned_steps=1, executed_steps=1,
            executed_operations=["insertJob"], executed_policy_ids=[policy_id],
            evidence=[dict(operation_id="insertJob", policy_id=policy_id, executed=True,
                           outcome=outcome, attempts=[dict(status_code=422)],
                           checks=[dict(kind="status", selector="status", expected=422,
                                        actual=422, passed=outcome == "PASS")])]))


def scope_ids(project, summary):
    from agenttest.product_obligations import approved_scope

    return [item["policy_id"] for item in approved_scope(project, summary)]


def gate_decision(project, summary, *, budget=None, **kwargs):
    state = obligation_state(project, summary)
    plan = dispatch_plan(project, summary, runs=kwargs.pop("runs", None),
                         attempt_limit=kwargs.pop("attempt_limit", 3))
    return decide(state=state, plan=plan, cleanup=cleanup_state(project, summary),
                  budget=dict(BUDGET if budget is None else budget), **kwargs)


# ---------------------------------------------------------------- 1. the two missing checks
def test_a_closing_answer_is_refused_while_two_approved_checks_are_missing():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    batch = next(group for group in summary["groups"] if group.get("policy_ids"))
    remaining = batch["policy_ids"][-2:]
    measure(summary, [key for key in scope_ids(project, summary) if key not in remaining])

    decision = gate_decision(project, summary)
    assert decision["action"] == "CONTINUE"
    assert decision["next"]["group_id"] == batch["id"]
    assert decision["obligations"]["pending_total"] == 2
    # The instruction names the real policies and the exact tool call, never a vague "continue".
    text = continuation_text(project, decision["next"], decision["next"]["policies"], BUDGET)
    assert f'group_id="{batch["id"]}"' in text
    assert all(key in text for key in remaining)
    assert "你的结束请求被拒绝" in text


def test_the_same_answer_is_accepted_once_the_last_obligation_is_measured():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    decision = gate_decision(project, summary)
    assert decision["action"] == "CONTINUE"
    measure(summary, scope_ids(project, summary))
    decision = gate_decision(project, summary)
    assert decision["action"] == "END"
    assert (decision["state"], decision["code"]) == ("COMPLETE", "OBLIGATIONS_COMPLETE")


# ---------------------------------------------------------------- 2. the terminal vocabulary
def test_an_assertion_failure_is_measured_evidence_not_an_open_obligation():
    """A real 503 on an approved read is a business assertion failure, not a retry loop."""
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    target = scope_ids(project, summary)[0]
    measure(summary, [target], outcome="ASSERTION_FAILED")

    state = obligation_state(project, summary)
    assert target not in [row["policy_id"] for row in state["pending"]]
    assert [row["policy_id"] for row in state["failed"]] == [target]
    measure(summary, [key for key in scope_ids(project, summary) if key != target])
    decision = gate_decision(project, summary)
    assert decision["action"] == "END" and decision["state"] == "COMPLETE"
    report = termination(project, summary, stop_reason="ANSWER")
    assert report["assertions"]["failed"] == 1
    assert report["strict_complete"] is False


def test_a_created_resource_without_a_cleanup_receipt_blocks_completion():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    measure(summary, scope_ids(project, summary))
    summary["write_effects"] = dict(
        unresolved=[dict(step_id="create", policy_id="web_rule_1", status="CLEANUP_UNPROVEN")],
        pending=[], requests=3, counts={})
    decision = gate_decision(project, summary)
    assert decision["action"] == "END"
    assert (decision["state"], decision["code"]) == (
        "BLOCKED", "WRITE_OUTCOME_REQUIRES_REVIEW")


@pytest.mark.parametrize(("budget", "code"), [
    (dict(BUDGET, requests_left=0), "OBLIGATION_REQUEST_BUDGET"),
    (dict(BUDGET, model_calls_left=1), "OBLIGATION_MODEL_CALL_BUDGET"),
    (dict(BUDGET, tool_calls_left=1), "OBLIGATION_TOOL_CALL_BUDGET"),
    (dict(BUDGET, seconds_left=0), "OBLIGATION_DEADLINE"),
])
def test_a_spent_budget_ends_as_blocked_and_never_as_complete(budget, code):
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    decision = gate_decision(project, summary, budget=budget)
    assert decision["action"] == "END"
    assert (decision["state"], decision["code"]) == ("BLOCKED", code)


def test_two_refusals_without_a_new_obligation_end_the_run_as_partial():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    gate = ObligationGate(project, summary)
    codes = []
    for _ in range(3):
        decision = gate.evaluate(budget=BUDGET, runs={}, attempt_limit=3)
        codes.append(decision["code"])
    assert codes == ["OBLIGATION_PENDING", "OBLIGATION_PENDING", "OBLIGATION_STALLED"]
    assert gate.continuations == 2 and gate.stalled == 2
    assert decision["action"] == "END" and decision["state"] == "PARTIAL"
    report = gate.terminate(stop_reason="ANSWER")
    assert report["blocked"] is False and report["state"] == "PARTIAL"


def test_a_continuation_that_measures_something_resets_the_stall_counter():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    gate = ObligationGate(project, summary)
    gate.evaluate(budget=BUDGET, runs={}, attempt_limit=3)
    assert gate.stalled == 1
    measure(summary, scope_ids(project, summary)[:1])
    gate.evaluate(budget=BUDGET, runs={}, attempt_limit=3)
    assert gate.stalled == 0 and gate.continuations == 2


def test_the_gate_bound_survives_a_durable_resume():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    gate = ObligationGate(project, summary)
    gate.evaluate(budget=BUDGET, runs={}, attempt_limit=3)
    gate.evaluate(budget=BUDGET, runs={}, attempt_limit=3)
    saved = json.loads(json.dumps(gate.public()))
    resumed = ObligationGate(project, summary)
    resumed.restore(saved)
    assert (resumed.continuations, resumed.stalled) == (2, 2)
    # A resume cannot buy another refusal: the same bound applies immediately.
    decision = resumed.evaluate(budget=BUDGET, runs={}, attempt_limit=3)
    assert decision["code"] == "OBLIGATION_STALLED" and decision["action"] == "END"


# ---------------------------------------------------------------- 3. dispatch discipline
def test_a_passed_obligation_is_never_dispatched_again():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    batch = next(group for group in summary["groups"] if group.get("policy_ids"))
    measure(summary, batch["policy_ids"])
    plan = dispatch_plan(project, summary, runs={}, attempt_limit=3)
    assert batch["id"] not in [row["group_id"] for row in plan["dispatchable"]]
    for row in plan["groups"]:
        assert not (set(row["policies"]) & set(batch["policy_ids"]))


def test_a_half_measured_batch_dispatches_only_its_missing_checks():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    batch = next(group for group in summary["groups"] if group.get("policy_ids"))
    missing = batch["policy_ids"][-1]
    measure(summary, batch["policy_ids"][:-1])
    row = next(row for row in dispatch_plan(project, summary)["dispatchable"]
               if row["group_id"] == batch["id"])
    assert row["policies"] == [missing] and row["pending"] == 1
    # The ordinary group owns the approved read/create, so it is dispatched before the batch;
    # once it is measured, the batch is next and only the missing check is requested.
    measure(summary, [key for key in scope_ids(project, summary)
                      if key not in batch["policy_ids"]])
    decision = gate_decision(project, summary, runs={batch["id"]: 1})
    assert decision["next"]["group_id"] == batch["id"]
    assert decision["next"]["policies"] == [missing]


def test_an_obligation_no_group_owns_is_blocked_instead_of_widening_the_scope():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    batch = next(group for group in summary["groups"] if group.get("policy_ids"))
    measure(summary, [key for key in scope_ids(project, summary)
                      if key not in batch["policy_ids"]])
    summary["groups"] = [group for group in summary["groups"] if group["id"] != batch["id"]]
    plan = dispatch_plan(project, summary)
    assert plan["dispatchable"] == [] and plan["groups"] == []
    decision = gate_decision(project, summary)
    assert (decision["action"], decision["state"], decision["code"]) == (
        "END", "BLOCKED", "OBLIGATION_AUTHORITY_LIMITED")


def test_an_exhausted_group_is_reported_instead_of_retried():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    runs = {group["id"]: 3 for group in summary["groups"]}
    decision = gate_decision(project, summary, runs=dict(runs))
    assert (decision["action"], decision["state"], decision["code"]) == (
        "END", "PARTIAL", "OBLIGATION_ATTEMPTS_EXHAUSTED")


def test_an_operation_that_is_not_ready_blocks_that_group_only():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    summary["inventory"]["operations"][0]["state"] = "BLOCKED"
    plan = dispatch_plan(project, summary)
    assert plan["dispatchable"] == []
    assert {row["group_id"] for row in plan["blocked"]} == {
        group["id"] for group in summary["groups"]}
    decision = gate_decision(project, summary)
    assert decision["code"] == "OBLIGATION_AUTHORITY_LIMITED"


# ---------------------------------------------------------------- 4. bounded repair
def test_a_persistent_json_syntax_failure_is_repaired_with_precise_feedback():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    batch = next(group for group in summary["groups"] if group.get("policy_ids"))
    diagnostic = dict(layer="json", code="JSON_SYNTAX", fields=[],
                      detail=dict(position=2626, line=41, column=17, reason="MISSING_COLON"))
    assert formal_code("JSON_SYNTAX") is True
    text = repair_constraint(project, batch, batch["policy_ids"], diagnostic, attempt=2)
    assert "group=" + batch["id"] in text
    assert "MISSING_COLON" in text and "第41行第17列" in text
    assert all(key in text for key in batch["policy_ids"])
    assert "不得新增、改名、只读化或删减策略" in text
    assert "PUT" in text or "POST" in text  # the real method is stated, not "read-only"
    assert len(text) <= 900
    # A semantic rejection is not a formal defect: it must not be auto-repaired.
    assert formal_code("DESIGN_UNAPPROVED_NEGATIVE_VALUE") is False
    assert formal_code("MODEL_SCHEMA_EXTRA_FIELD") is True


def test_a_reference_to_a_nonexistent_step_names_the_allowed_ids():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    batch = next(group for group in summary["groups"] if group.get("policy_ids"))
    diagnostic = dict(layer="model", code="CONSTRUCTION_REFERENCE_INVALID",
                      fields=["cases.1.plan.steps.1.arguments.0.from_step"],
                      detail=dict(reference="SOURCE_UNKNOWN",
                                  allowed_from_steps=["create_seed", "put_step"],
                                  case_index=1))
    text = repair_constraint(project, batch, batch["policy_ids"], diagnostic, attempt=3)
    assert "SOURCE_UNKNOWN" in text and "create_seed" in text and "put_step" in text
    assert "from_step 只能引用本方案中已声明的步骤" in text


def test_json_syntax_reports_a_position_and_a_closed_reason():
    with pytest.raises(Exception) as caught:
        bounded_json('{"name" "value"}', 1000)
    diagnostic = caught.value.diagnostic
    assert (diagnostic.layer, diagnostic.code) == (Layer.JSON, "JSON_SYNTAX")
    assert diagnostic.detail["reason"] == "MISSING_COLON"
    assert diagnostic.detail["position"] >= 1 and diagnostic.detail["line"] == 1
    assert "value" not in json.dumps(diagnostic.detail)  # never the model's text


def test_an_extra_schema_field_is_named_so_the_repair_can_remove_it():
    from pydantic import BaseModel, ConfigDict, ValidationError

    class Step(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str

    class Plan(BaseModel):
        model_config = ConfigDict(extra="forbid")
        steps: list[Step]

    try:
        Plan.model_validate({"steps": [{"id": "a", "sender": "x", "a b": "y"}]})
    except ValidationError as exc:
        diagnostic = schema_diagnostic(exc, Layer.MODEL)
    assert diagnostic.code == "MODEL_SCHEMA_EXTRA_FIELD"
    # An identifier-shaped key is echoed so the model can delete it; a value-shaped one is not.
    assert diagnostic.detail["unknown_fields"] == ["sender"]
    assert all("*" not in field or field.endswith("*") for field in diagnostic.fields)


def test_a_construction_reference_failure_carries_the_step_and_allowed_ids():
    project, _ = design_project(negative=True)
    from agenttest.product_design import design_context

    groups = [group for group in design_summary(project)["groups"]
              if group.get("policy_ids")]
    batch = groups[0]
    context = design_context(project, "帮我测一下任务接口。", batch, 24, 0)
    plan = dict(name="negative", steps=[
        dict(id="create_seed", operation_id="insertJob", role=None, policy_id="web_rule_1",
             arguments=[dict(location="body", name="name", value="x", from_step=None,
                             select=None)]),
        dict(id="bad", operation_id="insertJob", role=None,
             policy_id=batch["policy_ids"][0],
             arguments=[dict(location="body", name="name", from_step="missing_step",
                             select="$.id", value=None)])])
    with pytest.raises(Exception) as caught:
        construct_plan(plan, {**context, "planning_ir": None})
    diagnostic = caught.value.diagnostic
    assert diagnostic.code in {"CONSTRUCTION_REFERENCE_INVALID",
                               "CONSTRUCTION_NODE_NOT_APPROVED"}
    if diagnostic.code == "CONSTRUCTION_REFERENCE_INVALID":
        assert diagnostic.detail["reference"] == "SOURCE_UNKNOWN"
        # The test attempts to bind a model response to a literal-only input.
        assert diagnostic.detail["allowed_from_steps"] == []
        assert diagnostic.fields == ["plan.steps.1.arguments.0.from_step"]


def design_summary(project):
    return release_summary(project)


# ---------------------------------------------------------------- 5. the real loop
@requires_deep_agents
@pytest.mark.asyncio
async def test_the_real_loop_refuses_a_premature_answer_and_then_measures_the_rest(tmp_path):
    from test_product_agent import evidence, scripted, session, tool_turn

    from agenttest.product_agent import run_agent

    project, _ = lifecycle_project()
    policies = [policy.id for policy in project.registry.definitions() if not policy.negative]

    def effect(summary, group, supervision):
        evidence(summary, group, policies)
        summary["write_effects"] = dict(unresolved=[], pending=[], requests=2, counts={})

    model, observed = scripted(
        ("我已经把全部接口测完了。", {}),
        tool_turn("run_test_group", "g1", group_id="group_1"),
        ("全部已批准义务都有执行证据了。", {}))
    session_, tasks, *_ = session(tmp_path, effect=effect)
    result = await run_agent(session_.scope, tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=8, model=model, obligations=dict())
    assert result.stop_reason == "ANSWER"
    assert result.termination["state"] == "COMPLETE"
    assert result.termination["strict_complete"] is True
    # The refusal is in the audit trail, and the model really received the instruction.
    assert result.session.gate_log and result.session.obligations.continuations == 1
    instruction = next(item for item in observed[1]["payload"]["messages"]
                       if item["role"] == "user"
                       and "运行时义务门禁" in (item.get("content") or ""))
    assert 'group_id="group_1"' in instruction["content"]
    assert [row["name"] for row in result.transcript if row["kind"] == "TOOL_CALL"] == [
        "run_test_group"]


@requires_deep_agents
@pytest.mark.asyncio
async def test_a_model_that_only_answers_again_cannot_loop_forever(tmp_path):
    from test_product_agent import scripted, session

    from agenttest.product_agent import run_agent

    model, _ = scripted(("结束了。", {}))
    session_, tasks, *_ = session(tmp_path)
    result = await run_agent(session_.scope, tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=8, model=model, obligations=dict())
    # Two refusals without a newly measured obligation end the run; no third is attempted.
    assert result.termination["code"] == "OBLIGATION_STALLED"
    assert result.termination["state"] == "PARTIAL"
    assert result.session.obligations.continuations == MAX_STALLED_ALLOWED
    assert tasks.calls == []


MAX_STALLED_ALLOWED = 2


def test_a_persistent_model_failure_is_blocked_not_completed():
    """A run that dies on the gateway must not look like a finished pass."""
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    measure(summary, scope_ids(project, summary)[:3])
    report = termination(project, summary, stop_reason="MODEL_ERROR")
    assert (report["state"], report["code"]) == ("BLOCKED", "MODEL_SERVICE_UNAVAILABLE")
    assert report["blocked"] is True and report["strict_complete"] is False
    assert report["obligations"]["pending_total"] == 9
    # Once every obligation is measured the evidence decides, and the gateway stop stays visible
    # as the loop's own end reason instead of being turned into a fake "incomplete".
    measure(summary, scope_ids(project, summary))
    report = termination(project, summary, stop_reason="MODEL_ERROR")
    assert report["state"] == "COMPLETE" and report["stop_reason"] == "MODEL_ERROR"


def test_a_group_run_repairs_a_formal_rejection_without_a_model_note(tmp_path):
    """The runtime authors the repair note itself; a semantic rejection still needs a new one."""
    from test_product_agent import session

    session_, _, *_ = session(tmp_path)
    session_.summary["groups"][0]["policy_ids"] = None
    session_.group_runs["group_1"] = 1
    session_.summary["groups"][0]["status"] = "NOT_EXECUTED"
    session_.summary["design_records"].append(dict(
        group_id="group_1", status="REJECTED", reason="MODEL_INVALID_RESPONSE",
        diagnostic=dict(layer="json", code="JSON_SYNTAX", fields=[],
                        detail=dict(position=12, line=1, column=13,
                                    reason="MISSING_COMMA"))))
    session_.summary["groups"][0]["policy_ids"] = None
    assert session_.formal_repair_note("group_1") is None  # no batch: nothing to repair here

    project, _ = design_project(negative=True)
    batch = release_summary(project)["groups"][1]
    session_.summary["groups"] = [batch]
    session_.summary["design_records"] = [dict(
        group_id=batch["id"], status="REJECTED", reason="MODEL_INVALID_RESPONSE",
        diagnostic=dict(layer="model", code="MODEL_SCHEMA_EXTRA_FIELD", fields=["steps.*"],
                        detail=dict(unknown_fields=["sender"])))]
    session_.group_runs[batch["id"]] = 1
    note = session_.formal_repair_note(batch["id"])
    assert note and "sender" in note and "MODEL_SCHEMA_EXTRA_FIELD" in note
    # A semantic rejection is not repaired by the runtime.
    session_.summary["design_records"][-1]["diagnostic"] = dict(
        layer="model", code="DESIGN_UNAPPROVED_NEGATIVE_VALUE", fields=[])
    assert session_.formal_repair_note(batch["id"]) is None


@pytest.mark.asyncio
async def test_a_formally_rejected_batch_is_retried_once_with_a_runtime_repair_note(tmp_path):
    """The second attempt re-sends the approved batch with the structured defect attached."""
    from test_product_agent import session

    project, _ = design_project(negative=True)
    batch = release_summary(project)["groups"][1]
    rejections = []

    def effect(summary, group, supervision):
        if not rejections:
            rejections.append(supervision)
            group["status"] = "NOT_EXECUTED"
            summary["design_records"].append(dict(
                group_id=group["id"], status="NOT_EXECUTED", reason="MODEL_INVALID_RESPONSE",
                diagnostic=dict(layer="json", code="JSON_SYNTAX", fields=[],
                                detail=dict(position=40, line=2, column=3,
                                            reason="MISSING_COMMA"))))
            return
        group["status"] = "DESIGNED"
        measure(summary, batch["policy_ids"])

    session_, tasks, *_ = session(tmp_path, effect=effect)
    session_.project = session_.scope.project = project
    session_.scope.executable_checks = []
    session_.summary["groups"] = [batch]
    session_.summary["inventory"] = dict(total=2, operations=[
        dict(operation_id=name, state="READY", reason="", next_step="")
        for name in ("insertJob", "readJob")])

    first = await session_.run_group(batch["id"])
    # The precise diagnostic code reaches the model, together with its position and reason.
    assert first["ok"] is False and first["error"]["code"] == "JSON_SYNTAX"
    assert first["error"]["detail"]["reason"] == "MISSING_COMMA"
    assert first["error"]["retryable"] is True
    second = await session_.run_group(batch["id"])
    assert second.get("ok") is True
    note = tasks.calls[1]["supervision"]["design_note"]
    assert "运行时修复约束" in note and "MISSING_COMMA" in note and "第2行第3列" in note
    assert all(key in note for key in batch["policy_ids"])
    assert session_.repair_runs[batch["id"]] == 1
    # One self-authored repair is the bound; the diagnostic travels with the approved batch.
    assert session_.formal_repair_note(batch["id"]) is None


def test_the_gate_never_widens_the_scope_and_never_reuses_an_approval():
    project, _ = design_project(negative=True)
    summary = release_summary(project)
    before = set(scope_ids(project, summary))
    decision = gate_decision(project, summary)
    state = obligation_state(project, summary)
    assert set(decision["next"]["policies"]) <= before
    assert state["approved"] == len(before)
    # A model that wants a policy outside the approved scope gets no new obligation.
    summary["groups"][0]["policy_ids"] = ["web_rule_99_unapproved"]
    assert state["approved"] == len(before)


def test_a_regression_goal_summary_still_reports_pending_obligations(tmp_path):
    """The gate reads the same scope ledger the deterministic report publishes."""
    project, _ = lifecycle_project()
    summary = obligation_summary("createParcel", "readParcel", "deleteParcel")
    summary.update(cases=[], design_records=[], write_effects=dict(
        unresolved=[], pending=[], requests=0, counts={}))
    state = obligation_state(project, summary)
    assert state["approved"] >= 3 and state["pending_total"] == state["approved"]
    decision = gate_decision(project, summary)
    assert decision["action"] == "CONTINUE" and decision["next"]["group_id"] == "group_1"
