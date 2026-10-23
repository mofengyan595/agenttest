"""Real SQLite and ASGI task API: no model key, network, arbitrary destinations or replay."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_goal_suite import SyntheticPlanner

from agenttest.main import create_app
from agenttest.portability_demo import EXAMPLES
from agenttest.report import ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TERMINAL, TaskConflict, TaskStore


def service(tmp_path, factory=None):
    return TaskService({"tickets": EXAMPLES / "tickets/registered.project.json"},
        TaskStore(tmp_path / "tasks.sqlite3"), ReportStore(tmp_path / "reports"),
        factory or (lambda project: SyntheticPlanner()), synthetic=True, environ={})


async def settle(svc, task_id, owner):
    for _ in range(400):
        row = svc.db.get(task_id, owner)
        if row["status"] in TERMINAL:
            return row
        await asyncio.sleep(0.01)
    raise AssertionError("Task failed to terminate")


@pytest.mark.asyncio
async def test_persisted_tasks_and_duplicate_submission_do_not_execute_twice(tmp_path):
    pytest.importorskip("langgraph")
    planners = []

    def factory(project):
        planners.append(SyntheticPlanner())
        return planners[-1]

    svc = service(tmp_path, factory)
    submission = uuid4().hex
    first = await svc.submit("owner-hash", "tickets", "测试创建工单接口", submission)
    again = await svc.submit("owner-hash", "tickets", "测试创建工单接口", submission)
    assert first["id"] == again["id"]
    finished = await settle(svc, first["id"], "owner-hash")
    assert finished["status"] == "FINISHED" and finished["summary"]["task_satisfied"]
    assert len(planners) == planners[0].calls == 1
    assert {e["phase"] for e in svc.db.events(first["id"], "owner-hash")} >= {
        "QUEUED", "PLANNING", "VALIDATING", "EXECUTING", "FINISHED"}
    with pytest.raises(TaskConflict):
        await svc.submit("owner-hash", "tickets", "关闭工单", submission)
    with pytest.raises(KeyError):
        svc.db.get(first["id"], "different-owner")
    assert "测试创建工单接口".encode() not in svc.db.path.read_bytes()
    exported = svc.export_regression(first["id"], "owner-hash", finished["summary"]["run_id"])
    assert (exported / "manifest.json").is_file()
    assert len(list(exported.glob("test_agenttest_*.py"))) == 1
    await svc.close()


@pytest.mark.asyncio
async def test_suite_exports_derived_normal_and_negative_without_model_calls(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from agenttest.nl_entry_fixture import CatalogFixturePlanner
    from agenttest.regression import RegressionRuntime, assert_regression_passed, run_regression

    path = Path(__file__).resolve().parents[1] / (
        "examples/trusted-testing/projects/tickets/registered.project.json")
    svc = TaskService({"tickets": path}, TaskStore(tmp_path / "tasks.sqlite3"),
        ReportStore(tmp_path / "reports"), lambda project: CatalogFixturePlanner(),
        synthetic=True, environ={})
    try:
        first = await svc.submit("owner", "tickets", "请验证创建工单接口", uuid4().hex, suite=True)
        finished = await settle(svc, first["id"], "owner")
        assert finished["status"] == "FINISHED"
        summary = finished["summary"]
        assert summary["complete"] and summary["context_usage"]["model_calls"] == 1
        derived = [c for c in summary["cases"] if c.get("plan_derivation")]
        assert len(derived) == 9
        chosen = [next(c for c in derived if c["approved_negative_steps"]),
                  next(c for c in derived if not c["approved_negative_steps"])]
        exports = [svc.export_regression(first["id"], "owner", c["run_id"]) for c in chosen]

        def forbidden(*args, **kwargs):
            raise AssertionError("Fixed regressions must not call any model")

        monkeypatch.setattr(CatalogFixturePlanner, "generate", forbidden)
        from agenttest.llm_planner import OpenAICompatiblePlanner

        monkeypatch.setattr(OpenAICompatiblePlanner, "generate", forbidden)
        from agenttest.portability_demo import project_inputs

        project = svc.project("tickets")
        for case, exported in zip(chosen, exports):
            _, task, _ = svc._assets[(first["id"], case["run_id"])]
            app, _, _, _, env = project_inputs("tickets")
            report = await run_regression(exported, RegressionRuntime(project.document,
                project.target, project.registry, task, env, httpx.ASGITransport(app=app),
                ReportStore(tmp_path / "replayed")))
            assert_regression_passed(report)
            assert report.task_mode == "operator_plan" and report.task_satisfied is None
    finally:
        await svc.close()


def test_restart_marks_unfinished_interrupted_and_never_reconstructs_requests(tmp_path):
    db = TaskStore(tmp_path / "tasks.sqlite3")
    for state in ("QUEUED", "RUNNING", "WAITING_REVIEW"):
        db.create(state, "owner", state, "tickets", "a" * 64, "b" * 64, state, {})
    restarted = TaskStore(db.path)
    restarted.interrupt_unfinished()
    assert all(row["status"] == "INTERRUPTED" for row in restarted.list("owner"))


@pytest.mark.asyncio
@pytest.mark.parametrize("suite", [False, True])
async def test_configuration_change_during_planning_prevents_http(tmp_path, monkeypatch, suite):
    from copy import deepcopy

    from test_goal_suite import _project

    pytest.importorskip("langgraph")
    project = deepcopy(_project())

    class Changed(SyntheticPlanner):
        async def generate(self, context):
            reply = await super().generate(context)
            project.target.allow_mutations = False
            return reply

    planner = Changed()
    svc = service(tmp_path, lambda project: planner)
    monkeypatch.setattr(svc, "project", lambda project_id: project)
    try:
        task = await svc.submit("owner", "tickets", "创建工单", uuid4().hex, suite=suite)
        done = await settle(svc, task["id"], "owner")
        assert done["summary"]["context_usage"]["requests"] == 0
        assert planner.calls == 1
        if suite:
            assert not done["summary"]["complete"]
        else:
            assert done["summary"]["task_satisfied"] is False
            report = svc.reports.read(done["summary"]["run_id"])
            assert report.reason == "EXECUTION_PREFLIGHT_FAILED"
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_cancel_during_planning_stops_actions_and_preserves_unknown_usage(tmp_path):
    pytest.importorskip("langgraph")
    started = asyncio.Event()

    class Slow(SyntheticPlanner):
        async def generate(self, context):
            started.set()
            await asyncio.sleep(30)

    svc = service(tmp_path, lambda project: Slow())
    task = await svc.submit("owner", "tickets", "创建工单", uuid4().hex)
    await asyncio.wait_for(started.wait(), 10)
    cancelled = await svc.cancel(task["id"], "owner")
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["summary"]["context_usage"]["requests"] == 0
    assert cancelled["summary"]["reason"] == "CANCELLED_NO_ROLLBACK_OR_REPLAY"
    assert any(e["phase"] == "CANCELLED" for e in svc.db.events(task["id"], "owner"))
    await svc.close()


@pytest.mark.asyncio
async def test_concurrent_tasks_have_separate_resources_reports_and_serial_model_calls(tmp_path):
    pytest.importorskip("langgraph")
    svc = service(tmp_path)
    first, second = await asyncio.gather(
        svc.submit("owner", "tickets", "创建工单、查询", uuid4().hex),
        svc.submit("owner", "tickets", "关闭工单、查询", uuid4().hex))
    a, b = await asyncio.gather(settle(svc, first["id"], "owner"),
                              settle(svc, second["id"], "owner"))
    assert a["summary"]["task_satisfied"] and b["summary"]["task_satisfied"]
    assert a["summary"]["run_id"] != b["summary"]["run_id"]
    assert a["summary"]["context_usage"]["requests"] == 2
    assert b["summary"]["context_usage"]["requests"] == 3
    await svc.close()


@pytest.mark.asyncio
async def test_followup_is_new_task_and_shares_previous_remaining_budget(tmp_path):
    pytest.importorskip("langgraph")
    svc = service(tmp_path)
    first = await svc.submit("owner", "tickets", "先测创建工单", uuid4().hex)
    a = await settle(svc, first["id"], "owner")
    next_task = await svc.submit("owner", "tickets", "再补权限检查", uuid4().hex,
                                 parent=first["id"])
    b = await settle(svc, next_task["id"], "owner")
    assert a["summary"]["context_usage"]["requests"] == 1
    assert b["summary"]["context_usage"]["requests"] == 3
    assert a["summary"]["run_id"] != b["summary"]["run_id"]
    await svc.close()


@pytest.mark.asyncio
async def test_api_session_csrf_and_project_authorization_are_server_owned(tmp_path):
    pytest.importorskip("langgraph")
    svc = service(tmp_path)
    app = create_app(svc.reports.root, task_service=svc)
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        assert (await client.post("/api/tasks", json={})).status_code == 401
        session = await client.get("/api/task-session")
        csrf = session.json()["csrf_token"]
        assert "HttpOnly" in session.headers["set-cookie"]
        assert (await client.get("/api/task-session")).json()["csrf_token"] == csrf
        payload = dict(project_id="tickets", user_task="创建工单", submission_id=uuid4().hex)
        assert (await client.post("/api/tasks", json=payload)).status_code == 403
        headers = {"x-agenttest-csrf": csrf, "origin": "http://127.0.0.1:5173"}
        for field in ("target_url", "credentials", "policies"):
            rejected = await client.post("/api/tasks", json={**payload, field: "hidden-secret"},
                                         headers=headers)
            assert rejected.status_code == 422 and "hidden-secret" not in rejected.text
        assert (await client.post("/api/tasks", json=payload,
            headers={**headers, "origin": "https://unapproved.invalid"})).status_code == 403
        submitted = await client.post("/api/tasks", json=payload, headers=headers)
        assert submitted.status_code == 202
        task = submitted.json()
        for _ in range(400):
            result = (await client.get("/api/tasks/" + task["id"])).json()
            if result["status"] in TERMINAL:
                break
            await asyncio.sleep(0.01)
        assert result["status"] == "FINISHED"
        event = await client.get("/api/tasks/" + task["id"] + "/events")
        assert "text/event-stream" in event.headers["content-type"]
        assert '"phase": "FINISHED"' in event.text
        assert len((await client.get("/api/tasks")).json()["tasks"]) == 1
    await svc.close()


def test_sqlite_rejects_linked_metadata_file(tmp_path):
    import os

    db = TaskStore(tmp_path / "tasks.sqlite3")
    os.link(db.path, tmp_path / "linked.sqlite3")
    with pytest.raises(ValueError):
        TaskStore(db.path)


def test_project_directory_browser_cannot_choose_an_arbitrary_file(tmp_path):
    from agenttest.task_service import load_projects

    path = tmp_path / "projects.json"
    path.write_text(json.dumps({"escape": "../unapproved.json"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_projects(path)
    assert isinstance(EXAMPLES, Path)


@pytest.mark.asyncio
async def test_service_lease_prevents_second_runner_from_interrupting_active_metadata(tmp_path):
    svc = service(tmp_path)
    svc.db.create("queued", "owner", "submission", "tickets", "a" * 64, "b" * 64, "QUEUED", {})
    with pytest.raises(ValueError, match="TASK_SERVICE_ALREADY_RUNNING"):
        service(tmp_path)
    assert svc.db.get("queued", "owner")["status"] == "QUEUED"
    await svc.close()
    restarted = service(tmp_path)
    assert restarted.db.get("queued", "owner")["status"] == "INTERRUPTED"
    await restarted.close()


@pytest.mark.asyncio
async def test_policy_review_and_stale_versions_do_not_call_the_model(tmp_path, monkeypatch):
    from copy import deepcopy

    from test_goal_suite import _project

    svc = service(tmp_path)
    p = deepcopy(_project())
    p.registration.require_plan_review = True
    monkeypatch.setattr(svc, "project", lambda project_id: p)
    row = await svc.submit("owner", "tickets", "创建工单", uuid4().hex)
    assert row["status"] == "WAITING_REVIEW"
    assert row["summary"]["required_plan"][0]["operation_id"] == "openTicket"
    with pytest.raises(TaskConflict):
        await svc.start(row["id"], "owner", 2)
    assert svc.db.get(row["id"], "owner")["status"] == "WAITING_REVIEW"
    p.target.max_requests += 1
    with pytest.raises(TaskConflict, match="PLAN_PROJECT_VERSION_CHANGED"):
        await svc.start(row["id"], "owner", 1)
    assert not svc._workers
    await svc.close()


@pytest.mark.asyncio
async def test_missing_server_credentials_need_review_before_planner_admission(tmp_path):
    svc = service(tmp_path)
    svc.synthetic = False
    row = await svc.submit("owner", "tickets", "创建工单", uuid4().hex)
    assert row["status"] == "NEEDS_REVIEW"
    assert row["summary"]["reason"] == "PROJECT_CREDENTIAL_PREFLIGHT_FAILED"
    assert not svc._workers
    await svc.close()


@pytest.mark.asyncio
async def test_cancel_after_sent_write_does_not_claim_rollback(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from agenttest.portability_demo import project_inputs

    app, _, _, _, env = project_inputs("tickets")
    sent = asyncio.Event()
    calls = []

    class AfterDispatch(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request.method)
            response = await httpx.ASGITransport(app).handle_async_request(request)
            assert response.status_code == 201
            sent.set()
            await asyncio.sleep(30)
            return response

    monkeypatch.setattr("agenttest.task_service.synthetic_runtime",
                        lambda project: (env, AfterDispatch()))
    svc = service(tmp_path)
    row = await svc.submit("owner", "tickets", "创建工单、查询", uuid4().hex)
    await asyncio.wait_for(sent.wait(), 10)
    cancelled = await svc.cancel(row["id"], "owner")
    assert calls == ["POST"] and cancelled["status"] == "CANCELLED"
    assert cancelled["summary"]["context_usage"]["requests"] == 1
    run_id = next(e["run_id"] for e in svc.db.events(row["id"], "owner") if e["run_id"])
    report = svc.reports.read(run_id)
    assert len(report.steps) == 1 and len(report.steps[0].attempts) == 1
    assert report.task_satisfied is False
    await svc.close()
