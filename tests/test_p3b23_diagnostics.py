"""Focused offline regressions derived from the P3-B2.2 release evidence."""

import json

import httpx
import pytest
from test_product_design import case, project, settings

from agenttest.diagnostics import ValidationFailure, design_failure_kind
from agenttest.dsl import ArgumentBinding, Scenario, Step, validate_plan
from agenttest.llm_planner import PlannerFailure
from agenttest.plan_construction import construct_plan
from agenttest.product_design import DesignCase, design_context, design_groups, validate_design_case
from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_tasks import JournalPlanner
from agenttest.project_intake import IntakeInput
from agenttest.task_store import TaskConflict, TaskStore


def parcel_node(key, policy_id="web_rule_7_negative_8", source=None):
    return dict(id=key, operation_id="replaceParcel" if source else "createParcel", role=None,
                policy_id=policy_id, arguments=[] if source is None else [
                    dict(location="path_parameters", name="parcelId", from_step=source,
                         select="$.id", value=None)])


@pytest.mark.parametrize("reference, reason", [
    ("createParcelForNegative8", "SOURCE_UNKNOWN"),
    ("replaceQuantityOutOfBound", "SELF_REFERENCE"),
])
def test_v1_reference_hint_only_advertises_a_valid_earlier_producer(reference, reason):
    # The source, check ID and policy come from release-v1/model-outputs.jsonl:17.
    consumer = parcel_node("replaceQuantityOutOfBound", source=reference)
    seed = parcel_node("createSeed", policy_id="web_rule_3")
    later = parcel_node("laterSeed", policy_id="web_rule_3")
    other = parcel_node("otherSeed", policy_id="other")
    context = dict(policies=[
        dict(id="web_rule_7_negative_8", operation_id="replaceParcel", roles=[None],
             binding_rules=[dict(location="path_parameters", name="parcelId",
                                 source_policy_id="web_rule_3", source_selector="$.id",
                                 role_relation="anonymous")]),
        dict(id="web_rule_3", operation_id="createParcel", roles=[None]),
        dict(id="other", operation_id="createParcel", roles=[None]),
    ])
    for steps, candidates in (([consumer], []),
                              ([other, seed, consumer, later], ["createSeed"])):
        with pytest.raises(ValidationFailure, match="CONSTRUCTION_REFERENCE_INVALID") as caught:
            construct_plan(dict(name="archived-unknown-reference", steps=steps), context)
        diagnostic = caught.value.diagnostic
        assert diagnostic.detail == dict(reference=reason, allowed_from_steps=candidates,
            step_id=consumer["id"], obligation_id=consumer["policy_id"])
        assert diagnostic.fields == [f"plan.steps.{steps.index(consumer)}.arguments.0.from_step"]
        assert design_failure_kind(diagnostic.model_dump()) == "REFERENCE_ERROR"


@pytest.mark.parametrize("source", ["later", "missing", "consumer"])
def test_ordered_executor_input_rejects_future_missing_and_self_references(source):
    proj, _ = project()
    plan = Scenario(name="references", steps=[
        Step(id="consumer", operation_id="readJob", policy_id="web_rule_2",
             path_parameters={"jobId": ArgumentBinding(from_step=source, select="$.id")}),
        Step(id="later", operation_id="insertJob", policy_id="web_rule_1"),
    ])
    with pytest.raises(ValidationFailure, match="Dependency must reference an earlier") as caught:
        validate_plan(plan, proj.contract, proj.target, {})
    assert caught.value.diagnostic.code == "DEPENDENCY_FORWARD_OR_UNKNOWN"
    assert caught.value.diagnostic.detail["step_id"] == "consumer"
    assert caught.value.diagnostic.fields == ["steps.0.path_parameters.*.from_step"]


@pytest.mark.parametrize("step_id, misplaced_name", [
    ("neg_meta_type_mismatch", "测试包裹乙"),
    ("neg_name_type_mismatch", "测试包裹丙"),
    ("neg_name_empty", "测试包裹丁"),
])
def test_v2_unknown_body_name_is_masked_and_located(step_id, misplaced_name):
    # Same misplaced argument-name slots as release-v2/model-outputs.jsonl:5.
    proj, _ = project(negative=True)
    obligation = next(policy.id for policy in proj.registry.definitions() if policy.negative)
    proposal = case(policy=obligation)
    step = proposal["plan"]["steps"][0]
    step["id"] = step_id
    step["arguments"][0]["name"] = misplaced_name
    transport = httpx.MockTransport(lambda _: pytest.fail("preflight dispatched HTTP"))
    with pytest.raises(ValidationFailure, match="Unknown request body field") as caught:
        validate_design_case(proj, DesignCase.model_validate(proposal), None, environ={},
                             transport=transport)
    diagnostic = caught.value.diagnostic
    assert diagnostic.code == "OPENAPI_UNKNOWN_BODY_FIELD"
    assert diagnostic.fields == ["plan.steps.0.arguments.0.name"]
    assert diagnostic.detail == dict(step_id=step_id, step_index=0, obligation_id=obligation,
                                    allowed_body_fields=["details", "name", "quantity"],
                                    unknown_body_positions=[0])
    assert design_failure_kind(diagnostic.model_dump()) == "SCHEMA_MISMATCH"
    assert misplaced_name not in diagnostic.model_dump_json()


@pytest.mark.asyncio
async def test_preflight_rejection_is_durable_and_never_dispatches(tmp_path, monkeypatch):
    from test_product_tasks import finished, response, service

    proj, setup = project()
    for operation in setup.operations:
        operation.enabled = operation.operation_id == "insertJob"
    proposal = case()
    proposal["plan"]["steps"][0]["arguments"][0]["name"] = "misplacedValue"
    design = dict(interpretation="测试已批准的请求", relevant=True, cases=[proposal],
                  questions=[], not_covered=[])
    svc = service(tmp_path, lambda _: response(design))
    dispatched = []

    def unexpected_http(request):
        dispatched.append(request.method)
        return httpx.Response(503)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport",
                        lambda **_: httpx.MockTransport(unexpected_http))
    stored = svc.intake.save(IntakeInput(name="preflight regression",
                                        document=proj.document.decode("utf-8"),
                                        generic=setup, confirmed=True), "operator")
    task = await svc.submit("operator", stored["id"], "", "p3b23-preflight-regression")
    result = await finished(svc, task)
    summary = result["summary"]
    rejected, = summary["cases"]
    assert rejected["status"] == "REJECTED" and rejected.get("run_id") is None
    diagnostic = rejected["preflight_diagnostic"]
    assert diagnostic["kind"] == "SCHEMA_MISMATCH"
    assert diagnostic["detail"]["step_id"] == "insert"
    assert diagnostic["detail"]["obligation_id"] == "web_rule_1"
    assert dispatched == []
    model_call = summary["design_records"][0]["model_call"]
    with svc.db.connect() as connection:
        usage = connection.execute("SELECT stats FROM model_usage_journal WHERE id=?",
                                   (model_call["local_call_id"],)).fetchone()
    assert json.loads(usage["stats"]) == model_call
    assert model_call["batch"] == "group_1" and model_call["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_deep_agent_trace_can_be_joined_to_its_usage_row(tmp_path):
    pytest.importorskip("deepagents")
    from test_product_agent import session

    from agenttest.product_agent import run_agent

    agent, tasks, *_ = session(tmp_path)
    db = agent.scope.svc.db = TaskStore(tmp_path / "tasks.sqlite3")

    def gateway(_):
        return httpx.Response(200, headers={"request-id": "chat-gateway-fixture"},
            json=dict(id="chat-completion-fixture",
                      choices=[dict(finish_reason="stop", message=dict(content="已记录"))],
                      usage=dict(prompt_tokens=9, completion_tokens=2, total_tokens=11)))

    result = await run_agent(agent.scope, tasks, settings(), transport=httpx.MockTransport(gateway))
    row, = result.call_trace
    assert row["phase"] == "agent" and row["task_id"] == agent.scope.task_id
    assert row["attempt"] == 1 and row["usage_state"] == "KNOWN"
    assert row["provider_request_id"] == "chat-gateway-fixture"
    with db.connect() as connection:
        usage = connection.execute("SELECT stats FROM model_usage_journal WHERE id=?",
                                   (row["local_call_id"],)).fetchone()
    assert all(row[key] == value for key, value in json.loads(usage["stats"]).items())


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["no_id", "ids", "read_timeout", "bad_json", "refused"])
async def test_journal_and_model_trace_share_local_id_and_preserve_usage(tmp_path, result):
    config = settings()
    secret = config.api_key.get_secret_value()
    proj, _ = project()
    context = design_context(proj, "private prompt text", design_groups(proj)[0], 10, 2)
    db = TaskStore(tmp_path / "tasks.sqlite3")
    seen = []

    def gateway(request):
        with db.connect() as connection:
            pending, = list(connection.execute("SELECT id,state,stats FROM model_usage_journal"))
        assert pending["state"] == "PENDING"
        assert json.loads(pending["stats"])["local_call_id"] == pending["id"]
        seen.append(pending["id"])
        if result == "read_timeout":
            raise httpx.ReadTimeout("untrusted transport text", request=request)
        if result == "refused":
            return httpx.Response(503, headers={"x-request-id": "refusal-fixture"})
        content = ('{"cases" []}' if result == "bad_json"
                   else '{"cases":[],"not_covered":[],"questions":[],'
                        '"interpretation":"test","relevant":true}')
        envelope = dict(usage=dict(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            choices=[dict(finish_reason="stop", message=dict(content=content))])
        if result == "ids":
            envelope["id"] = "chatcmpl-fixture"
        # Only the allowlisted request-ID header is accepted, and credential echoes are dropped.
        headers = ({"x-request-id": "gateway-fixture"} if result == "ids"
                   else {"x-request-id": secret, "authorization": "Bearer " + secret})
        return httpx.Response(200, json=envelope, headers=headers)

    planner = JournalPlanner(DesignModel(config, httpx.MockTransport(gateway), profile="generic"),
                             db, "task-id", phase="design", batch="negative_batch_1",
                             previous_attempts=2)
    failures = {"read_timeout": "MODEL_TIMEOUT", "bad_json": "JSON_SYNTAX",
                "refused": "MODEL_SERVICE_ERROR"}
    if result in failures:
        with pytest.raises(PlannerFailure) as caught:
            await planner.generate(context)
        assert caught.value.diagnostic.code == failures[result]
    else:
        await planner.generate(context)
    stats = planner.stats
    with db.connect() as connection:
        row = connection.execute("SELECT state,stats FROM model_usage_journal WHERE id=?",
                                 (stats.local_call_id,)).fetchone()
    assert seen == [stats.local_call_id]
    assert json.loads(row["stats"]) == stats.model_dump(mode="json")
    assert stats.run_id == stats.task_id == "task-id"
    assert (stats.phase, stats.batch, stats.attempt) == ("design", "negative_batch_1", 3)
    assert stats.started_at <= stats.ended_at and stats.elapsed_ms >= 0
    assert len(stats.request_sha256) == 64 and stats.model_name == config.model
    assert stats.provider_request_id == {"ids": "gateway-fixture",
                                         "refused": "refusal-fixture"}.get(result)
    assert stats.provider_response_id == ("chatcmpl-fixture" if result == "ids" else None)
    assert secret not in row["stats"] and "private prompt text" not in row["stats"]
    if result == "read_timeout":
        assert stats.http_status is None and stats.timeout_kind == "READ"
        assert stats.error_type == "MODEL_TIMEOUT"
        assert row["state"] == stats.usage_state == "UNKNOWN" and stats.total_tokens is None
        with pytest.raises(TaskConflict, match="MODEL_USAGE_REQUIRES_RECONCILIATION"):
            await planner.generate(context)
        assert len(seen) == 1
    elif result == "refused":
        assert stats.http_status == 503 and stats.error_type == "MODEL_SERVICE_ERROR"
        assert row["state"] == stats.usage_state == "REFUSED" and stats.total_tokens is None
        assert not db.model_usage_blocked()
    else:
        assert row["state"] == stats.usage_state == "KNOWN"
        assert stats.http_status == 200 and stats.total_tokens == 8
