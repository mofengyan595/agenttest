"""Headless adapters use the actual task API with ASGI, no keys, sockets or Docker."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_product_design import case
from test_product_tasks import response, service
from test_resource_lifecycle import lifecycle_project

from agenttest.generic_intake import IntakeRole
from agenttest.main import create_app
from agenttest.project_intake import IntakeInput
from agenttest.workspace_cli import execute
from agenttest.workspace_client import WorkspaceClient, task_exit_code, workspace_origin


def design():
    value = case()
    for name, operation, policy in [("read", "readJob", "web_rule_2"),
                                     ("delete", "removeJob", "web_rule_3")]:
        value["plan"]["steps"].append(dict(id=name, operation_id=operation, policy_id=policy,
            role=None, arguments=[dict(location="path_parameters", name="jobId", value=None,
                                      from_step="insert", select="$.id")]))
    return dict(interpretation="准备本轮数据、读取并清理", relevant=True, cases=[value],
                questions=[], not_covered=[])


@pytest.mark.asyncio
async def test_cli_shared_api_write_regression_and_expired_config(tmp_path, monkeypatch, capsys):
    pytest.importorskip("langgraph")
    requests, ids, models = [], [], []
    resources = {"foreign"}

    def target(request):
        requests.append(request.method)
        if request.method == "POST":
            key = "fresh_" + str(len(ids))
            ids.append(key)
            resources.add(key)
            return httpx.Response(201, json=dict(id=key))
        key = request.url.path.rsplit("/", 1)[-1]
        assert key in resources
        if request.method == "DELETE":
            resources.remove(key)
            return httpx.Response(204)
        return httpx.Response(200, json=dict(state="ready"))

    def model(request):
        models.append(1)
        return response(design())

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path / "workspace", model)
    origin = "http://127.0.0.1:18120"
    transport = httpx.ASGITransport(app=create_app(svc.reports.root, task_service=svc,
                                                allowed_origins=frozenset({origin})))
    session_file = tmp_path / "client/.env"
    client = WorkspaceClient(origin, session_file, transport=transport, allow_model=True)
    try:
        proj, setup = lifecycle_project()
        setup.roles = [IntakeRole(alias="auditor", bearer_env="AGENTTEST_TARGET_AUDITOR")]
        svc.environ["AGENTTEST_TARGET_AUDITOR"] = "initial-private-credential"
        saved = svc.intake.save(IntakeInput(name="通用写项目", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        assert (await client.call("list_projects"))["ok"]
        submitted = await client.call("submit_test", dict(project_id=saved["id"],
            user_task="", submission_id=uuid4().hex))
        assert submitted["ok"], submitted
        done = await client.wait(submitted["data"]["id"], 30)
        assert done["ok"] and task_exit_code(done["data"]) == 0, done
        task = done["data"]["id"]
        run = done["data"]["summary"]["cases"][0]["run_id"]
        assert (await client.call("read_evidence", dict(task_id=task, run_id=run)))["ok"]
        wrong_run = await client.call("read_evidence", dict(task_id=task, run_id=uuid4().hex))
        assert not wrong_run["ok"]
        assert (await client.call("save_test", dict(task_id=task, run_id=run)))["ok"]
        # New CLI/MCP process using the same protected cookie retains owner identity.
        restarted = WorkspaceClient(origin, session_file, transport=transport)
        args = SimpleNamespace(workspace_action="regression", task_id=task, run_id=run,
            submission_id=uuid4().hex, wait_seconds=30, output=tmp_path / "ci-report.json")
        assert await execute(args, restarted) == 0
        artifact = json.loads(args.output.read_text(encoding="utf-8"))
        assert artifact["task"]["data"]["summary"]["context_usage"]["model_calls"] == 0
        assert artifact["reports"][0]["data"]["resource_lifecycle"][0]["status"] == "CLEANED"
        assert len(models) == 1 and len(ids) == 2 and resources == {"foreign"}
        cookie = dict(line.split("=", 1) for line in session_file.read_text().splitlines())[
            "AGENTTEST_WORKSPACE_SESSION"]
        assert cookie not in capsys.readouterr().out
        outsider = WorkspaceClient(origin, tmp_path / "other/.env", transport=transport)
        assert not (await outsider.call("task_status", dict(task_id=task)))["ok"]
        before = list(requests)
        svc.environ["AGENTTEST_TARGET_AUDITOR"] = "rotated-private-credential"
        expired = await restarted.call("run_saved_test", dict(task_id=task, run_id=run,
                                                               submission_id=uuid4().hex))
        assert not expired["ok"] and requests == before
        record = svc.intake.record(saved["id"])
        svc.intake.save(IntakeInput(name="已更改规则", document=record["document"],
            generic=setup, confirmed=True, version=record["version"]), "operator", saved["id"])
        before = list(requests)
        blocked = await restarted.call("run_saved_test", dict(task_id=task, run_id=run,
                                                               submission_id=uuid4().hex))
        assert not blocked["ok"] and requests == before
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_client_rejects_unknown_arguments_and_requires_explicit_model(tmp_path):
    def network(request):
        pytest.fail("Invalid input must not reach the workspace")

    client = WorkspaceClient("http://127.0.0.1:18120", tmp_path / ".env",
                              transport=httpx.MockTransport(network))
    for action, data in [("submit_test", dict(project_id="web_demo", user_task="测试",
                                             submission_id=uuid4().hex)),
                         ("task_status", dict(task_id="../../private")),
                         ("list_projects", dict(target="http://evil")),
                         ("execute_shell", dict(command="secret"))]:
        assert not (await client.call(action, data))["ok"]


@pytest.mark.parametrize("url", ["http://example.org", "http://127.0.0.1/private",
    "http://user:secret@127.0.0.1", "https://127.0.0.1", "http://10.0.0.1"])
def test_workspace_origin_cannot_expand_targets(url):
    with pytest.raises(ValueError):
        workspace_origin(url)


@pytest.mark.parametrize("status,summary,code", [
    ("FINISHED", dict(fixed_regression_passed=True), 0),
    ("FINISHED", dict(outcome="ASSERTION_FAILED"), 1),
    ("FINISHED", dict(outcome="TEST_INFRA_ERROR"), 2),
    ("NEEDS_REVIEW", {}, 2), ("INTERRUPTED", {}, 2), ("CANCELLED", {}, 2),
    ("FINISHED", dict(complete=False, checks_passed=True), 2)])
def test_ci_exit_code_preserves_failure_review_and_infrastructure(status, summary, code):
    assert task_exit_code(dict(status=status, summary=summary)) == code


@pytest.mark.asyncio
async def test_fixed_cancel_restart_keeps_owned_resource_without_replay(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    entered, requests, blocked = asyncio.Event(), [], False

    async def target(request):
        requests.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json=dict(id="fresh_" + str(requests.count("POST"))))
        if request.method == "DELETE":
            return httpx.Response(204)
        if blocked:
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=dict(state="ready"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    root = tmp_path / "workspace"
    svc = service(root, lambda _: response(design()))
    origin, session = "http://127.0.0.1:18120", tmp_path / "client/.env"

    def client(current):
        return WorkspaceClient(origin, session, allow_model=True,
            transport=httpx.ASGITransport(app=create_app(current.reports.root, task_service=current,
                                                       allowed_origins=frozenset({origin}))))

    try:
        proj, setup = lifecycle_project()
        saved = svc.intake.save(IntakeInput(name="固定取消", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        api = client(svc)
        initial = await api.call("submit_test", dict(project_id=saved["id"], user_task="",
                                                    submission_id=uuid4().hex))
        done = (await api.wait(initial["data"]["id"], 30))["data"]
        source, run = done["id"], done["summary"]["cases"][0]["run_id"]
        assert (await api.call("save_test", dict(task_id=source, run_id=run)))["ok"]
        blocked = True
        repeated = await api.call("run_saved_test", dict(task_id=source, run_id=run,
                                                        submission_id=uuid4().hex))
        task = repeated["data"]["id"]
        await asyncio.wait_for(entered.wait(), 10)
        assert (await api.call("cancel_test", dict(task_id=task)))["ok"]
        cancelled = (await api.call("task_status", dict(task_id=task)))["data"]
        assert cancelled["status"] == "CANCELLED" and cancelled["summary"]["run_id"]
        assert cancelled["summary"]["resource_lifecycle"][0]["resource_id"] == "fresh_2"
        assert cancelled["summary"]["resource_lifecycle"][0]["status"] == "OWNED"
        before = list(requests)
        await svc.close()
        svc = service(root, lambda _: pytest.fail("Restart cannot plan or replay"))
        restarted = await client(svc).call("task_status", dict(task_id=task))
        assert restarted["ok"] and restarted["data"]["status"] == "CANCELLED"
        assert restarted["data"]["summary"]["resource_lifecycle"][0]["resource_id"] == "fresh_2"
        assert not svc._workers and requests == before
    finally:
        await svc.close()
