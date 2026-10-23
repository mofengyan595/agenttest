"""Unknown semantics never gain execution authority, even with perfectly structured proposals."""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from test_goal_suite import SyntheticPlanner, _project

from agenttest.goal_derivation import derive_goal
from agenttest.goal_spec import GoalSpecDraft, GoalSpecModel, verify_draft
from agenttest.goal_suite import SuiteLedger, run_suite
from agenttest.llm_planner import ModelSettings
from agenttest.portability_demo import EXAMPLES
from agenttest.report import ModelCallStats, ReportStore, fingerprint


class DraftFixture:
    def __init__(self, data, *, unknown=False):
        self.data, self.unknown = data, unknown
        self.stats = ModelCallStats()
        self.contexts = []

    async def generate(self, context):
        self.contexts.append(deepcopy(context))
        self.stats = ModelCallStats(calls=1, total_tokens=None if self.unknown else 50,
                                   model_name="SIMULATED")
        return json.dumps(self.data)


@pytest.mark.parametrize("text", ["测试 ＬＩＳＴＴＩＣＫＥＴＳ 接口。",
                                  "测试 `listTickets` 接口。", "测试 LISTtickets 接口。"])
def test_reference_typography_varies_without_new_action_aliases(text):
    before = _project().catalog.model_dump()
    goal = derive_goal(_project(), text)
    assert goal.task and goal.requested_goals == ["list"]
    assert goal.user_task_sha256 == fingerprint(text)
    assert before == _project().catalog.model_dump()


def test_normalization_keeps_quoted_payload_and_ambiguous_identifiers_fail_closed():
    goal = derive_goal(_project(), '创建工单“ＡＢＣ”')
    assert goal.task
    assert any(d["value"] == "ＡＢＣ" for d in goal.input_defaults)
    p = deepcopy(_project())
    p.catalog.goals[1].aliases.append("LISTTICKETS")
    assert derive_goal(p, "测试 listtickets 接口").task is None


def test_method_path_whitespace_is_typography_not_new_business_semantics():
    from agenttest.project_goals import load_goal_project

    p = load_goal_project(EXAMPLES / "courses/registered.project.json")
    goal = derive_goal(p, "测试 ｐｏｓｔ\t/api/courses 接口")
    assert goal.task and goal.requested_goals == ["create"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "drop_requirement", "unknown_goal", "overlap",
                                    "extra_authority", "unknown_usage"])
async def test_semantic_draft_is_traced_accounted_and_never_a_business_run(tmp_path, change):
    p = deepcopy(_project())
    p.registration.semantic_goal_drafts = True
    text = "麻烦把工单提交一下，退款条件也检查。"
    boundary = text.index("，")
    data = dict(scope="flow", goals=[dict(goal_id="create", start=0, end=boundary)],
                unresolved=[dict(start=boundary, end=len(text))])
    if change == "drop_requirement":
        data["unresolved"] = []
    elif change == "unknown_goal":
        data["goals"][0]["goal_id"] = "refund"
    elif change == "overlap":
        data["unresolved"][0]["start"] = 0
    elif change == "extra_authority":
        data["url"] = "http://169.254.169.254/"
    draft = DraftFixture(data, unknown=change == "unknown_usage")
    planner = SyntheticPlanner()
    ledger = SuiteLedger(10, 8, 100000)
    calls = []
    result = await run_suite(p, text, planner, ReportStore(tmp_path), environ={},
        transport=httpx.MockTransport(lambda r: calls.append(r)), ledger=ledger,
        semantic_model=draft)
    assert not result["complete"] and planner.calls == 0 and not calls
    record = result["goal_spec"]
    assert not record["semantic_verified"] and record["status"] == "NEEDS_REVIEW"
    assert record["original_request_sha256"] == fingerprint(text)
    assert result["usage"]["model_calls"] == 1 and result["http_requests"] == 0
    assert draft.contexts[0]["original_request"] == text
    assert text not in json.dumps(record, ensure_ascii=False)
    if change == "drop_requirement":
        assert record["unaccounted_characters"] > 0
        assert record["reason"] == "GOAL_SPEC_REQUIREMENTS_UNACCOUNTED"
    elif change in {"unknown_goal", "overlap", "extra_authority"}:
        assert not record["structure_valid"]
    elif change == "unknown_usage":
        assert result["usage"]["total_tokens"] is None and ledger.usage_unknown
    else:
        assert record["structure_valid"] and record["source_accounted"]


@pytest.mark.asyncio
async def test_no_draft_call_after_budget_exhaustion_or_on_secret_input(tmp_path):
    p = deepcopy(_project())
    p.registration.semantic_goal_drafts = True
    draft = DraftFixture(dict(scope="unknown", goals=[], unresolved=[]))
    text = "检验报销条件"
    result = await run_suite(p, text, SyntheticPlanner(), ReportStore(tmp_path), environ={},
        ledger=SuiteLedger(10, 1, 1000, model_calls=1), semantic_model=draft)
    assert draft.contexts == []
    assert result["goal_spec"]["reason"] == "SUITE_MODEL_BUDGET_EXHAUSTED"
    with pytest.raises(ValueError, match="GOAL_SPEC_SECRET_REJECTED"):
        await run_suite(p, "检验 sensitive-local-bearer", SyntheticPlanner(), ReportStore(tmp_path),
            environ={"TICKET_OWNER_BEARER": "sensitive-local-bearer"}, semantic_model=draft)
    assert draft.contexts == []


def test_provider_schema_cannot_add_permissions_and_has_no_reference_plan():
    settings = ModelSettings(model="mock", base_url="https://mock-provider.invalid/v1",
                             api_key="fixture-key")
    adapter = GoalSpecModel(settings, profile="generic")
    context = dict(original_request="检验一下", project_id="tickets",
                   catalog=_project().catalog.model_dump())
    request = json.loads(adapter.prepare_request(context))
    raw = json.dumps(request)
    assert "additionalProperties" in raw and "goal_id" in raw
    assert "reference_plan" not in raw and "requirements" not in context
    draft = GoalSpecDraft(scope="unknown", goals=[], unresolved=[])
    assert verify_draft(_project(), "检验一下", draft)["unaccounted_characters"] == 4


@pytest.mark.asyncio
async def test_task_service_draft_persists_usage_and_explicit_clarification_shares_it(
    tmp_path, monkeypatch,
):
    pytest.importorskip("langgraph")
    from test_task_service import settle

    from agenttest.task_service import TaskService
    from agenttest.task_store import TaskStore

    p = deepcopy(_project())
    p.registration.semantic_goal_drafts = True
    text = "麻烦受理这个请求"
    draft = DraftFixture(dict(scope="single", goals=[dict(goal_id="create", start=0,
                         end=len(text))], unresolved=[]))
    svc = TaskService({"tickets": EXAMPLES / "tickets/registered.project.json"},
        TaskStore(tmp_path / "tasks.sqlite3"), ReportStore(tmp_path / "reports"),
        lambda p: SyntheticPlanner(), synthetic=True, environ={}, semantic_factory=lambda p: draft)
    monkeypatch.setattr(svc, "project", lambda _: p)
    try:
        row = await svc.submit("owner", "tickets", text, uuid4().hex)
        pending = await settle(svc, row["id"], "owner")
        assert pending["status"] == "NEEDS_REVIEW"
        assert pending["summary"]["context_usage"]["model_calls"] == 1
        assert pending["summary"]["context_usage"]["requests"] == 0
        clarified = await svc.submit("owner", "tickets", "创建工单", uuid4().hex,
                                     parent=row["id"], replace_pending=True)
        finished = await settle(svc, clarified["id"], "owner")
        assert finished["summary"]["task_satisfied"]
        assert finished["summary"]["context_usage"]["model_calls"] == 2
        assert len(draft.contexts) == 1
        assert text.encode() not in svc.db.path.read_bytes()
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_draft_timeout_consumes_one_slot_without_retry(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.workflow import WorkflowBudget

    class Slow(DraftFixture):
        async def generate(self, context):
            self.contexts.append(context)
            self.stats = ModelCallStats(calls=1, total_tokens=None)
            await asyncio.sleep(10)

    p = deepcopy(_project())
    p.registration.semantic_goal_drafts = True
    slow = Slow({})
    result = await run_suite(p, "核查退款条件", SyntheticPlanner(), ReportStore(tmp_path),
        environ={}, semantic_model=slow, budget=WorkflowBudget(model_timeout=0.01))
    assert len(slow.contexts) == 1 and result["model_calls"] == 1
    assert result["goal_spec"]["reason"] == "GOAL_SPEC_TIMEOUT"
    assert result["usage"]["total_tokens"] is None


def test_cli_proposal_uses_the_run_ledger_and_does_not_execute(tmp_path, capsys):
    pytest.importorskip("langgraph")
    from agenttest.__main__ import main

    for name in ("registered.project.json", "goals.json", "openapi.json",
                 "target.json", "policies.json"):
        (tmp_path / name).write_bytes((EXAMPLES / "tickets" / name).read_bytes())
    registration = json.loads((tmp_path / "registered.project.json").read_bytes())
    registration["semantic_goal_drafts"] = True
    (tmp_path / "registered.project.json").write_text(json.dumps(registration), encoding="utf-8")
    text = "请受理这个请求"
    draft = dict(scope="single", goals=[dict(goal_id="create", start=0, end=len(text))],
                 unresolved=[])
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps(draft), encoding="utf-8")
    code = main(["goal", "--project", str(tmp_path / "registered.project.json"),
        "--user-task", text, "--fake-plan", str(reply), "--synthetic-target",
        "--runs-dir", str(tmp_path / "reports")])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and not result["goal_spec"]["semantic_verified"]
    assert result["model_calls"] == 1 and result["http_requests"] == 0
