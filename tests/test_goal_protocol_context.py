"""Native protocol Mock tests and bounded multi-turn state, separately from real providers."""

import json
from copy import deepcopy

import httpx
import pytest
from test_goal_suite import SyntheticPlanner, _project

from agenttest.dsl import Scenario
from agenttest.goal_conversation import GoalConversation
from agenttest.llm_planner import ModelSettings, PlannerFailure
from agenttest.native_tools import NativeFunctionPlanner
from agenttest.planner_input import planning_context
from agenttest.portability_demo import project_inputs
from agenttest.report import Outcome, ReportStore
from agenttest.tool_registry import REQUESTS, TOOLS


def native_context():
    p = _project()
    return planning_context(p.contract, p.target, p.registry, "测试创建工单接口",
                            Scenario.model_json_schema())


def native_completion(*, name="submit_test_plan", duplicate=False):
    draft = dict(name="native proposal", steps=[dict(id="open", operation_id="openTicket",
        policy_id="ticket_open_v1", role="owner", arguments=[
            dict(location="body", name="subject", value="a", from_step=None, select=None),
            dict(location="body", name="priority", value="low", from_step=None, select=None)])])
    call = dict(id="call_synthetic", type="function",
                function=dict(name=name, arguments=json.dumps(draft)))
    return dict(choices=[dict(finish_reason="tool_calls", message=dict(content=None,
        tool_calls=[call, call] if duplicate else [call]))],
        usage=dict(prompt_tokens=100, completion_tokens=50, total_tokens=150))


def native(handler, *, supported=True, protocol="openai_compatible"):
    return NativeFunctionPlanner(ModelSettings(model="synthetic-model",
        base_url="https://model.invalid/v1", api_key="synthetic-credential", protocol=protocol),
        httpx.MockTransport(handler), provider_supported=supported)


@pytest.mark.asyncio
async def test_native_envelope_and_json_share_schema_and_dsl_conversion():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=native_completion())

    planner = native(handle)
    plan = Scenario.model_validate_json(await planner.generate(native_context()))
    assert plan.steps[0].body["priority"].value == "low"
    payload = requests[0]
    assert "response_format" not in payload and payload["parallel_tool_calls"] is False
    function = payload["tools"][0]["function"]
    assert function["name"] == "submit_test_plan" and function["strict"]
    assert function["parameters"]["additionalProperties"] is False
    assert payload["tool_choice"]["function"]["name"] == function["name"]
    assert planner.stats.calls == 1 and planner.stats.total_tokens == 150


@pytest.mark.asyncio
@pytest.mark.parametrize("supported,protocol", [(False, "openai_compatible"), (True, "chatecnu")])
async def test_unsupported_protocol_returns_status_before_any_model_request(supported, protocol):
    requests = []
    planner = native(lambda r: requests.append(r), supported=supported, protocol=protocol)
    with pytest.raises(PlannerFailure, match="NATIVE_FUNCTION_PROTOCOL_UNSUPPORTED"):
        await planner.generate(native_context())
    assert planner.stats.calls == 0 and requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [dict(name="execute_shell"), dict(duplicate=True)])
async def test_unlisted_and_parallel_tool_calls_never_execute(kwargs):
    planner = native(lambda r: httpx.Response(200, json=native_completion(**kwargs)))
    with pytest.raises(PlannerFailure, match="MODEL_INVALID_RESPONSE"):
        await planner.generate(native_context())
    assert planner.stats.calls == 1 and planner.stats.total_tokens == 150


def test_shared_registry_has_permissions_resources_and_result_types():
    assert set(REQUESTS) == {"list_tests", "get_contract", "validate_plan", "run_scenario",
                             "read_report"}
    for tool in TOOLS.values():
        public = tool.public()
        assert public["purpose"] and public["permission"] and public["resource_relation"]
        assert public["result_type"]
    assert TOOLS["submit_test_plan"].permission == "propose_only"


@pytest.mark.asyncio
async def test_supplement_produces_new_immutable_plan_with_shared_remaining_budget(tmp_path):
    pytest.importorskip("langgraph")
    app, _, _, _, env = project_inputs("tickets")
    p = deepcopy(_project())
    session = GoalConversation(p)
    first, preview = session.plan(p, "先测创建工单", environ=env)
    assert preview["status"] == "READY"
    planner = SyntheticPlanner()
    report = await session.execute(p, first.id, planner, ReportStore(tmp_path), environ=env,
                                   transport=httpx.ASGITransport(app=app))
    assert report.outcome == Outcome.PASS
    second, supplement = session.plan(p, "再补权限检查", environ=env, expected_version=1)
    assert supplement["requested_goals"] == ["create", "ownership"]
    assert first.request_sha256 != second.request_sha256
    snapshot = session.snapshot()
    assert snapshot["plans"][0]["status"] == "FINISHED"
    assert snapshot["remaining"]["requests"] == 9
    assert snapshot["evidence_refs"] == [report.run_id]
    assert all(secret not in json.dumps(snapshot) for secret in env.values())
    with pytest.raises(ValueError, match="PLAN_STALE_OR_ALREADY_STARTED"):
        await session.execute(p, first.id, planner, ReportStore(tmp_path), environ=env)


def test_configuration_change_requires_new_checked_revision_and_no_budget_topup():
    p = deepcopy(_project())
    session = GoalConversation(p)
    first, _ = session.plan(p, "创建工单", environ={})
    p.target.max_requests = 20
    p.registration.budget.suite_max_model_calls = 2
    p.registration.budget.suite_max_total_tokens = 10_000
    revised, checked = session.revalidate(p, first.id)
    assert checked["status"] == "READY" and revised.id != first.id
    assert session.ledger.max_requests == 10
    assert session.ledger.max_model_calls == 2
    assert session.ledger.max_total_tokens == 10_000
    assert session.snapshot()["plans"][0]["status"] == "STALE"


def test_clarification_preserves_original_history_without_turning_unknown_assertions_trusted():
    session = GoalConversation(_project())
    first, pending = session.plan(_project(), "创建工单并退款", environ={})
    assert pending["status"] == "NEEDS_REVIEW"
    second, ready = session.plan(_project(), "创建工单", environ={}, replace_pending=True)
    assert ready["status"] == "READY" and second.version == 2
    assert len(session.snapshot()["request_hashes"]) == 2
    assert session.snapshot()["clarification_hashes"]
    with pytest.raises(ValueError, match="ONLY_UNVERIFIED"):
        session.plan(_project(), "关闭工单", environ={}, replace_pending=True)
    assert first.request_sha256 != second.request_sha256


def test_context_rejects_credentials_cross_project_and_unbounded_turns():
    p = deepcopy(_project())
    session = GoalConversation(p)
    with pytest.raises(ValueError):
        session.plan(p, "创建工单“hidden-value”", environ={"TICKET_OWNER_BEARER": "hidden-value"})
    assert session.snapshot()["version"] == 0
    for _ in range(8):
        session.plan(p, "测试创建工单", environ={})
    with pytest.raises(ValueError, match="CONVERSATION_VERSION_OR_PROJECT_MISMATCH"):
        session.plan(p, "创建工单", environ={})


@pytest.mark.asyncio
async def test_registered_readonly_capabilities_reuse_existing_feedback_and_verdict(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.feedback import FakeFeedback
    from agenttest.feedback_check import CASES, inputs
    from agenttest.goal_runtime import run_goal

    app, _, _, _, env, _, _, capabilities = inputs(CASES[1])
    p = deepcopy(_project())
    p.capabilities = capabilities
    feedback = FakeFeedback()
    _, report = await run_goal(p, "创建工单、查询、权限检查", SyntheticPlanner(),
        ReportStore(tmp_path), environ=env, transport=httpx.ASGITransport(app=app),
        analyzer=feedback)
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.feedback is not None and feedback.calls <= 3
    assert len(report.investigations) <= 2
    assert report.trusted_symptom is not None
    assert report.task_satisfied is False
