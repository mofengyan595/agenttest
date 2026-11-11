"""LangGraph round supervision: closed actions, real back edges and deterministic gates."""

import asyncio
import json
import sys
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("langgraph", reason="Round supervision runs on the optional agent graph")

from test_product_design import case, project, settings  # noqa: E402

from agenttest.contracts import load_openapi  # noqa: E402
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project  # noqa: E402
from agenttest.product_design import TestDesignModel as DesignModel  # noqa: E402
from agenttest.product_supervisor import (  # noqa: E402
    NextAction,
    RoundSupervisor,
    SupervisorModel,
    admit_action,
    allowed_targets,
    decision_context,
)
from agenttest.project_intake import IntakeInput
from agenttest.report import ModelCallStats, ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore

pytest.importorskip("langgraph")


def candidates(*ids, action="DESIGN_MORE"):
    return [dict(id=key, action=action, kind="STATUS_EXPECTATION", operations=["insertJob"],
                 policies=[key], method="POST", why="已批准的状态预期还没有执行证据。")
            for key in ids]


class Harness:
    """Zero-arg facts plus the two observation-producing actions, exactly like the product."""

    def __init__(self, targets, *, remaining=None, requests=1, assertions=1, cancel=False,
                 guard=None, asked=None):
        self.targets = list(targets)
        self.remaining = {**dict(requests=8, model_calls=4, rounds=3, usage_known=True),
                          **(remaining or {})}
        self.requests, self.assertions = requests, assertions
        self.cancel, self.guard, self.asked = cancel, guard, asked
        self.attempted, self.calls = [], []

    def facts(self):
        return dict(user_task="帮我测一下包裹管理功能。", approved_scope=[], executed_cases=[],
                    satisfied_obligations=[], open_obligations=[], confirmed_anomalies=[],
                    unconfirmed_causes=[], investigations=[], attempted=list(self.attempted),
                    resources={}, remaining=dict(self.remaining),
                    candidates=[item for item in self.targets
                                if item["id"] not in self.attempted])

    async def design(self, candidate):
        self.attempted.append(candidate["id"])
        self.calls.append(candidate["id"])
        return ActionOutcome(executed=True, cases=1, requests=self.requests,
                             assertions=self.assertions)

    async def probe(self, candidate):
        return await self.design(candidate)

    def ask(self, candidate, question, reason):
        self.asked = dict(candidate=candidate, question=question, reason=reason)
        return dict(id="q", question=question)

    def supervisor(self, model, **kwargs):
        return RoundSupervisor(model, self.facts, self.design, self.probe, self.ask,
                               guard=self.guard,
                               cancelled=lambda: self.cancel, **kwargs)


from agenttest.product_supervisor import ActionOutcome  # noqa: E402


class Answers:
    """Deterministic decision model: every answer is a closed action plus one target id."""

    def __init__(self, *answers):
        self.answers, self.contexts = list(answers), []

    @property
    def stats(self):
        return ModelCallStats(calls=1, input_tokens=40, output_tokens=20, total_tokens=60)

    async def generate(self, context):
        self.contexts.append(context)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def action(name, target=None, reason="已确认的预期仍未验证。"):
    return dict(action=name, target=target, reason=reason)


class Forbidden:
    """A supervisor model that must never be called."""

    stats = ModelCallStats()

    async def generate(self, context):
        raise AssertionError("The round supervisor must stay off by default")


def test_supervision_graph_has_the_only_back_edges():
    harness = Harness(candidates("design:a", "design:b"))
    supervisor = harness.supervisor(Answers(action("FINISH")), max_rounds=2)
    graph = supervisor.graph()
    shape = graph.get_graph()
    nodes = {node for node in shape.nodes if not node.startswith("__")}
    assert nodes == {"gather_facts", "decide", "design_more", "read_probe", "ask_question",
                     "finish"}
    edges = {(edge.source, edge.target) for edge in shape.edges}
    assert ("__start__", "gather_facts") in edges
    assert ("design_more", "gather_facts") in edges
    assert ("read_probe", "gather_facts") in edges
    assert ("ask_question", "__end__") in edges and ("finish", "__end__") in edges
    # The cycle is what makes this an autonomous loop rather than one extra prompt.
    assert ("gather_facts", "decide") in {(e.source, e.target) for e in shape.edges
                                          if e.conditional}
    assert supervisor._recursion_limit == 6 + 3 * 2
    assert "gather_facts" in graph.get_graph().draw_mermaid()


@pytest.mark.asyncio
async def test_design_more_rounds_return_to_observation_and_finish():
    harness = Harness(candidates("design:a", "design:b"))
    model = Answers(action("DESIGN_MORE", "design:a"), action("DESIGN_MORE", "design:b"))
    outcome = await harness.supervisor(model, max_rounds=3).run()
    assert [entry["action"] for entry in outcome.decisions] == ["DESIGN_MORE"] * 2
    assert harness.calls == ["design:a", "design:b"]
    assert outcome.trace == ["gather_facts", "decide", "design_more", "gather_facts", "decide",
                             "design_more", "gather_facts", "finish"]
    # The third decision is never requested: the closed candidate set is exhausted.
    assert len(model.contexts) == 2
    assert outcome.stop_reason == "SUPERVISION_NO_OPEN_OBLIGATION"
    assert [entry["requests"] for entry in outcome.actions] == [1, 1]
    assert outcome.model_calls == 2 and outcome.total_tokens == 120


@pytest.mark.asyncio
async def test_rejected_target_is_repaired_once_and_then_selected():
    harness = Harness(candidates("design:a"))
    model = Answers(action("DESIGN_MORE", "design:missing"), action("DESIGN_MORE", "design:a"))
    outcome = await harness.supervisor(model, max_rounds=1).run()
    first, second = outcome.decisions
    assert first["state"] == "REJECTED" and first["reason"] == "SUPERVISION_TARGET_NOT_OPEN"
    assert second["state"] == "SELECTED" and second["repair_of"] == 0
    assert harness.calls == ["design:a"]
    assert model.contexts[1]["previous_decision_rejected"] == "SUPERVISION_TARGET_NOT_OPEN"


@pytest.mark.asyncio
async def test_repeated_target_is_never_executed_twice():
    harness = Harness(candidates("design:a", "design:b"))
    model = Answers(action("DESIGN_MORE", "design:a"))
    outcome = await harness.supervisor(model, max_rounds=2).run()
    assert harness.calls == ["design:a"]
    assert [entry["state"] for entry in outcome.decisions] == ["SELECTED", "REJECTED",
                                                               "REJECTED"]
    assert outcome.stop_reason == "SUPERVISION_DECISION_REJECTED"
    assert {entry["reason"] for entry in outcome.decisions[1:]} == {
        "SUPERVISION_TARGET_NOT_OPEN"}
    # Defense in depth: even when a caller keeps offering an attempted target, admission
    # refuses it, so no obligation is ever executed twice.
    ok, reason = admit_action(NextAction(action="DESIGN_MORE", target="design:a",
                                         reason="再补一次同样的检查。"),
                              candidates("design:a"), ["design:a"])
    assert not ok and reason == "SUPERVISION_TARGET_REPEATED"


@pytest.mark.asyncio
async def test_two_rounds_without_new_evidence_force_finish():
    harness = Harness(candidates("design:a", "design:b", "design:c"), requests=0, assertions=0)
    model = Answers(action("DESIGN_MORE", "design:a"), action("DESIGN_MORE", "design:b"),
                    action("DESIGN_MORE", "design:c"))
    outcome = await harness.supervisor(model, max_rounds=3).run()
    assert harness.calls == ["design:a", "design:b"]
    assert outcome.stop_reason == "SUPERVISION_NO_PROGRESS"
    assert len(model.contexts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("setting,stop", [
    (dict(cancel=True), "SUPERVISION_CANCELLED"),
    (dict(remaining=dict(requests=0)), "SUPERVISION_REQUEST_BUDGET_EXHAUSTED"),
    (dict(remaining=dict(model_calls=0)), "SUPERVISION_MODEL_BUDGET_EXHAUSTED"),
    (dict(remaining=dict(usage_known=False)), "SUPERVISION_USAGE_UNKNOWN"),
])
async def test_deterministic_gates_stop_before_any_model_call(setting, stop):
    harness = Harness(candidates("design:a"), **{k: v for k, v in setting.items()
                                                if k != "remaining"},
                      remaining=setting.get("remaining"))
    model = Answers(action("DESIGN_MORE", "design:a"))
    outcome = await harness.supervisor(model, max_rounds=2).run()
    assert outcome.stop_reason == stop
    assert model.contexts == [] and harness.calls == []
    assert outcome.trace == ["gather_facts", "finish"]


@pytest.mark.asyncio
async def test_round_budget_and_authority_change_stop_with_their_own_reason():
    harness = Harness(candidates("design:a"))
    outcome = await harness.supervisor(Answers(action("FINISH")), max_rounds=1).run()
    assert outcome.stop_reason == "SUPERVISION_FINISHED" and harness.calls == []

    def guard():
        raise ValueError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")

    changed = Harness(candidates("design:a"), guard=guard)
    outcome = await changed.supervisor(Answers(action("FINISH")), max_rounds=1).run()
    assert outcome.stop_reason == "SUPERVISION_AUTHORITY_CHANGED"
    assert changed.calls == [] and outcome.decisions == []

    changed.cancel = True
    outcome = await changed.supervisor(Answers(action("FINISH")), max_rounds=1).run()
    assert outcome.stop_reason == "SUPERVISION_CANCELLED"


@pytest.mark.asyncio
async def test_question_ends_the_loop_without_any_business_action():
    harness = Harness(candidates("design:a", action="DESIGN_MORE"))
    model = Answers(dict(action="ASK_QUESTION", target=None, reason="缺少已确认的业务规则。",
                         question="删除后再次读取应该返回什么状态？"))
    outcome = await harness.supervisor(model, max_rounds=2).run()
    assert outcome.stop_reason == "SUPERVISION_ASKED_USER"
    assert harness.calls == [] and harness.asked["question"].startswith("删除后")
    assert outcome.question["id"] == "q"
    assert outcome.trace == ["gather_facts", "decide", "ask_question"]


def test_supervisor_model_closes_the_action_and_target_menu():
    model = SupervisorModel(settings())
    door = candidates("design:a", "design:b", action="READ_PROBE")
    context = decision_context(dict(user_task="检查包裹", candidates=door), [])
    payload = json.loads(model.prepare_request(context))
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["required"] == list(schema["properties"])
    assert schema["properties"]["action"]["enum"] == ["FINISH", "READ_PROBE", "ASK_QUESTION"]
    assert schema["properties"]["target"]["anyOf"][1]["enum"] == ["design:a", "design:b"]
    assert set(NextAction.model_fields) == {"action", "target", "reason", "question"}
    # A decoded answer is admitted against the same server facts, never trusted blindly.
    with pytest.raises(ValueError, match="SUPERVISION_TARGET_NOT_OPEN"):
        model._decode_output(json.dumps(dict(action="READ_PROBE", target="design:zzz",
                                             reason="再看一次真实响应。")), context)
    assert allowed_targets(door, "READ_PROBE") == ["design:a", "design:b"]
    ok, reason = admit_action(NextAction(action="FINISH", target="design:a", reason="已完成。"),
                              door, [])
    assert not ok and reason == "SUPERVISION_FINISH_HAS_TARGET"


# ---------------------------------------------------------------- open obligations


def obligation_summary(*operations):
    return dict(groups=[dict(id="group_1", primary=list(operations), operations=list(operations),
                            supported=True)],
                inventory=dict(total=len(operations), operations=[
                    dict(operation_id=name, state="READY", reason="", next_step="")
                    for name in operations]), cases=[])


def test_one_unexecuted_interface_yields_one_obligation_per_approved_policy():
    from agenttest.product_obligations import approved_scope, candidates, executed_evidence

    proj, _ = project()
    summary = obligation_summary("insertJob", "readJob")
    assert executed_evidence(summary) == {}
    scope = approved_scope(proj, summary)
    assert [item["policy_id"] for item in scope] == ["web_rule_1", "web_rule_2"]
    assert [item["id"] for item in candidates(proj, summary, scope, executed_evidence(summary),
                                              allow_negatives=False)] == ["design:web_rule_1",
                                                                          "design:web_rule_2"]
    assert [item["id"] for item in candidates(proj, summary, scope, {}, allow_negatives=False,
                                              attempted=("design:web_rule_1",))] == [
        "design:web_rule_2"]


def test_a_passed_policy_is_not_reopened_just_because_its_endpoint_is_used_elsewhere():
    from agenttest.product_obligations import approved_scope, candidates, satisfied_obligations

    proj, _ = project()
    summary = obligation_summary("insertJob", "readJob")
    scope = approved_scope(proj, summary)
    evidence = {"web_rule_1": dict(passed=True, observed=[201], checks=[1], failed=[]),
                "web_rule_2": dict(passed=True, observed=[200], checks=[1], failed=[])}
    assert candidates(proj, summary, scope, evidence, allow_negatives=False) == []
    assert [item["policy_id"] for item in satisfied_obligations(scope, evidence)] == [
        "web_rule_1", "web_rule_2"]


def test_cleanup_and_probe_executions_count_as_measured_evidence():
    from agenttest.product_obligations import approved_scope, candidates, executed_evidence

    proj, _ = project()
    summary = obligation_summary("insertJob", "readJob")
    summary["cases"] = [dict(case_id="c1", status="FINISHED", outcome="ASSERTION_FAILED",
        evidence=[dict(policy_id="web_rule_1", executed=True, outcome="PASS",
                       checks=[dict(kind="http_status", expected=[201], actual=201,
                                    passed=True)])],
        # The executor really deleted the created resource under its own approved policy.
        cleanup_evidence=[dict(policy_id="web_rule_2", executed=True, outcome="PASS",
                               checks=[dict(kind="http_status", expected=[200], actual=200,
                                            passed=True)])])]
    evidence = executed_evidence(summary)
    assert sorted(evidence) == ["web_rule_1", "web_rule_2"]
    assert evidence["web_rule_2"]["passed"] is True and evidence["web_rule_2"]["cases"] == ["c1"]
    scope = approved_scope(proj, summary)
    assert candidates(proj, summary, scope, evidence, allow_negatives=False) == []
    # A read probe is evidence too, so a measured failure is reported as failed, not untried.
    summary["supervision"] = dict(probes=[dict(policy_id="web_rule_2", run_id="p1",
        outcome="ASSERTION_FAILED", requests=1, checks=[dict(kind="http_status",
        expected=[200], actual=503, passed=False)])])
    summary["cases"] = []
    measured = executed_evidence(summary)
    assert measured["web_rule_2"]["passed"] is False
    assert measured["web_rule_2"]["observed"] == "ASSERTION_FAILED"
    reopened = candidates(proj, summary, scope, measured, allow_negatives=False)
    assert reopened[-1]["id"] == "design:web_rule_2"
    assert reopened[-1]["why"] == "该策略已执行但未通过，需要一条新的、仍受批准范围约束的检查。"
    assert reopened[-1]["observed"] == "ASSERTION_FAILED"


def lifecycle_project():
    """Creation, read and deletion of one resource, linked like the real parcels contract."""
    parcel_id = [dict(name="parcelId", **{"in": "path"}, required=True,
                      schema=dict(type="string"))]
    create = dict(operationId="createParcel", summary="创建包裹",
        requestBody=dict(required=True, content={"application/json": dict(schema=dict(
            type="object", required=["name", "quantity"], properties=dict(
                name=dict(type="string", minLength=1, maxLength=20),
                quantity=dict(type="integer", minimum=1, maximum=3))))}),
        responses={
            "201": dict(description="created", links=dict(
                read=dict(operationId="readParcel",
                          parameters=dict(parcelId="$response.body#/id")),
                remove=dict(operationId="deleteParcel",
                            parameters=dict(parcelId="$response.body#/id")))),
            "422": dict(description="invalid parameters")})
    read = dict(operationId="readParcel", summary="查询包裹", parameters=parcel_id,
                responses={"200": dict(description="ok"), "404": dict(description="missing")})
    delete = dict(operationId="deleteParcel", summary="删除包裹", parameters=parcel_id,
                  responses={"204": dict(description="deleted"),
                             "404": dict(description="missing")})
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="Parcels API", version="1"),
        paths={"/parcels": {"post": create},
               "/parcels/{parcelId}": {"get": read, "delete": delete}}))
    contract = load_openapi(document)
    suggestions = analyze_contract(contract)
    operations = [dict(operation_id=s["operation_id"], alias=s["alias"], statuses=s["statuses"],
        arguments=[{k: v for k, v in a.items() if k in {"location", "name", "value",
            "source_operation", "selector", "generated", "needs_input"}}
            for a in s["arguments"]]) for s in suggestions]
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998",
        isolated_confirmed=True, allow_mutations=True, assisted=True, operations=operations))
    return build_generic_project("parcels", document, contract, setup), setup


def test_a_creation_obligation_carries_its_approved_cleanup_policy():
    from agenttest.product_obligations import approved_scope, candidates

    proj, _ = lifecycle_project()
    policies = {policy.operation_id: policy.id for policy in proj.registry.definitions()
                if not policy.negative}
    assert set(policies) == {"createParcel", "readParcel", "deleteParcel"}
    summary = obligation_summary("createParcel", "readParcel", "deleteParcel")
    scope = approved_scope(proj, summary)
    opened = {item["id"]: item for item in candidates(proj, summary, scope, {},
                                                     allow_negatives=False)}
    creation = opened[f"design:{policies['createParcel']}"]
    assert creation["operations"] == ["createParcel", "deleteParcel"]
    assert creation["cleanup_policies"] == [policies["deleteParcel"]]
    assert creation["why"].endswith("同一场景若创建了资源，还需按已批准的删除策略清理。")
    # A read that needs a created resource carries the same cleanup co-requisite.
    reading = opened[f"design:{policies['readParcel']}"]
    assert reading["operations"] == ["readParcel", "deleteParcel"]
    assert reading["cleanup_policies"] == [policies["deleteParcel"]]


# ---------------------------------------------------------------------- product chain


def service(root, design, supervisor=None):
    return TaskService({}, TaskStore(root / "tasks.sqlite3"), ReportStore(root / "reports"),
        lambda _: None, designer_factory=lambda _: DesignModel(settings(), transport=httpx
            .MockTransport(design), profile="generic"), generic_intake=True, environ={},
        model_status=lambda: "CONFIGURED_MODEL",
        supervisor_factory=(lambda _: supervisor) if supervisor is not None else None)


def with_rounds(svc, rounds):
    """Opt one project into supervision the same way a project registration would."""
    original = svc.project

    def patched(identifier):
        loaded = original(identifier)
        budget = loaded.registration.budget.model_copy(
            update=dict(supervisor_max_rounds=rounds))
        return replace(loaded, registration=loaded.registration.model_copy(
            update=dict(budget=budget)))

    svc.project = patched


async def finished(svc, row):
    for _ in range(400):
        current = svc.db.get(row["id"], "operator")
        if current["status"] in {"FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "CANCELLED"}:
            return current
        await asyncio.sleep(0.01)
    pytest.fail("Task did not terminate")


def response(design):
    return httpx.Response(200, json=dict(usage=dict(prompt_tokens=200, completion_tokens=150,
        total_tokens=350), choices=[dict(finish_reason="stop", message=dict(
            content=json.dumps(design, ensure_ascii=False)))]))


def design_transport(handler):
    """Decode the real design envelope so the fixture answers the group it was asked about."""
    def respond(request):
        payload = json.loads(request.content)
        return response(handler(json.loads(payload["messages"][1]["content"])))
    return respond


def read_case():
    return dict(title="创建后读取任务", kind="normal", plan=dict(name="insert-read", steps=[
        dict(id="insert", operation_id="insertJob", role=None, policy_id="web_rule_1",
             arguments=[dict(location="body", name="name", value="fresh-job",
                             from_step=None, select=None),
                        dict(location="body", name="quantity", value=2, from_step=None,
                             select=None),
                        dict(location="body", name="details",
                             value={"object": [{"key": "labels",
                                                "value": {"array": ["b"]}}]},
                             from_step=None, select=None)]),
        dict(id="read", operation_id="readJob", role=None, policy_id="web_rule_2",
             arguments=[dict(location="path_parameters", name="jobId", value=None,
                             from_step="insert", select="$.id")])]))


def jobs_design(context):
    """Round one covers creation only; the supervision round is asked for the read."""
    if "只补测一个已批准的检查" in context["user_task"]:
        return dict(interpretation="再创建一份任务并读取它", relevant=True, cases=[read_case()],
                    questions=[], not_covered=[])
    return dict(interpretation="创建一份新任务并核对创建结果", relevant=True, cases=[case()],
                questions=[], not_covered=[])


def jobs_target(requests):
    def respond(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json={"id": "job-1"})
        return httpx.Response(200, json={"id": "job-1"})
    return respond


@pytest.mark.asyncio
async def test_supervisor_off_by_default_keeps_the_legacy_round(tmp_path, monkeypatch):
    requests = []
    monkeypatch.setattr(httpx, "AsyncHTTPTransport",
                        lambda **_: httpx.MockTransport(jobs_target(requests)))
    # Base installs have no langgraph: the disabled path must never import the graph module.
    monkeypatch.setitem(sys.modules, "agenttest.product_supervisor", None)
    svc = service(tmp_path, design_transport(jobs_design), supervisor=Forbidden())
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="默认关闭", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "帮我测一下任务功能。", uuid4().hex)
        done = await finished(svc, row)
        assert "supervision" not in done["summary"]
        assert len(requests) == 1 and requests[0] == ("POST", "/jobs")
        assert done["summary"]["complete"]
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_open_obligation_gets_one_more_real_test(tmp_path, monkeypatch):
    requests = []
    monkeypatch.setattr(httpx, "AsyncHTTPTransport",
                        lambda **_: httpx.MockTransport(jobs_target(requests)))
    answers = Answers(action("DESIGN_MORE", "design:web_rule_2"))
    svc = service(tmp_path, design_transport(jobs_design), supervisor=answers)
    with_rounds(svc, 2)
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="补测读取", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "帮我测一下任务功能。", uuid4().hex)
        done = await finished(svc, row)
        summary = done["summary"]
        supervision = summary["supervision"]
        assert supervision["stop_reason"] == "SUPERVISION_NO_OPEN_OBLIGATION"
        assert [entry["action"] for entry in supervision["decisions"]] == ["DESIGN_MORE"]
        assert len(answers.contexts) == 1
        assert answers.contexts[0]["candidates"][0]["id"] == "design:web_rule_2"
        assert requests == [("POST", "/jobs"), ("POST", "/jobs"), ("GET", "/jobs/job-1")]
        assert len(summary["groups"]) == 2
        assert summary["groups"][-1]["id"].startswith("supervision_1_")
        assert summary["design_records"][-1]["supervision_round"] == 1
        assert summary["design_records"][-1]["supervision_target"] == "design:web_rule_2"
        assert {op for item in summary["cases"] for op in item["executed_operations"]} == {
            "insertJob", "readJob"}
        assert all(item["outcome"] == "PASS" for item in summary["cases"])
        assert summary["complete"] and summary["checks_passed"]
        assert supervision["trace"] == ["gather_facts", "decide", "design_more", "gather_facts",
                                        "finish"]
        assert supervision["graph_nodes"] == ["ask_question", "decide", "design_more", "finish",
                                              "gather_facts", "read_probe"]
        assert supervision["model_calls"] == 1
        assert supervision["total_tokens"] == 60
        assert supervision["actions"][0]["requests"] == 2
        assert supervision["actions"][0]["assertions"] >= 1
        assert done["status"] == "FINISHED"
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_no_open_obligation_never_calls_the_model(tmp_path, monkeypatch):
    requests = []
    monkeypatch.setattr(httpx, "AsyncHTTPTransport",
                        lambda **_: httpx.MockTransport(jobs_target(requests)))

    def complete_design(context):
        return dict(interpretation="创建并读取一份新任务", relevant=True, cases=[read_case()],
                    questions=[], not_covered=[])
    answers = Answers(action("DESIGN_MORE", "design:web_rule_1"))
    svc = service(tmp_path, design_transport(complete_design), supervisor=answers)
    with_rounds(svc, 2)
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="已完成义务", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "帮我测一下任务功能。", uuid4().hex)
        done = await finished(svc, row)
        supervision = done["summary"]["supervision"]
        assert answers.contexts == []
        assert supervision["decisions"] == [] and supervision["actions"] == []
        assert supervision["stop_reason"] == "SUPERVISION_NO_OPEN_OBLIGATION"
        assert done["summary"]["complete"]
    finally:
        await svc.close()


def state_project():
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="State", version="1"),
        paths={"/state": {"get": dict(operationId="readState",
                                      responses={"200": dict(description="ok")})}}))
    config = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998", assisted=True,
        isolated_confirmed=True, max_requests=6, operations=[dict(operation_id="readState",
            alias="读取状态", statuses=[200])]))
    return document, config


@pytest.mark.asyncio
async def test_read_probe_reuses_one_approved_read_without_writing(tmp_path, monkeypatch):
    requests, methods = [], []
    read_case_row = dict(title="检查当前状态", kind="normal", plan=dict(name="state", steps=[dict(
        id="read", operation_id="readState", role=None, policy_id="web_rule_1", arguments=[])]))

    def target(request):
        methods.append(request.method)
        assert request.method == "GET", "A supervision probe must never write"
        requests.append(request.url.path)
        return httpx.Response(500, json={"state": "broken"})

    def design(context):
        return dict(interpretation="检查状态是否正常", relevant=True, cases=[read_case_row],
                    questions=[], not_covered=[])

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    answers = Answers(action("READ_PROBE", "probe:web_rule_1"))
    svc = service(tmp_path, design_transport(design), supervisor=answers)
    with_rounds(svc, 2)
    try:
        document, config = state_project()
        saved = svc.intake.save(IntakeInput(name="故障状态探针", document=document,
            generic=config, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "检查状态是否正常", uuid4().hex)
        done = await finished(svc, row)
        supervision = done["summary"]["supervision"]
        # One business read, the workflow's own bounded investigation, then the probe.
        assert methods == ["GET", "GET", "GET"] and set(requests) == {"/state"}
        assert len(done["summary"]["cases"]) == 1
        probe = supervision["probes"][0]
        assert probe["operation_id"] == "readState" and probe["outcome"] == "ASSERTION_FAILED"
        assert probe["requests"] == 1 and probe["checks"][0]["actual"] == 500
        assert probe["run_id"] != done["summary"]["cases"][0]["run_id"]
        assert supervision["actions"][0]["action"] == "READ_PROBE"
        assert supervision["actions"][0]["executed"] is True
        assert supervision["stop_reason"] == "SUPERVISION_NO_OPEN_OBLIGATION"
        assert len(answers.contexts) == 1
        assert done["summary"]["context_usage"]["requests"] == 3
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_ask_question_reuses_the_pending_question_channel(tmp_path, monkeypatch):
    requests = []

    def target(request):
        requests.append(request.method)
        return httpx.Response(500, json={"state": "broken"})

    read_policy_case = dict(title="检查当前状态", kind="normal", plan=dict(name="state", steps=[
        dict(id="read", operation_id="readState", role=None, policy_id="web_rule_1",
             arguments=[])]))

    def design(context):
        return dict(interpretation="检查状态是否正常", relevant=True, cases=[read_policy_case],
                    questions=[], not_covered=[])

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    answers = Answers(dict(action="ASK_QUESTION", target=None,
                           reason="缺少已确认的删除后读取规则。",
                           question="删除后再次读取，接口应该返回 404 还是 200？"))
    svc = service(tmp_path, design_transport(design), supervisor=answers)
    with_rounds(svc, 2)
    try:
        document, config = state_project()
        saved = svc.intake.save(IntakeInput(name="待确认状态", document=document,
            generic=config, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "检查状态是否正常", uuid4().hex)
        done = await finished(svc, row)
        summary = done["summary"]
        question = [item for item in summary["questions"]
                    if item["id"].startswith("supervision_")]
        assert len(question) == 1 and question[0]["kind"] == "clarification"
        assert question[0]["question"].startswith("删除后再次读取")
        assert summary["supervision"]["stop_reason"] == "SUPERVISION_ASKED_USER"
        assert summary["supervision"]["actions"] == []
        assert [entry["state"] for entry in summary["supervision"]["decisions"]] == ["SELECTED"]
        assert requests == ["GET", "GET"]
        assert not summary["complete"]
    finally:
        await svc.close()
