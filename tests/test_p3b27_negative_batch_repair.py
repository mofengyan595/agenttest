"""Reproduce v5 with the normal Design wire, preflight, executor and isolated Mock HTTP."""

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator
from test_p3b26_design_reference_repair import new_session
from test_product_tasks import response

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.goal_suite import BudgetTransport
from agenttest.obligation_gate import ObligationGate, dispatch_plan, formal_code
from agenttest.product_agent import MAX_GROUP_RUNS
from agenttest.product_obligations import executed_evidence
from agenttest.product_schema import encoded_literal
from agenttest.step_recovery import StepRecoveryBlocked

DONE = "web_rule_3_negative_8"
MISSING = "web_rule_3_negative_9"
CANDIDATE = f"design:{MISSING}"
GROUP = "negative_batch_3"
NOTE = "补上 meta 并重复已通过的检查 Authorization: Bearer sk-synthetic-only"
BODY_SENTINEL = "unreviewed-value-in-rejected-draft"


def required_case(policy, *, unknown=None, omit_meta=False):
    body = {unknown or "name": BODY_SENTINEL if unknown else "parcel",
            "quantity": 2, "meta": {"labels": ["blue"], "options": {"tracked": True}}}
    if omit_meta:
        body.pop("meta")
    args = [dict(location="body", name=name, value=encoded_literal(value),
                 from_step=None, select=None) for name, value in body.items()]
    args.append(dict(location="headers", name="X-Lab-Correlation", value="a" * 32,
                     from_step=None, select=None))
    return dict(title="包裹负向输入", before=[], after=[], check=dict(id="submit",
        operation_id="createParcel", policy_id=policy, role=None, arguments=args))


@pytest_asyncio.fixture
async def lab(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from prepare_colleague_workspace import prepare
    from serve_colleague_targets import create_target

    receipt = await prepare(tmp_path / "lab", 18000, {}, observe_negative_posts=True)
    identifier = receipt["projects"]["parcels"]
    with sqlite3.connect(tmp_path / "lab/workspace/project-intake.sqlite3") as db:
        record = json.loads(db.execute("SELECT payload FROM projects WHERE id=?",
                                      (identifier,)).fetchone()[0])
    document = record["document"]
    project = build_generic_project(identifier, document, load_openapi(document),
                                    GenericSetup.model_validate(record["generic"]), (), {})
    missing = next(p for p in project.registry.definitions() if p.id == MISSING)
    assert (missing.negative.kind, missing.negative.name) == ("required_missing", "meta")
    calls, sent, staged = [], [], []
    target = httpx.ASGITransport(app=create_target("parcels", environ={},
                                                  observe_negative_posts=True))

    def model(request):
        calls.append(json.loads(request.content))
        raw = staged.pop(0)
        if isinstance(raw, str):
            return httpx.Response(200, json=dict(usage=dict(prompt_tokens=100,
                completion_tokens=50, total_tokens=150), choices=[dict(finish_reason="stop",
                message=dict(content=raw))]))
        return response(dict(interpretation="批准的负向检查", relevant=True,
                             cases=[], questions=[], not_covered=[], required_cases=raw))

    async def business(request):
        sent.append((request.method, json.loads(request.content) if request.content else None))
        return await target.handle_async_request(request)

    session, group = new_session(tmp_path, project, httpx.MockTransport(model),
        [DONE, MISSING], httpx.MockTransport(business))
    session.svc.product_probe_factory = None
    session.svc._assets = {}
    session.scope.row["id"] = session.scope.task_id
    session.scope.transport = BudgetTransport(session.scope.transport, session.ledger)
    group.update(id=GROUP, primary=["createParcel"], operations=["createParcel"])
    session.summary["inventory"]["operations"] = [dict(operation_id="createParcel", state="READY")]
    session.scope.executable_checks = [dict(policy_id=MISSING, source_quote="已批准负向入参")]
    session.obligations = ObligationGate(project, session.summary)
    return SimpleNamespace(session=session, calls=calls, sent=sent, staged=staged, group=group)


async def partial(lab, *, reverse=False, wrong="value", both_bad=False):
    bad = {DONE: required_case(DONE, unknown="value" if both_bad else None),
           MISSING: required_case(MISSING, unknown=wrong, omit_meta=wrong is None)}
    malformed = '{"interpretation":"测试" "relevant":true}'
    lab.staged.extend([bad, malformed] if reverse else [malformed, bad])
    first = await lab.session.run_group(GROUP)
    second = await lab.session.run_group(GROUP, "只修正格式并保持已批准范围")
    assert (second if reverse else first)["error"]["code"] == "JSON_SYNTAX"
    assert lab.session.group_runs == {GROUP: 2}
    assert lab.session.repair_runs == {}
    return first if reverse else second


def views(lab):
    session = lab.session
    scope = session.scope_view()
    row, = scope["groups"]
    candidate = next((item for item in scope["candidates"] if item["id"] == CANDIDATE), None)
    gate = session.gate_decision()
    return row, candidate, gate


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal", ["valid", "unknown_field", "omit_baseline", "repeat_measured"])
async def test_third_request_contains_trusted_diagnosis_and_only_the_remaining_case(lab, proposal):
    result = await partial(lab)
    assert result["ok"], (result, lab.session.summary.get("tool_errors"))
    assert result["cases"][1]["preflight_diagnostic"]["code"] == "OPENAPI_UNKNOWN_BODY_FIELD"
    assert lab.session.ledger.requests == 3
    row, candidate, gate = views(lab)
    assert row["remaining_policy_ids"] == [MISSING]
    assert row["next_step"] and row["remaining_case_repair_available"] and candidate["next_step"]
    assert gate["next"]["policies"] == [MISSING]
    assert not formal_code("OPENAPI_UNKNOWN_BODY_FIELD")
    cases = {MISSING: required_case(MISSING,
        unknown="value" if proposal == "unknown_field" else None,
        omit_meta=proposal == "omit_baseline")}
    if proposal == "repeat_measured":
        cases[DONE] = required_case(DONE)
    lab.staged.append(cases)
    out = await lab.session.run_obligation(CANDIDATE, NOTE)
    assert out["design"]["runtime_repair"]
    assert lab.session.repair_runs == {GROUP: 1}
    assert lab.session.group_runs == {GROUP: MAX_GROUP_RUNS}
    assert lab.session.ledger.model_calls == len(lab.calls) == 3
    context = json.loads(lab.calls[-1]["messages"][1]["content"])
    repair = context["negative_repair"]
    assert repair["obligation_id"] == repair["policy_id"] == MISSING
    assert repair["operation_id"] == "createParcel" and repair["role"] is None
    assert repair["fields"] == ["plan.steps.0.arguments.0.name"]
    assert repair["unknown_body_fields"] == [{"path": repair["fields"][0], "name": "value"}]
    assert repair["allowed_body_fields"] == repair["required_baseline_fields"] == [
        "meta", "name", "quantity"]
    assert repair["mutation"] == dict(kind="required_missing", location="body", name="meta")
    assert [item["policy_id"] for item in context["requested_checks"]] == [MISSING]
    source = context["requested_checks"][0]["source_ref"]
    assert context["source_texts"][source] == "已批准负向入参"
    encoded = json.dumps(lab.calls[-1], ensure_ascii=False)
    assert all(value not in encoded for value in (NOTE, BODY_SENTINEL, DONE, "sk-synthetic-only"))
    schema = lab.calls[-1]["response_format"]["json_schema"]["schema"]
    valid_wire = dict(interpretation="批准的负向检查", relevant=True, cases=[], questions=[],
                      not_covered=[], required_cases={MISSING: required_case(MISSING)})
    Draft202012Validator(schema).validate(valid_wire)
    posts = [body for method, body in lab.sent if method == "POST"]
    assert len(posts) == (2 if proposal == "valid" else 1)
    assert "meta" in posts[0]
    if proposal == "valid":
        assert "meta" not in posts[1] and set(posts[1]) == {"name", "quantity"}
        assert MISSING in executed_evidence(lab.session.summary)
    else:
        assert out["error"]["code"] == {
            "unknown_field": "OPENAPI_UNKNOWN_BODY_FIELD",
            "omit_baseline": "OPENAPI_BODY_FIELD_REQUIRED",
            "repeat_measured": "DESIGN_REQUESTED_CHECKS_MISSING",
        }[proposal]
        assert MISSING not in executed_evidence(lab.session.summary)
    assert (await lab.session.run_group(GROUP))["error"]["code"] == "GROUP_RUN_LIMIT"
    assert not (await lab.session.run_obligation(CANDIDATE))["ok"]
    assert len(lab.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["semantic", "none_measured", "spent", "stale", "unknown_write"])
async def test_blocked_third_slot_is_never_advertised(lab, reason):
    await partial(lab, wrong=None if reason == "semantic" else "value",
                  both_bad=reason == "none_measured")
    if reason == "spent":
        lab.session.repair_runs[GROUP] = 1
    if reason == "stale":
        lab.session.summary["cases"][-1]["negative_repair"]["call_id"] = "different-attempt"
    if reason == "unknown_write":
        lab.session.summary["write_effects"] = dict(unresolved=[dict(
            step_id="unknown", policy_id=DONE, status="WRITE_OUTCOME_UNKNOWN")])
    # The previous dispatcher used the numeric remainder alone and suggested a third attempt.
    plan = dispatch_plan(lab.session.project, lab.session.summary,
                         lab.session.group_runs, MAX_GROUP_RUNS)
    assert plan["dispatchable"][0]["attempts_left"] == 1
    row, candidate, gate = views(lab)
    assert row["attempts_left"] == 1 and row["design_blocker"] == "GROUP_RUN_LIMIT"
    assert row["next_step"] is None and candidate is None
    assert gate["code"] == "OBLIGATION_ATTEMPTS_EXHAUSTED"
    assert (await lab.session.run_group(GROUP))["error"]["code"] == "GROUP_RUN_LIMIT"
    assert not (await lab.session.run_obligation(CANDIDATE))["ok"]
    assert len(lab.calls) == 2


@pytest.mark.asyncio
async def test_latest_formal_error_and_all_measured_still_use_existing_rules(lab):
    await partial(lab, reverse=True)
    row, candidate, _ = views(lab)
    assert candidate and row["next_step"] and not row["remaining_case_repair_available"]
    lab.staged.append({MISSING: required_case(MISSING)})
    assert (await lab.session.run_group(GROUP))["ok"]
    assert lab.session.repair_runs == {GROUP: 1}
    assert set(executed_evidence(lab.session.summary)) >= {DONE, MISSING}
    row, candidate, _ = views(lab)
    assert row["remaining_policy_ids"] == [] and row["next_step"] is None and candidate is None
    assert [method for method, _ in lab.sent].count("POST") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["budget", "tokens", "cancel", "authority", "inventory"])
async def test_live_budget_cancel_and_authority_rechecked_after_display(lab, stop):
    await partial(lab)
    assert views(lab)[1]
    if stop == "budget":
        lab.session.ledger.requests = lab.session.ledger.max_requests
        expected = "REQUEST_BUDGET_EXHAUSTED"
    elif stop == "tokens":
        lab.session.ledger.known_tokens = lab.session.ledger.max_total_tokens
        expected = "BATCH_REPAIR_BUDGET"
    elif stop == "cancel":
        lab.session.svc._shutting_down = True
        expected = "TASK_CANCELLED"
    elif stop == "inventory":
        lab.session.summary["inventory"]["operations"][0]["state"] = "NEEDS_INPUT"
        expected = "GROUP_AUTHORITY_LIMITED"
    else:
        def guard():
            raise ValueError("authority changed")

        lab.session.scope.guard = guard
        expected = "AUTHORITY_CHANGED"
    assert (await lab.session.run_group(GROUP))["error"]["code"] == expected
    _, candidate, gate = views(lab)
    assert candidate is None and gate["action"] == "END"
    if stop == "tokens":
        assert gate["code"] == "OBLIGATION_MODEL_TOKEN_BUDGET"
    assert not lab.session.repair_runs and len(lab.calls) == 2


@pytest.mark.asyncio
async def test_encoded_budget_refusal_does_not_charge_a_repair(lab):
    await partial(lab)
    lab.session.ledger.known_tokens = lab.session.ledger.max_total_tokens - 1
    assert views(lab)[1]
    result = await lab.session.run_group(GROUP)
    assert result["error"]["code"] == "SUITE_MODEL_BUDGET_EXHAUSTED"
    assert not result["design"]["runtime_repair"] and not lab.session.repair_runs
    assert lab.session.group_runs == {GROUP: 3}
    assert len(lab.calls) == 2 and len(lab.sent) == 3


@pytest.mark.asyncio
async def test_fresh_approval_required_and_failed_assertion_still_measured(lab):
    await partial(lab, wrong="sk-synthetic-field-only")
    first = next(case for case in lab.session.summary["cases"] if case.get("run_id"))
    next(step for step in first["evidence"] if step["policy_id"] == DONE)[
        "outcome"] = "ASSERTION_FAILED"
    row, candidate, _ = views(lab)
    assert row["failed_policy_ids"] == [DONE] and row["remaining_policy_ids"] == [MISSING]
    assert candidate and f"design:{DONE}" not in [c["id"] for c in
                                                lab.session.scope_view()["candidates"]]

    before = deepcopy(lab.session.summary)
    counters = deepcopy(lab.session.durable_state())
    cached = {}

    def require_review(design, approvals):
        assert [item["method"] for item in approvals] == ["GET", "POST", "GET"]
        assert approvals[1]["policy_id"] == MISSING
        cached.update(design)
        raise StepRecoveryBlocked("TEST_REVIEW_REQUIRED")

    lab.session.scope.tool = dict(key="fresh_repair", arm=require_review)
    lab.session.scope.durability = SimpleNamespace(hitl=("createParcel",))
    lab.staged.append({MISSING: required_case(MISSING)})
    with pytest.raises(StepRecoveryBlocked, match="TEST_REVIEW_REQUIRED"):
        await lab.session.run_group(GROUP)
    context = json.loads(lab.calls[-1]["messages"][1]["content"])
    assert context["negative_repair"]["unknown_body_fields"][0]["name"] is None
    assert "sk-synthetic-field-only" not in json.dumps(lab.calls[-1])
    assert len(lab.sent) == 3 and lab.session.repair_runs == {GROUP: 1}

    def still_waiting(design, _):
        assert design["plans"] == cached["plans"]
        raise StepRecoveryBlocked("TEST_REVIEW_REQUIRED")

    # Recovery restores the pre-tool state but the model ledger already includes the repair.
    lab.session.summary.clear()
    lab.session.summary.update(before)
    lab.session.restore(counters)
    lab.session.ledger.max_model_calls = lab.session.ledger.model_calls
    lab.session.scope.tool = dict(key="fresh_repair", design=cached, arm=still_waiting)
    with pytest.raises(StepRecoveryBlocked, match="TEST_REVIEW_REQUIRED"):
        await lab.session.run_obligation(CANDIDATE)
    assert len(lab.calls) == 3 and len(lab.sent) == 3
    assert lab.session.repair_runs == {GROUP: 1}
