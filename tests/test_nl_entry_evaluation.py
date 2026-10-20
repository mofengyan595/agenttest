"""Harness development uses older demands only; new 12 and first demand remain unexecuted."""

from copy import deepcopy

import httpx
import pytest

from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.review import task_identity
from agenttest.evaluation import save_evaluation
from agenttest.nl_entry_evaluation import (
    TraceModel,
    _position,
    grade_position,
    prepare_profiles,
    run_requests,
    summarize,
    verify_position,
)
from agenttest.report import ReportStore


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["none", "owner_leak"])
async def test_old_demands_exercise_real_json_adapter_and_isolated_scoring(tmp_path, fault):
    pytest.importorskip("langgraph")
    prepare_profiles(tmp_path / "projects")
    folder = tmp_path / "position-001"
    folder.mkdir()
    checks = []
    row = await _position(folder, dict(project="tickets", user_task="创建工单、查询、权限检查"),
                          fault, None, lambda: checks.append(True))
    row.update(position=1, round=1, case_id="OLD_DEVELOPMENT", attempted=True)
    verify_position(tmp_path, row)
    assert checks and row["requests"] >= 3 and not row["stop_paid"]
    label = dict(scope="flow", behavior="execute", expected_goals=["create", "read", "ownership"],
        required_policies=["ticket_open_v1", "ticket_read_v1", "ticket_denied_v1"],
        required_operations=["openTicket", "readTicket"], expected_normal_plans=1)
    if fault == "owner_leak":
        label["designated_failure"] = ["readTicket", "http_status", None]
    score = grade_position(tmp_path, row, label)
    assert score["plan_complete"] and score["actual_execution_complete"]
    assert score["supported_scope_outcome_correct"] and score["full_task_complete"]
    if fault == "owner_leak":
        # The intentionally simple Fake picks the first closed-state read on an open ticket.
        # Its failed corroboration must not be promoted into a rule-supported diagnosis.
        assert score["designated_fault_detected"] and score["rule_supported"] == 0
        assert score["model_explicit_abstentions"] == 1
        assert score["model_readonly_selections"] <= 2
    # A product PASS cannot hide a missing externally required policy/plan.
    wrong = deepcopy(label)
    wrong["required_policies"].append("ticket_list_v1")
    assert not grade_position(tmp_path, row, wrong)["plan_complete"]
    changed = deepcopy(row)
    changed["usage"]["model_calls"] += 1
    with pytest.raises(ValueError, match="actual report evidence"):
        verify_position(tmp_path, changed)


@pytest.mark.asyncio
async def test_unknown_usage_is_retained_and_stops_later_suite_groups(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from test_goal_suite import SyntheticPlanner

    from agenttest.feedback import FakeFeedback

    prepare_profiles(tmp_path / "projects")
    folder = tmp_path / "position-001"
    folder.mkdir()
    monkeypatch.setattr("agenttest.nl_entry_fixture.mock_adapters",
                        lambda: (SyntheticPlanner(unknown=True), FakeFeedback()))
    row = await _position(folder, dict(project="tickets", user_task="测试这个项目已授权的核心接口"),
                          "none", None, lambda: None)
    row.update(position=1, attempted=True)
    assert row["stop_paid"] and row["usage"]["total_tokens"] is None
    assert row["usage"]["model_calls"] == row["unknown_usage_calls"] == 1
    verify_position(tmp_path, row)
    summary = summarize([row, *[dict(attempted=False) for _ in range(35)]], "real")
    assert summary["scheduled"] == 36 and summary["attempted"] == 1
    assert summary["not_attempted"] == 35 and summary["total_tokens"] is None


@pytest.mark.asyncio
async def test_labels_or_source_drift_are_rejected_before_delegate(tmp_path):
    from test_goal_suite import SyntheticPlanner

    delegate = SyntheticPlanner()
    trace = TraceModel(delegate, tmp_path, lambda: None)
    with pytest.raises(ValueError, match="answers entered"):
        await trace.generate(dict(nested=[dict(reference_plan=[])]))
    assert delegate.calls == trace.stats.calls == 0

    def drift():
        raise ValueError("Frozen source/configuration changed")

    trace = TraceModel(delegate, tmp_path, drift)
    with pytest.raises(ValueError, match="Frozen"):
        await trace.generate({})
    assert delegate.calls == trace.stats.calls == 0


def test_dirty_source_rejected_before_reading_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("agenttest.nl_entry_evaluation.checkout_identity",
                        lambda root: dict(worktree_status="dirty"))
    with pytest.raises(ValueError, match="clean checkout"):
        run_requests(tmp_path / "absent", tmp_path / "missing", tmp_path / "missing",
                     model_env=tmp_path / "private.env")
    assert not (tmp_path / "absent").exists()


def test_new_public_requests_and_first_demand_are_excluded_from_independent_holdout():
    from agenttest.demo import PROJECT_ROOT

    root = PROJECT_ROOT / "examples"
    requests = read_json(root / "nl-entry/new-requests.json")
    labels = read_json(root / "nl-entry/grading-labels.json")["cases"]
    assert len(requests) == len({r["id"] for r in requests}) == 12
    assert {scope: sum(r["scope"] == scope for r in labels) for scope in
            ("single", "flow", "project")} == dict(single=6, flow=4, project=2)
    assert all(set(q) == {"id", "project", "user_task"} for q in requests)
    assert {q["project"] for q in requests} == {"courses", "tickets"}
    requests = [*requests, read_json(root / "nl-entry/new-demand.json")]
    exclusions = {r["case_id"]: r["task_identity"] for r in read_json(
        root / "eval-v2/goal-used-task-identities.json")["tasks"]}
    assert all(exclusions[q["id"]] == task_identity(q["user_task"]) for q in requests)


@pytest.mark.asyncio
async def test_configuration_drift_after_first_sent_request_blocks_next_dispatch(tmp_path):
    from test_goal_suite import SyntheticPlanner, _project

    from agenttest.goal_runtime import run_goal
    from agenttest.portability_demo import project_inputs

    pytest.importorskip("langgraph")
    project = deepcopy(_project())
    app, _, _, _, env = project_inputs("tickets")
    sent = []
    inner = httpx.ASGITransport(app=app)

    class ChangeAfterSend(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            sent.append(request.method)
            reply = await inner.handle_async_request(request)
            project.target.max_requests += 1
            return reply

    _, report = await run_goal(project, "创建工单、查询", SyntheticPlanner(), ReportStore(tmp_path),
                              environ=env, transport=ChangeAfterSend())
    assert sent == ["POST"] and not report.task_satisfied


def test_model_disabled_replay_derives_contract_from_old_original_text(tmp_path, capsys):
    pytest.importorskip("langgraph")
    from test_goal_entry import ticket_plan

    from agenttest.__main__ import main
    from agenttest.nl_entry_usage import replay_export

    prepare_profiles(tmp_path / "projects")
    request = dict(project="tickets",
                   user_task="验证创建、关闭、查询，以及其他用户不能读取的流程。")
    save_evaluation(request, tmp_path / "request.json")
    plan = tmp_path / "fake-plan.json"
    plan.write_text(ticket_plan().model_dump_json(), encoding="utf-8")
    code = main(["goal", "--project", str(tmp_path / "projects/tickets/registered.project.json"),
        "--user-task", request["user_task"], "--fake-plan", str(plan), "--synthetic-target",
        "--runs-dir", str(tmp_path / "source-reports"),
        "--export-regression", str(tmp_path / "bundle")])
    assert code == 0
    capsys.readouterr()
    replay = replay_export(tmp_path, tmp_path / "pytest-replay")
    assert replay["verified"] and replay["exit"] == 0
