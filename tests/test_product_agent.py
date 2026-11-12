"""The product's autonomous runtime: narrow tools, deterministic refusals, honest verdicts.

These tests never reach a real network or a real model. They prove the parts a harness can
get wrong without anyone noticing: which tools are bound, that a typed refusal reaches the
model instead of a silent generic error, that a repeated action is admitted only when it is
genuinely new, and that completion/budget/authority are decided by code rather than prose.
"""

import json
from dataclasses import replace
from importlib.util import find_spec
from types import SimpleNamespace

import httpx
import pytest
from test_product_supervisor import lifecycle_project, obligation_summary

from agenttest.goal_suite import SuiteLedger
from agenttest.llm_planner import ModelSettings
from agenttest.policies import PolicyRegistry
from agenttest.product_agent import (
    TOOL_NAMES,
    AgentRuntimeUnavailable,
    AgentSession,
    _absence_approvals,
    resolve_engine,
    run_agent,
    verify_catalogue,
)
from agenttest.project_goals import ProjectBudget
from agenttest.report import CheckResult, Outcome, ReportStore, RunReport, StepResult
from agenttest.task_completion import evaluate

FUNCTIONAL_GOAL = "帮我测一下包裹管理功能。"
# The runtime tests below drive a real create_deep_agent; a profile without the Deep
# Agents extra (the CI "agent" profile installs the Fake-LLM workflow only) must skip them
# instead of failing on an import. Everything else in this file is harness-independent.
requires_deep_agents = pytest.mark.skipif(find_spec("deepagents") is None,
                                          reason="Deep Agents extra is not installed")


def budget_project(**budget):
    base = ProjectBudget(**budget)
    registration = SimpleNamespace(budget=base)
    return SimpleNamespace(registration=registration)


def service(tmp_path):
    return SimpleNamespace(reports=ReportStore(tmp_path), environ={},
                           _shutting_down=False, model_status=lambda: "CONFIGURED_MODEL")


class FakeTasks:
    """The two deterministic actions the runtime may call, with no HTTP at all."""

    DEFAULT_ROUND = "安排一轮测试"

    def __init__(self, effect=None):
        self.effect = effect
        self.calls = []
        self.rounds = 0

    def next_supervision_round(self, scope):
        self.rounds += 1
        return self.rounds

    async def run_group(self, scope, group, *, position=None, future_groups=(),
                        supervision=None):
        self.calls.append(dict(kind="group", group_id=group["id"],
                               supervision=supervision))
        if self.effect is not None:
            self.effect(scope.summary, group, supervision)

    async def supervise_design(self, scope, candidate):
        self.calls.append(dict(kind="design", candidate=candidate["id"]))
        return SimpleNamespace(executed=True, cases=1, requests=2, assertions=1,
                               detail=dict(planned=1), note=None)

    async def supervise_read(self, scope, candidate):
        self.calls.append(dict(kind="probe", candidate=candidate["id"]))
        scope.state.setdefault("probes", []).append(
            dict(policy_id=candidate["policies"][0], operation_id=candidate["operations"][0],
                 run_id="probe-run", outcome="PASS",
                 checks=[dict(kind="status", selector="status", expected=200, actual=200,
                              passed=True)]))
        return SimpleNamespace(executed=True, cases=0, requests=1, assertions=1,
                               detail=dict(planned=1), note=None)


def evidence(summary, group, policies):
    """One executed case per approved policy, exactly the shape the executor writes."""
    summary["design_records"].append(dict(group_id=group["id"], status="DESIGNED",
                                          reason="EXECUTED", planning_engine="deterministic"))
    summary["cases"].append(dict(
        case_id="case_" + group["id"], run_id="run_" + group["id"], status="FINISHED",
        outcome="PASS", executed_operations=list(group["primary"]),
        executed_steps=len(policies), planned_steps=len(policies), http_requests=2,
        executed_policy_ids=list(policies),
        evidence=[dict(operation_id="createParcel", policy_id=policy_id, executed=True,
                       outcome="PASS", checks=[dict(kind="status", selector="status",
                                                    expected=201, actual=201, passed=True)],
                       attempts=[dict(status_code=201)])
                  for policy_id in policies]))
    group["status"] = "DESIGNED"


def session(tmp_path, text=FUNCTIONAL_GOAL, *, effect=None, state=None,
            ledger=None, guard=None):
    project, _ = lifecycle_project()
    policies = [policy.id for policy in project.registry.definitions() if not policy.negative]
    summary = obligation_summary("createParcel", "readParcel", "deleteParcel")
    summary.update(cases=[], design_records=[], questions=[], not_covered=[],
                   focus=dict(mode="all_ready"), budget=dict(max_requests=24))
    scope = SimpleNamespace(svc=service(tmp_path), project=project, summary=summary,
                            text=text, state=state if state is not None else {},
                            ledger=ledger or SuiteLedger(24, 8, 100_000),
                            guard=guard or (lambda: None), max_seconds=60.0,
                            task_id="task_1")
    effect = effect or (lambda summary, group, supervision: evidence(summary, group, policies))
    tasks = FakeTasks(effect)
    return AgentSession(scope=scope, tasks=tasks), tasks, project, policies, summary


# ------------------------------------------------------------------ engine resolution


def test_auto_uses_deep_agents_only_when_the_deployment_configured_it(monkeypatch):
    import agenttest.product_agent as runtime

    project = budget_project()
    monkeypatch.setattr(runtime, "_harness_available", lambda: False)
    assert runtime.resolve_engine(project, SimpleNamespace(agent_model_factory=lambda p: p)) == (
        "DETERMINISTIC", "AGENT_RUNTIME_DEPENDENCIES_NOT_INSTALLED")
    assert runtime.resolve_engine(project, SimpleNamespace(agent_model_factory=None)) == (
        "DETERMINISTIC", "AGENT_RUNTIME_MODEL_NOT_CONFIGURED")
    monkeypatch.setattr(runtime, "_harness_available", lambda: True)
    assert runtime.resolve_engine(project, SimpleNamespace(agent_model_factory=lambda p: p)) == (
        "DEEP_AGENTS", "DEFAULT_DEEP_AGENTS")


def test_an_explicit_engine_fails_closed_instead_of_silently_degrading():
    with pytest.raises(AgentRuntimeUnavailable):
        resolve_engine(budget_project(loop_engine="DEEP_AGENTS"),
                       SimpleNamespace(agent_model_factory=None))
    assert resolve_engine(budget_project(loop_engine="DETERMINISTIC"),
                          SimpleNamespace(agent_model_factory=None)) == (
        "DETERMINISTIC", "PROJECT_REQUESTED_DETERMINISTIC")
    assert resolve_engine(budget_project(loop_engine="LEGACY_SUPERVISOR"),
                          SimpleNamespace(agent_model_factory=None)) == (
        "LEGACY_SUPERVISOR", "PROJECT_REQUESTED_LEGACY_SUPERVISOR")


def test_the_bound_catalogue_must_be_exactly_the_product_tools():
    def agent(*names):
        bound = SimpleNamespace(tools_by_name={name: object() for name in names})
        return SimpleNamespace(nodes={"tools": SimpleNamespace(bound=bound)})

    catalogue = verify_catalogue(agent(*TOOL_NAMES, "read_file"))
    assert catalogue["bound_tools"] == sorted([*TOOL_NAMES, "read_file"])
    assert catalogue["missing"] == []
    with pytest.raises(AgentRuntimeUnavailable, match="TOOL_CATALOGUE_NOT_NARROWED"):
        verify_catalogue(agent(*TOOL_NAMES, "read_file", "write_file", "execute"))
    with pytest.raises(AgentRuntimeUnavailable, match="TOOL_CATALOGUE_NOT_NARROWED"):
        verify_catalogue(agent(*TOOL_NAMES, "run_python"))
    with pytest.raises(AgentRuntimeUnavailable, match="TOOL_CATALOGUE_NOT_NARROWED"):
        verify_catalogue(agent("get_contract"))


# ------------------------------------------------------------------ scope and refusals


def test_scope_view_is_the_only_place_ids_come_from(tmp_path):
    session_, _, project, policies, _ = session(tmp_path)
    view = session_.scope_view()
    assert view["task"]["kind"] == "FUNCTIONAL"
    assert [group["group_id"] for group in view["groups"]] == ["group_1"]
    assert view["groups"][0]["next_step"] == dict(tool="run_test_group",
                                                  arguments={"group_id": "group_1"})
    candidate_ids = [item["id"] for item in view["candidates"]]
    assert candidate_ids == [f"design:{policy_id}" for policy_id in policies[:3]]
    assert all(item["next_step"]["tool"] == "run_open_obligation"
               for item in view["candidates"])
    assert view["completion"]["complete"] is False
    assert view["budget"]["requests_left"] == 24


@pytest.mark.asyncio
async def test_an_unknown_group_is_refused_with_the_valid_ids(tmp_path):
    session_, tasks, _, _, _ = session(tmp_path)
    payload = await session_.run_group("group-99")
    assert payload["ok"] is False
    assert payload["error"]["code"] == "UNKNOWN_GROUP_ID"
    assert payload["valid_group_ids"] == ["group_1"]
    assert tasks.calls == []


@pytest.mark.asyncio
async def test_a_rejected_design_reports_the_compiler_code_and_a_repair_hint(tmp_path):
    """MODEL_INVALID_RESPONSE alone told the model nothing; the compiler code is the repair key."""

    def rejected(summary, group, supervision):
        summary["design_records"].append(dict(
            group_id=group["id"], status="REJECTED", reason="MODEL_INVALID_RESPONSE",
            diagnostic=dict(layer="model", code="PLANNING_INSTANCE_COVERAGE",
                            fields=["instance.ai_2"],
                            detail=dict(missing=["ai_2"],
                                        operations={"ai_2": "readParcel"},
                                        hint="补上产生者步骤。"))))
        group["status"] = "NOT_EXECUTED"
        group["reason"] = "MODEL_INVALID_RESPONSE"

    session_, _, _, _, _ = session(tmp_path, effect=rejected)
    payload = await session_.run_group("group_1")
    assert payload["ok"] is False
    assert payload["requests"] == 0
    assert payload["error"]["code"] == "PLANNING_INSTANCE_COVERAGE"
    assert payload["error"]["fields"] == ["instance.ai_2"]
    assert payload["error"]["detail"]["missing"] == ["ai_2"]
    assert payload["error"]["hint"] == "补上产生者步骤。"
    assert payload["design"]["code"] == "PLANNING_INSTANCE_COVERAGE"
    assert payload["design"]["reason"] == "MODEL_INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_the_same_group_is_not_re_executed_without_a_new_design_note(tmp_path):
    session_, tasks, _, _, _ = session(tmp_path)
    first = await session_.run_group("group_1")
    assert first["ok"] is True and first["cases"][0]["run_id"] == "run_group_1"
    second = await session_.run_group("group_1")
    assert second["ok"] is False
    assert second["error"]["code"] == "GROUP_ALREADY_MEASURED"
    assert len(tasks.calls) == 1
    retry = await session_.run_group("group_1", "补充：只验证删除后的 404 缺失返回值。")
    assert retry["ok"] is True
    assert tasks.calls[-1]["supervision"]["text"].endswith("补充：只验证删除后的 404 缺失返回值。")
    assert len(tasks.calls) == 2


@pytest.mark.asyncio
async def test_a_rejected_design_tells_the_model_to_supply_a_new_constraint(tmp_path):
    def reject(summary, group, supervision):
        summary["design_records"].append(dict(group_id=group["id"], status="NOT_EXECUTED",
                                              reason="PLAN_PREFLIGHT_REJECTED"))
        group["status"] = "NOT_EXECUTED"

    session_, _, _, _, _ = session(tmp_path, effect=reject)
    payload = await session_.run_group("group_1")
    assert payload["ok"] is False
    assert payload["error"]["code"] == "PLAN_PREFLIGHT_REJECTED"
    assert payload["error"]["retryable"] is True
    assert "design_note" in payload["error"]["hint"]


@pytest.mark.asyncio
async def test_an_unknown_obligation_candidate_is_refused(tmp_path):
    session_, tasks, _, _, _ = session(tmp_path)
    payload = await session_.run_obligation("design:not-a-policy")
    assert payload["error"]["code"] == "UNKNOWN_CANDIDATE_ID"
    assert "design:web_rule_1" in payload["valid_candidate_ids"]
    assert tasks.calls == []


@pytest.mark.asyncio
async def test_an_obligation_action_uses_the_server_decided_action(tmp_path):
    session_, tasks, _, _, _ = session(tmp_path)
    projected = session_.scope_view()["candidates"][0]
    payload = await session_.run_obligation(projected["id"])
    assert payload["ok"] is True
    assert payload["action"] == "DESIGN_MORE"
    assert tasks.calls == [dict(kind="design", candidate=projected["id"])]


@pytest.mark.asyncio
async def test_a_read_only_candidate_runs_a_probe_instead_of_a_write(tmp_path, monkeypatch):
    session_, tasks, _, _, _ = session(tmp_path)
    monkeypatch.setattr(session_, "_facts", lambda remaining=None: dict(candidates=[dict(
        id="probe:web_rule_2", action="READ_PROBE", policies=["web_rule_2"],
        operations=["readJob"], why="该只读策略还没有执行证据。")]))
    payload = await session_.run_obligation("probe:web_rule_2")
    assert payload["executed"] is True
    assert payload["action"] == "READ_PROBE"
    assert tasks.calls == [dict(kind="probe", candidate="probe:web_rule_2")]
    assert list(session_.run_ids()) == ["probe-run"]


@pytest.mark.asyncio
async def test_requests_budget_and_stop_reason_gate_every_action(tmp_path):
    exhausted = SuiteLedger(0, 8, 100_000)
    session_, tasks, _, _, summary = session(tmp_path, ledger=exhausted)
    payload = await session_.run_group("group_1")
    assert payload["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"
    assert payload["error"]["retryable"] is False
    assert tasks.calls == []
    assert summary["agent_runtime"]["tool_calls"] == []

    session_.stop = "ANSWER"
    stopped = await session_.run_group("group_1")
    assert stopped["error"]["code"] == "RUNTIME_STOPPED"


@pytest.mark.asyncio
async def test_a_changed_project_identity_stops_the_runtime(tmp_path):
    def guard():
        raise ValueError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")

    session_, tasks, _, _, _ = session(tmp_path, guard=guard)
    payload = await session_.run_group("group_1")
    assert payload["error"]["code"] == "AUTHORITY_CHANGED"
    assert payload["error"]["retryable"] is False
    assert tasks.calls == []
    assert session_.stop == "AUTHORITY_CHANGED"


@pytest.mark.asyncio
async def test_reports_and_runs_come_only_from_this_round(tmp_path):
    session_, _, _, _, _ = session(tmp_path)
    refused = session_.report_view("run_made_up")
    assert refused["error"]["code"] == "UNKNOWN_RUN_ID"
    await session_.run_group("group_1")
    assert list(session_.run_ids()) == ["run_group_1"]
    # the case exists but its report file was never written by this fake: that is a refusal,
    # not an empty success and not a crash
    assert session_.report_view("run_group_1")["error"]["code"] == "REPORT_UNAVAILABLE"


def test_absence_approval_is_an_ordinary_404_policy_with_a_delete_producer(tmp_path):
    project, _ = lifecycle_project()
    definitions = list(project.registry.definitions())
    read = next(policy for policy in definitions if policy.operation_id == "readParcel")
    deletion = next(policy for policy in definitions if policy.operation_id == "deleteParcel")
    absence = read.model_copy(update=dict(
        id="web_rule_7",
        assertions=read.assertions.model_copy(update=dict(
            status=read.assertions.status.model_copy(update=dict(codes=[404])),
            source="operator: after deleteParcel the same parcel must answer 404"))))
    project = replace(project, registry=PolicyRegistry(definitions + [absence]))
    rows = _absence_approvals(project)
    assert [row["policy_id"] for row in rows] == ["web_rule_7"]
    assert rows[0]["approved_statuses"] == [404]
    assert rows[0]["admissible"] is True
    assert rows[0]["establishing_delete_policy_ids"] == [deletion.id]
    assert rows[0]["bound_source_policies"] == [read.binding_rules[0].source_policy_id]


# ------------------------------------------------------------------ real Deep Agents loop


def scripted(*messages):
    from langchain_core.messages import AIMessage

    from agenttest.deep_harness.loop import scripted_model

    observed = []
    return scripted_model([AIMessage(content=content, **extra) for content, extra in messages],
                          observed, "ecnu-max"), observed


def tool_turn(name, call_id, **args):
    return "", dict(tool_calls=[dict(name=name, id=call_id, args=args)])


@pytest.mark.asyncio
@requires_deep_agents
@pytest.mark.parametrize(("goal", "name", "heading", "action", "args"), [
    (FUNCTIONAL_GOAL, "api-functional-testing", "功能测试", "run_test_group",
     {"group_id": "group_1", "design_note": "同一资源先读取再清理"}),
    ("删除功能刚改过，帮我回归一下。", "api-regression-testing", "回归测试",
     "get_approved_policies", {}),
    ("包裹接口偶尔报错，帮我看看。", "api-failure-investigation", "失败调查",
     "investigate_failure", {"run_id": "invented"}),
])
async def test_official_skill_disclosure_and_domain_tool_loop(
        tmp_path, goal, name, heading, action, args):
    from agenttest.deep_harness.skills import SKILL_NAMES, skill_path

    path = skill_path(name)
    model, observed = scripted(
        tool_turn("read_file", "skill-1", file_path=path),
        tool_turn(action, "domain-1", **args),
        ("已收集证据。", {}))
    session_, tasks, _, _, _ = session(tmp_path, goal)
    result = await run_agent(session_.scope, tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=4, model=model)
    first, second, _ = result.session.context_trace
    assert first["skills_discovered"] == sorted(map(skill_path, SKILL_NAMES))
    assert first["skills_in_context"] == []
    assert second["skills_in_context"] == [path]
    assert not any(f"# {heading}" in (item.get("content") or "")
                   for item in observed[0]["payload"]["messages"])
    answer = next(item for item in observed[1]["payload"]["messages"]
                  if item["role"] == "tool" and item["tool_call_id"] == "skill-1")
    assert f"# {heading}" in answer["content"]
    assert result.catalogue["bound_tools"] == sorted([*TOOL_NAMES, "read_file"])
    assert all(result.enforcement["denied"].values())
    assert [row["name"] for row in result.transcript if row["kind"] == "TOOL_CALL"] == [
        "read_file", action]


@pytest.mark.asyncio
@requires_deep_agents
async def test_product_tools_work_without_skills(tmp_path):
    session_, tasks, *_ = session(tmp_path)
    model, _ = scripted(tool_turn("get_test_scope", "scope"), ("完成读取。", {}))
    result = await run_agent(session_.scope, tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=3, model=model, enable_skills=False)
    assert result.stop_reason == "ANSWER"
    assert result.session.context_trace[0]["skills_discovered"] == []
    assert "groups" in result.transcript[1]["preview"]


@pytest.mark.asyncio
@requires_deep_agents
async def test_native_context_editing_allows_full_read_after_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr("agenttest.product_agent.CONTEXT_CLEAR_TRIGGER_TOKENS", 1)
    model, observed = scripted(
        tool_turn("get_contract", "original"),
        *(tool_turn("get_approved_policies", f"policy-{index}") for index in range(4)),
        tool_turn("get_contract", "pointer"),
        tool_turn("get_contract", "refreshed", refresh=True),
        ("已恢复契约。", {}))
    session_, tasks, *_ = session(tmp_path)
    result = await run_agent(session_.scope, tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=9, model=model)
    assert result.session.context_trace[-1]["cleared_tool_results"] > 0
    last = {row["tool_call_id"]: row["content"] for row in observed[-1]["payload"]["messages"]
            if row["role"] == "tool"}
    assert json.loads(last["pointer"])["next_step"]["arguments"] == {"refresh": True}
    assert "operations" in json.loads(last["refreshed"])
    assert result.session.ledger.requests == 0


@pytest.mark.asyncio
@requires_deep_agents
async def test_compaction_keeps_persisted_failure_and_cleanup_available(tmp_path, monkeypatch):
    monkeypatch.setattr("agenttest.product_agent.CONTEXT_CLEAR_TRIGGER_TOKENS", 1)
    session_, tasks, _, _, summary = session(tmp_path)
    failure = StepResult(step_id="read", operation_id="readParcel", method="GET",
                         path_template="/parcels/{id}", outcome=Outcome.ASSERTION_FAILED,
                         reason="Unexpected status", policy_sha256="approved",
                         checks=[CheckResult(kind="http_status", passed=False,
                                             expected=[200], actual=503)])
    report = RunReport(scenario_sha256="scenario", contract_sha256="contract",
                       environment="fixture", target_origin="http://127.0.0.1:18991",
                       outcome=Outcome.ASSERTION_FAILED, completed=True,
                       cleanup="cleanup pending",
                       resource_lifecycle=[dict(status="CLEANUP_PENDING", create_step="create")],
                       steps=[failure] * 4)
    session_.svc.reports.save(report)
    summary["cases"].append(dict(run_id=report.run_id, outcome=report.outcome.value,
                                 resource_lifecycle=report.resource_lifecycle))
    model, observed = scripted(
        tool_turn("read_test_report", "first-page", run_id=report.run_id),
        *(tool_turn("get_contract", f"contract-{index}") for index in range(4)),
        tool_turn("get_test_coverage", "coverage"),
        tool_turn("read_test_report", "last-page", run_id=report.run_id, step_offset=3),
        ("证据仍为断言失败，清理待处理。", {}))
    result = await run_agent(session_.scope, tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=9, model=model)
    last = {row["tool_call_id"]: row["content"] for row in observed[-1]["payload"]["messages"]
            if row["role"] == "tool"}
    assert result.session.context_trace[-1]["cleared_tool_results"] > 0
    coverage, page = json.loads(last["coverage"]), json.loads(last["last-page"])
    assert coverage["evidence_refs"] == [dict(run_id=report.run_id, outcome="ASSERTION_FAILED")]
    assert coverage["resources"]["pending"][0]["status"] == "CLEANUP_PENDING"
    assert page["outcome"] == "ASSERTION_FAILED" and page["steps_total"] == 4
    check, = page["steps"][0]["checks"]
    assert (check["passed"], check["expected"], check["actual"]) == (False, [200], 503)
    assert session_.svc.reports.read(report.run_id).steps == report.steps


@pytest.mark.asyncio
@requires_deep_agents
async def test_schema_error_and_tool_limit_use_real_tool_messages(tmp_path):
    model, observed = scripted(
        tool_turn("read_test_report", "invalid", step_limit=0),
        tool_turn("run_test_group", "blocked", group_id="group_1"),
        ("这条消息不应生成。", {}))
    session_, tasks, *_ = session(tmp_path)
    result = await run_agent(session_.scope, tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=4, max_tool_calls=1, model=model)
    schemas = {entry["function"]["name"]: entry["function"]["parameters"]
               for entry in observed[0]["payload"]["tools"]}
    assert schemas["read_test_report"]["properties"]["step_limit"]["minimum"] == 1
    assert "run_id" in schemas["read_test_report"]["required"]
    feedback = next(row for row in observed[1]["payload"]["messages"] if row["role"] == "tool")
    assert feedback["tool_call_id"] == "invalid" and "run_id" in feedback["content"]
    assert result.stop_reason == "TOOL_CALL_LIMIT"
    assert tasks.calls == [] and len(observed) == 2


@pytest.mark.asyncio
@requires_deep_agents
@pytest.mark.parametrize(("failure", "stop"), [("refusal", "MODEL_ERROR"),
                                               ("defect", "RUNTIME_ERROR")])
async def test_model_refusal_or_unexpected_failure_is_reported(tmp_path, failure, stop):
    def reply(_request):
        if failure == "defect":
            raise RuntimeError("adapter defect")
        return httpx.Response(200, json=dict(choices=[dict(message=dict(
            role="assistant", content="", refusal="fixture refusal"))]))

    settings = ModelSettings(model="ecnu-max", base_url="https://unit.invalid/v1",
                             api_key="fixture-not-a-key", max_input_tokens=60000,
                             max_total_tokens=100000)
    session_, tasks, *_ = session(tmp_path)
    result = await run_agent(session_.scope, tasks, settings, max_model_calls=4,
                             transport=httpx.MockTransport(reply))
    assert result.stop_reason == stop
    assert result.model_totals["calls"] == 1
    assert tasks.calls == [] and result.session.ledger.requests == 0


@pytest.mark.asyncio
@requires_deep_agents
async def test_the_model_chooses_a_real_tool_and_the_run_is_auditable(tmp_path):
    model, observed = scripted(("", dict(tool_calls=[dict(
        name="get_test_scope", args={}, id="call-1")])), ("我还没看到真实证据。", {}))
    session_, _, project, _, summary = session(tmp_path)
    result = await run_agent(session_.scope, session_.tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=3, model=model)
    assert result.stop_reason == "ANSWER"
    assert result.catalogue["missing"] == []
    assert set(result.catalogue["bound_tools"]) >= set(TOOL_NAMES)
    assert result.enforcement["boundary_rejected"] is True
    assert result.enforcement["files_written"] == []
    assert [row["kind"] for row in result.transcript] == ["TOOL_CALL", "TOOL_RESULT", "ANSWER"]
    assert [row["tool"] for row in result.session.tool_calls] == ["get_test_scope"]
    assert summary["agent_runtime"]["engine"] == "DEEP_AGENTS"
    assert summary["agent_runtime"]["stop_reason"] == "ANSWER"
    completion = evaluate(project, summary, FUNCTIONAL_GOAL,
                          stop=dict(reason=result.stop_reason))
    assert completion["complete"] is False
    assert completion["flags"]["evidence_produced"] is False
    assert result.answer == "我还没看到真实证据。"
    assert sorted(observed[0]["tool_names"]) == sorted([*TOOL_NAMES, "read_file"])


@pytest.mark.asyncio
@requires_deep_agents
async def test_an_invented_identifier_becomes_a_structured_tool_error(tmp_path):
    model, observed = scripted(("", dict(tool_calls=[dict(
        name="run_test_group", args={"group_id": "group-99"}, id="call-1")])),
        ("收到，停止。", {}))
    session_, _, _, _, summary = session(tmp_path)
    result = await run_agent(session_.scope, session_.tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=3, model=model)
    tool_results = [row for row in result.transcript if row["kind"] == "TOOL_RESULT"]
    assert tool_results and "UNKNOWN_GROUP_ID" in tool_results[0]["preview"]
    assert "valid_group_ids" in tool_results[0]["preview"]
    assert summary["agent_runtime"]["tool_calls"] == []


@pytest.mark.asyncio
@requires_deep_agents
async def test_a_model_that_never_calls_a_tool_produces_no_evidence_and_completes_nothing(
        tmp_path):
    model, _ = scripted(("我先解释一下计划。", {}), ("结束。", {}))
    session_, tasks, project, _, summary = session(tmp_path)
    result = await run_agent(session_.scope, session_.tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=3, model=model)
    assert result.stop_reason == "ANSWER"
    assert result.session.tool_calls == []
    assert tasks.calls == []
    assert session_.ledger.requests == 0
    completion = evaluate(project, summary, FUNCTIONAL_GOAL,
                          stop=dict(reason=result.stop_reason))
    assert completion["complete"] is False
    assert completion["checks_passed"] is False
    assert completion["flags"]["evidence_produced"] is False
    assert "EVIDENCE_PRODUCED" in completion["unmet"]


@pytest.mark.asyncio
@requires_deep_agents
async def test_the_model_call_limit_ends_the_run_with_the_evidence_it_already_has(tmp_path):
    model, _ = scripted(("", dict(tool_calls=[dict(
        name="get_test_scope", args={}, id="call-1")])),
        ("", dict(tool_calls=[dict(name="get_test_scope", args={}, id="call-2")])),
        ("还应该再说一次。", {}))
    session_, _, project, _, summary = session(tmp_path)
    result = await run_agent(session_.scope, session_.tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=1, model=model)
    assert result.stop_reason in {"MODEL_CALL_LIMIT", "ANSWER"}
    assert result.session.tool_calls, "证据不能被预算停止抹掉"
    completion = evaluate(project, summary, FUNCTIONAL_GOAL,
                          stop=dict(reason=result.stop_reason))
    assert completion["flags"]["agent_terminated"] is True
    assert completion["flags"]["agent_stop_reason"] == result.stop_reason


@pytest.mark.asyncio
@requires_deep_agents
async def test_a_cancelled_task_sends_nothing_and_says_so(tmp_path):
    model, _ = scripted(("", dict(tool_calls=[dict(
        name="run_test_group", args={"group_id": "group_1"}, id="call-1")])),
        ("已停止。", {}))
    session_, tasks, project, _, summary = session(tmp_path)
    session_.scope.svc._shutting_down = True
    result = await run_agent(session_.scope, session_.tasks, SimpleNamespace(max_total_tokens=0),
                             max_model_calls=3, model=model)
    tool_results = [row for row in result.transcript if row["kind"] == "TOOL_RESULT"]
    assert tool_results and "TASK_CANCELLED" in tool_results[0]["preview"]
    assert tasks.calls == []
    assert result.stop_reason == "CANCELLED"
    completion = evaluate(project, summary, FUNCTIONAL_GOAL,
                          stop=dict(reason=result.stop_reason))
    assert completion["complete"] is False
    assert completion["flags"]["evidence_produced"] is False


# ------------------------------------------------------------------ cost without losing facts


def test_a_repeated_read_can_be_recovered_after_context_editing(tmp_path):
    session_, *_ = session(tmp_path)
    first = session_.read_view("get_contract", lambda: dict(operations=["createParcel"]))
    again = session_.read_view("get_contract", lambda: dict(operations=["createParcel"]))
    assert first == dict(operations=["createParcel"])
    assert again["repeated"] is True and again["unchanged_since_previous_call"] is True
    assert again["sha256"] and "note" in again
    session_.generation += 1
    assert session_.read_view("get_contract", lambda: pytest.fail("static view recomputed"))[
        "repeated"]
    assert session_.read_view("get_contract", lambda: first, refresh=True) == first
    scope = session_.read_view("get_test_scope", lambda: dict(groups=[]))
    assert scope == dict(groups=[])
    assert session_.read_view("get_test_scope", lambda: pytest.fail("same revision"))["repeated"]
    session_.generation += 1
    assert session_.read_view("get_test_scope", lambda: dict(groups=["new"])) == {
        "groups": ["new"]}


def test_a_truncated_tool_result_keeps_the_error_and_the_repair_fields():
    from agenttest.product_agent import _json

    payload = dict(ok=False, status="NOT_EXECUTED",
                   error=dict(code="PLANNING_INSTANCE_COVERAGE",
                              fields=["instance.ai_3"], detail=dict(missing=["ai_3"])),
                   budget=dict(remaining_requests=7),
                   cases=[dict(title="很长的用例" * 20, steps=list(range(30)))
                          for _ in range(40)])
    wire = json.loads(_json(payload, limit=2000))
    assert wire["truncated"] is True
    assert wire["error"]["code"] == "PLANNING_INSTANCE_COVERAGE"
    assert wire["error"]["detail"] == dict(missing=["ai_3"])
    assert wire["budget"] == dict(remaining_requests=7)
    assert wire["status"] == "NOT_EXECUTED"
    assert "preview" in wire and "read_test_report" in wire["note"]


def test_only_a_transient_gateway_failure_is_retried():
    from agenttest.deep_harness.chat_model import ModelHarnessError
    from agenttest.product_agent import _transient_model_error

    assert _transient_model_error(ModelHarnessError("MODEL_SERVICE_ERROR", "concurrency")) is True
    assert _transient_model_error(ModelHarnessError("MODEL_TIMEOUT")) is True
    assert _transient_model_error(ModelHarnessError("MODEL_CONNECTION_ERROR")) is True
    assert _transient_model_error(ModelHarnessError("MODEL_TOKEN_BUDGET", "limit")) is False
    assert _transient_model_error(ModelHarnessError("MODEL_INPUT_REJECTED")) is False
    assert _transient_model_error(ModelHarnessError("MODEL_REFUSAL")) is False
