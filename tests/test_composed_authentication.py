"""One authentication per role in the bounded composition profile; no plan repair."""

import json

import httpx
import pytest
from pydantic import ValidationError
from test_composed_constraints import adapter, catalog, step

from agenttest.composed_plan import ComposedPlannerDraft
from agenttest.demo import PROJECT_ROOT
from agenttest.diagnostics import Layer, schema_diagnostic
from agenttest.llm_planner import PlannerFailure


def login(role="alice", **changes):
    return step(**{"id": "auth_" + role, "operation_id": "login", "role": role,
                   "policy_id": "composed_login_v1", "csv_fixture_id": None, **changes})


@pytest.mark.parametrize("role", ["alice", "bob"])
def test_repeated_login_is_rejected_even_with_distinct_step_ids(role):
    data = {"name": "private-generated-name", "steps": [login(role), login(role, id="again")]}
    with pytest.raises(ValidationError) as exc:
        ComposedPlannerDraft.model_validate(data)
    diagnostic = schema_diagnostic(exc.value, Layer.MODEL)
    assert diagnostic.code == "MODEL_SCHEMA_COMPOSED_AUTHENTICATION"
    assert role not in diagnostic.model_dump_json()
    assert "private-generated-name" not in diagnostic.model_dump_json()
    assert len(data["steps"]) == 2


def test_each_role_can_login_once_and_catalog_rules_do_not_reveal_task_answers():
    draft = ComposedPlannerDraft.model_validate(
        {"name": "two roles", "steps": [login("bob"), login("alice")]})
    assert [s.role for s in draft.steps] == ["bob", "alice"]
    contexts = [catalog(), catalog(
        "bob 创建课程“鉴权目录检查”；验证 alice 读取被拒绝；核对创建者读取一致。")]
    payloads = [json.loads(adapter().prepare_request(c)) for c in contexts]
    public = [json.loads(p["messages"][1]["content"]) for p in payloads]
    for value in public:
        assert value["authentication_rules"] == [
            {"role": role, "login_operation_id": "login", "max_occurrences": 1}
            for role in ("alice", "bob")]
    assert public[0].pop("user_task") != public[1].pop("user_task")
    assert public[0] == public[1]
    assert "exactly once" in payloads[0]["messages"][0]["content"]
    for payload in payloads:
        assert len(json.dumps(payload, ensure_ascii=False).encode()) + 512 <= 20_000


@pytest.mark.asyncio
async def test_duplicate_login_reply_is_not_repaired_or_retried():
    calls = []
    draft = {"name": "untrusted", "steps": [login("bob"), login("bob", id="again")]}

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(draft)}}], "usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    planner = adapter(httpx.MockTransport(respond))
    with pytest.raises(PlannerFailure) as exc:
        await planner.generate(catalog())
    assert exc.value.reason == "MODEL_INVALID_RESPONSE"
    assert exc.value.diagnostic.code == "MODEL_SCHEMA_COMPOSED_AUTHENTICATION"
    assert len(calls) == planner.stats.calls == 1
    assert planner.stats.total_tokens == 150


@pytest.mark.asyncio
@pytest.mark.parametrize("case_index", range(4))
async def test_new_development_tasks_pass_fixed_reply_workflow_with_one_login_per_role(
        tmp_path, case_index):
    pytest.importorskip("langgraph", reason="Full composition flow requires the agent extra")
    from agenttest.composed_demo import ComposedDraftPlanner, run_composed_demo
    from agenttest.composed_evaluation import CompositionSuite, reference_draft
    from agenttest.report import Outcome, ReportStore
    from agenttest.workflow import FakeLLM

    suite = CompositionSuite.model_validate_json((PROJECT_ROOT /
        "examples/composed-authentication-development.json").read_bytes())
    case = suite.cases[case_index]
    draft = reference_draft(case.expected)
    planner = ComposedDraftPlanner(FakeLLM(draft.model_dump_json()))
    report = await run_composed_demo(case.user_task, planner, ReportStore(tmp_path))
    assert report.outcome == Outcome.PASS and report.task_satisfied is True
    assert report.planner_calls == 1 and report.model_call is None
    assert all(sum(s.operation_id == "login" and s.role == role for s in report.plan_structure) == 1
               for role in {s.role for s in draft.steps})
