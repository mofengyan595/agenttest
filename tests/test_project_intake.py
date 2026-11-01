"""New persisted web identities remain bound to server authorization and original gates."""

import asyncio
import json
import shutil
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from agenttest.main import create_app
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.project_intake import IntakeInput
from agenttest.report import ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TERMINAL, TaskConflict, TaskStore

PROFILES = Path(__file__).resolve().parents[1] / "examples/trusted-testing/projects"


def service(root, *, synthetic=True, factory=None):
    profiles = {k: PROFILES / k / "registered.project.json" for k in ("tickets", "courses")}
    return TaskService(profiles,
        TaskStore(root / "tasks.sqlite3"), ReportStore(root / "reports"),
        factory or (lambda project: CatalogFixturePlanner()), synthetic=synthetic, environ={},
        expose_registered=False, model_status=lambda: "SIMULATED")


@pytest.mark.asyncio
@pytest.mark.parametrize("route,component,method", [
    ("/api/projects", None, "project_list"), ("/api/project-intake", "intake", "list"),
    ("/api/tasks", "db", "list"), ("/api/saved-tests", "saved", "list")])
async def test_slow_readonly_catalog_never_blocks_health_or_session_requests(
        tmp_path, monkeypatch, route, component, method):
    from threading import Event

    entered, release = Event(), Event()
    svc = service(tmp_path)

    def slow(*_):
        entered.set()
        release.wait(5)
        return []

    monkeypatch.setattr(getattr(svc, component) if component else svc, method, slow)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(create_app(
        svc.reports.root, task_service=svc)), base_url="http://127.0.0.1:8000")
    try:
        assert (await client.get(route)).status_code == 401
        assert not entered.is_set()
        await client.get("/api/task-session")
        pending = asyncio.create_task(client.get(route))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await asyncio.wait_for(client.get("/health"), 1)).status_code == 200
            assert (await asyncio.wait_for(client.get("/api/task-session"), 1)).status_code == 200
            assert not pending.done()
        finally:
            release.set()
            assert (await pending).status_code == 200
    finally:
        await client.aclose()
        await svc.close()


def test_concurrent_document_cache_eviction_preserves_detached_validated_contracts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    svc = service(tmp_path)

    def load(index):
        name = f"read{index % 20}"
        raw = json.dumps(dict(openapi="3.0.3", info=dict(title=name, version="1"), paths={
            "/state": {"get": dict(operationId=name, responses={"200": dict(description="ok")})}}))
        first = svc.intake.document(raw)
        first.operations.clear()
        assert svc.intake.document(raw).operations[0].operation_id == name

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(load, range(60)))
    assert len(svc.intake._documents) <= 16


def payload(svc, domain="tickets", confirmed=True):
    profile = svc.intake.describe(domain)
    return IntakeInput(name="网页新建" + domain,
        document=(PROFILES / domain / "openapi.json").read_text(encoding="utf-8"),
        profile_id=domain, profile_identity=profile["identity"], confirmed=confirmed)


async def settle(svc, task_id):
    for _ in range(600):
        result = svc.db.get(task_id, "owner")
        if result["status"] in TERMINAL:
            return result
        await asyncio.sleep(.01)
    raise AssertionError("Task did not finish")


@pytest.mark.asyncio
async def test_web_save_restart_two_domains_and_model_disabled_regressions(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    svc = service(tmp_path)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(
                svc.reports.root, task_service=svc)), base_url="http://127.0.0.1:8000") as client:
            csrf = (await client.get("/api/task-session")).json()["csrf_token"]
            headers = {"x-agenttest-csrf": csrf}
            assert (await client.get("/api/projects")).json()["projects"] == []
            ids = []
            for domain in ("tickets", "courses"):
                created = await client.post("/api/project-intake", headers=headers,
                    json=payload(svc, domain).model_dump())
                assert created.status_code == 201, created.text
                value = created.json()
                assert value["ready"] and value["id"] != domain
                assert value["confirmation"]["source"] == "WEB_OPERATOR_SESSION"
                ids.append(value["id"])
            catalog = (await client.get("/api/projects")).json()["projects"]
            assert {p["id"] for p in catalog} == set(ids)
        await svc.close()
        svc = service(tmp_path)
        assert {p["id"] for p in svc.project_list()} == set(ids)
        from agenttest.goal_cli import synthetic_runtime
        from agenttest.regression import RegressionRuntime, assert_regression_passed, run_regression

        exports = []
        for key, demand in zip(ids,
                ("创建工单，关闭工单，查询工单，其他用户不能读取工单", "创建课程，查询课程")):
            task = await svc.submit("owner", key, demand, uuid4().hex)
            done = await settle(svc, task["id"])
            assert done["status"] == "FINISHED", done
            assert done["summary"]["task_satisfied"], done
            run_id = done["summary"]["run_id"]
            assert svc.task_view(done)["summary"]["exports"][run_id]["available"]
            directory = svc.export_regression(task["id"], "owner", run_id)
            _, contract, project = svc._assets[(task["id"], run_id)]
            exports.append((directory, contract, project))

        def forbidden(*args, **kwargs):
            raise AssertionError("Fixed regression must not invoke a model")

        monkeypatch.setattr(CatalogFixturePlanner, "generate", forbidden)
        for directory, contract, project in exports:
            env, transport = synthetic_runtime(project)
            report = await run_regression(directory, RegressionRuntime(project.document,
                project.target, project.registry, contract, env, transport, svc.reports))
            assert_regression_passed(report)
        saved = svc.saved.list("owner")
        assert len(saved) == 2 and all(s["available"] for s in saved)
        await svc.close()
        svc = service(tmp_path)
        assert len(svc.saved.list("owner")) == 2
        for asset in saved:
            submission = uuid4().hex
            first = await svc.saved.submit("owner", asset["task_id"], asset["run_id"], submission)
            duplicate = await svc.saved.submit("owner", asset["task_id"], asset["run_id"],
                                                submission)
            assert duplicate["id"] == first["id"]
            done = await settle(svc, first["id"])
            assert done["summary"]["fixed_regression_passed"], done
            assert done["summary"]["context_usage"]["model_calls"] == 0
            assert done["summary"]["task_satisfied"] is None
            with pytest.raises(KeyError):
                await svc.saved.submit("other", asset["task_id"], asset["run_id"], uuid4().hex)
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_drafts_confirmation_mismatch_credentials_and_revision_boundaries(tmp_path):
    svc = service(tmp_path)
    try:
        source = payload(svc, confirmed=False)
        draft = svc.intake.save(source, "owner")
        assert not draft["ready"]
        with pytest.raises(ValueError, match="NOT_READY"):
            svc.project(draft["id"])
        approved = svc.intake.save(source.model_copy(update={"confirmed": True, "version": 1}),
                                  "owner", draft["id"])
        assert approved["ready"] and approved["version"] == 2
        identity = svc.project(draft["id"]).identity()
        with pytest.raises(TaskConflict):
            svc.intake.save(source, "owner", draft["id"])
        updated = svc.intake.save(source.model_copy(update={"confirmed": True, "version": 2,
            "name": "改名"}), "owner", draft["id"])
        assert updated["ready"] and svc.project(draft["id"]).identity() != identity
        with pytest.raises(TaskConflict):
            svc.intake.save(source.model_copy(update={"confirmed": True,
                "profile_identity": "0" * 64}), "owner")
        mismatch = svc.intake.save(source.model_copy(update={"confirmed": True,
            "document": (PROFILES / "courses/openapi.json").read_text(encoding="utf-8")}), "owner")
        assert not mismatch["ready"] and any("不匹配" in m for m in mismatch["missing"])
        unbound = svc.intake.save(source.model_copy(update={"profile_id": None}), "owner")
        assert not unbound["ready"] and unbound["confirmation"] is None
        with pytest.raises(KeyError):
            svc.intake.save(source.model_copy(update={"profile_id": "unknown"}), "owner")
    finally:
        await svc.close()
    svc = service(tmp_path, synthetic=False)
    try:
        profile = svc.intake.describe("tickets")
        assert not any(i["ready"] for i in profile["identities"])
        assert "TICKET_OWNER_BEARER" in str(profile)
        approved = svc.intake.save(payload(svc), "owner")
        assert not approved["ready"] and any("缺少账号" in m for m in approved["missing"])
        with pytest.raises(TaskConflict, match="DEDICATED_SYNTHETIC"):
            await svc.saved.submit("owner", "a" * 32, "b" * 32, uuid4().hex)
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_full_workspace_reports_capacity_and_preserves_all_existing_projects(tmp_path):
    svc = service(tmp_path)
    try:
        with svc.intake.connect() as db:
            db.executemany("INSERT INTO projects VALUES (?,?)", [
                (f"existing_{i}", json.dumps(dict(id=f"existing_{i}", name="原记录")))
                for i in range(100)])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(
                svc.reports.root, task_service=svc)), base_url="http://127.0.0.1:8000") as client:
            csrf = (await client.get("/api/task-session")).json()["csrf_token"]
            response = await client.post("/api/project-intake",
                headers={"x-agenttest-csrf": csrf}, json=payload(svc).model_dump())
            assert response.status_code == 409
            assert "100个项目" in response.json()["detail"]
            assert "版本已变化" not in response.text
        with svc.intake.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 100
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_api_limits_session_csrf_arbitrary_paths_and_secrets(tmp_path):
    svc = service(tmp_path, synthetic=False)
    try:
        svc.environ["TICKET_OWNER_BEARER"] = "private-credential-must-stay-server-side"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(
                svc.reports.root, task_service=svc)), base_url="http://127.0.0.1:8000") as client:
            assert (await client.get("/api/project-intake")).status_code == 401
            csrf = (await client.get("/api/task-session")).json()["csrf_token"]
            value = payload(svc).model_dump()
            assert (await client.post("/api/project-intake", json=value)).status_code == 403
            headers = {"x-agenttest-csrf": csrf}
            for extra in ({"target": "https://example.com"}, {"path": "../../outside"},
                          {"credentials": {"password": "x"}}):
                response = await client.post("/api/project-intake", headers=headers,
                                             json={**value, **extra})
                assert response.status_code == 422
            response = await client.post("/api/project-intake", headers=headers,
                json={**value, "name": svc.environ["TICKET_OWNER_BEARER"]})
            assert response.status_code == 422 and "private-credential" not in response.text
            assert (await client.post("/api/project-intake", headers=headers,
                json={**value, "document": "x" * 1_100_001})).status_code == 413
            assert svc.intake.list() == []
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_unknown_demand_and_intake_change_during_planning_send_no_requests(tmp_path):
    pytest.importorskip("langgraph")
    svc = service(tmp_path)
    try:
        data = payload(svc)
        project = svc.intake.save(data, "owner")
        task = await svc.submit("owner", project["id"], "工单退款金额应正确", uuid4().hex)
        assert task["status"] == "NEEDS_REVIEW"
        assert svc.db.events(task["id"], "owner")[0]["requests"] == 0
        assert not svc._workers

        class UpdatingPlanner(CatalogFixturePlanner):
            async def generate(self, context):
                result = await super().generate(context)
                svc.intake.save(data.model_copy(update={"name": "配置变化", "version": 1}),
                                "owner", project["id"])
                return result

        svc.planner_factory = lambda project: UpdatingPlanner()
        queued = await svc.submit("owner", project["id"], "创建工单", uuid4().hex)
        done = await settle(svc, queued["id"])
        assert done["summary"]["context_usage"]["requests"] == 0
        assert done["summary"].get("task_satisfied") is not True
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_saved_test_configuration_change_disables_button_and_execution(tmp_path):
    pytest.importorskip("langgraph")
    svc = service(tmp_path)
    try:
        data = payload(svc)
        project = svc.intake.save(data, "owner")
        task = await svc.submit("owner", project["id"], "创建工单", uuid4().hex)
        done = await settle(svc, task["id"])
        run_id = done["summary"]["run_id"]
        first = svc.export_regression(task["id"], "owner", run_id)
        assert first == svc.export_regression(task["id"], "owner", run_id)
        svc.intake.save(data.model_copy(update={"name": "新版本", "version": 1}),
                        "owner", project["id"])
        assert not svc.saved.list("owner")[0]["available"]
        assert not svc.task_view(done)["summary"]["exports"][run_id]["available"]
        with pytest.raises(TaskConflict, match="VERSION_CHANGED"):
            await svc.saved.submit("owner", task["id"], run_id, uuid4().hex)
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_content_cache_cannot_preserve_changed_authority_or_accept_changed_document(tmp_path):
    svc = service(tmp_path)
    try:
        local = tmp_path / "server-profile"
        shutil.copytree(PROFILES / "tickets", local)
        svc.projects["tickets"] = local / "registered.project.json"
        data = payload(svc)
        project = svc.intake.save(data, "owner")
        assert svc.project(project["id"]).identity() == svc.project(project["id"]).identity()
        registration = json.loads((local / "registered.project.json").read_bytes())
        registration["require_plan_review"] = True
        (local / "registered.project.json").write_text(json.dumps(registration), encoding="utf-8")
        assert not svc.intake.list()[0]["ready"]
        with pytest.raises(ValueError, match="NOT_READY"):
            svc.project(project["id"])
        with pytest.raises(TaskConflict):
            svc.intake.save(data.model_copy(update={"version": 1}), "owner", project["id"])
        with pytest.raises(ValueError):
            svc.intake.save(data.model_copy(update={"document": "not an OpenAPI document"}),
                            "owner")
        assert len(svc.intake.list()) == 1
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_disabled_service_is_an_actual_503_and_does_not_register_projects(tmp_path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)),
                                 base_url="http://127.0.0.1:8000") as client:
        assert (await client.get("/api/task-session")).status_code == 503
        assert (await client.get("/api/project-intake")).status_code == 503
        assert (await client.post("/api/project-intake", json={})).status_code == 503
    assert not (tmp_path / "project-intake.sqlite3").exists()
