"""New contracts use the ordinary intent, policy and executor gates, without a domain adapter."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.goal_derivation import derive_goal
from agenttest.goal_runtime import run_goal
from agenttest.main import create_app
from agenttest.project_intake import IntakeInput
from agenttest.report import Outcome, ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


def document():
    return json.dumps({"openapi": "3.0.3", "info": {"title": "Unregistered API", "version": "1"},
        "paths": {"/objects": {"post": {"operationId": "insertObject",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["title"], "properties": {
                    "title": {"type": "string", "minLength": 1}}, "additionalProperties": False}}}},
            "responses": {"201": {"description": "created"}}}},
            "/objects/{objectId}": {"get": {"operationId": "fetchObject", "parameters": [
                {"in": "path", "name": "objectId", "required": True,
                 "schema": {"type": "string"}}], "responses": {"200": {"description": "found"}}}}}})


def setup():
    return GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18987",
        isolated_confirmed=True, allow_mutations=True, operations=[
            dict(operation_id="insertObject", alias="新建条目", statuses=[201], arguments=[
                dict(location="body", name="title", value="operator chosen")], checks=[
                dict(select="$.id", operator="exists")]),
            dict(operation_id="fetchObject", alias="读取条目", statuses=[200], arguments=[
                dict(location="path_parameters", name="objectId", source_operation="insertObject",
                     selector="$.id")],
                 checks=[dict(select="$.title", expected="operator chosen")])]))


def service(root, **kwargs):
    def planner(project):
        from agenttest.workflow import FakeLLM

        return FakeLLM("{}")

    return TaskService({}, TaskStore(root / "tasks.sqlite3"), ReportStore(root / "reports"),
        planner, generic_intake=True, environ={}, **kwargs)


def test_omitting_optional_input_keeps_closed_policy_and_required_validation(tmp_path):
    from agenttest.dsl import Scenario
    from agenttest.executor import Executor

    data = json.loads(document())
    body = data["paths"]["/objects"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]
    body["properties"]["reviewerUserId"] = dict(type="integer", minimum=1)
    raw = json.dumps(data)
    config = setup()
    contract = load_openapi(raw)
    project = build_generic_project("optional", raw, contract, config)
    policy = project.registry.definitions()[0]
    assert [r.name for r in policy.binding_rules] == ["title"]
    plan = Scenario.model_validate(dict(name="省略可选字段", steps=[dict(id="create",
        operation_id="insertObject", policy_id=policy.id,
        body=dict(title=dict(value="operator chosen")))]))
    engine = Executor(project.target, contract, ReportStore(tmp_path), environ={},
                      registry=project.registry)
    engine.validate(plan)
    from agenttest.dsl import ArgumentBinding

    plan.steps[0].body["reviewerUserId"] = ArgumentBinding(value=999)
    with pytest.raises(ValueError):
        engine.validate(plan)
    config.operations[0].arguments.clear()
    with pytest.raises(ValueError, match="REQUIRED_ARGUMENT_MISSING"):
        build_generic_project("missing", raw, contract, config)


@pytest.mark.asyncio
async def test_new_document_persists_and_executes_original_gates_with_dynamic_resource(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.workflow import FakeLLM

    svc = service(tmp_path)
    try:
        config = setup()
        draft = svc.intake.save(IntakeInput(name="独立接口", document=document(), generic=config),
                                "operator")
        assert not draft["ready"] and draft["confirmation"] is None
        saved = svc.intake.save(IntakeInput(name="独立接口", generic=config,
            confirmed=True, version=1), "operator", draft["id"])
        assert saved["ready"] and saved["profile_id"] is None
        assert saved["confirmation"]["source"] == "WEB_OPERATOR_SESSION"
        project = svc.project(saved["id"])
        goal = derive_goal(project, "新建条目，然后读取条目")
        assert goal.task is not None, goal.reason
        assert len(goal.task.requirements.steps) == 2
        proposal = {"name": "new contract", "steps": [
            {"id": "create", "operation_id": "insertObject", "policy_id": "web_rule_1",
             "body": {"title": {"value": "operator chosen"}}},
            {"id": "read", "operation_id": "fetchObject", "policy_id": "web_rule_2",
             "path_parameters": {"objectId": {"from_step": "create", "select": "$.id"}}}]}
        observed = []

        def target(request):
            observed.append(request.url.path)
            if request.method == "POST":
                assert json.loads(request.content) == {"title": "operator chosen"}
                return httpx.Response(201, json={"id": "fresh-id", "title": "operator chosen"})
            return httpx.Response(200, json={"id": "fresh-id", "title": "operator chosen"})

        _, report = await run_goal(project, "新建条目，然后读取条目",
            FakeLLM(json.dumps(proposal)), svc.reports, environ={},
            transport=httpx.MockTransport(target))
        assert report.outcome == Outcome.PASS and report.task_satisfied
        assert observed == ["/objects", "/objects/fresh-id"]
        unknown = derive_goal(project, "新建条目并核验退款期限")
        assert unknown.task is None
        await svc.close()
        svc = service(tmp_path)
        assert svc.project(saved["id"]).identity() == project.identity()
    finally:
        await svc.close()


def test_target_mutation_dependency_and_schema_authority_remain_enforced():
    raw, config = document(), setup()
    contract = load_openapi(raw)
    for url in ("https://example.com", "http://169.254.169.254", "http://10.20.30.40",
                "http://127.0.0.1:8080/private", "http://user:password@127.0.0.1"):
        with pytest.raises(ValueError):
            changed = config.model_copy(update={"base_url": url})
            build_generic_project("web_test", raw, contract, changed)
    for change in ({"isolated_confirmed": False}, {"allow_mutations": False}):
        with pytest.raises(ValueError):
            build_generic_project("web_test", raw, contract, config.model_copy(update=change))
    config.operations[1].arguments[0].source_operation = "unapprovedOperation"
    with pytest.raises(ValueError, match="RESOURCE_SOURCE"):
        build_generic_project("web_test", raw, contract, config)
    config = setup()
    config.operations[0].arguments[0].value = 123
    with pytest.raises(ValueError):
        build_generic_project("web_test", raw, contract, config)
    config = setup()
    config.operations[0].arguments[0].source_operation = "fetchObject"
    config.operations[0].arguments[0].value = None
    with pytest.raises(ValueError, match="CYCLE"):
        build_generic_project("web_test", raw, contract, config)


@pytest.mark.asyncio
async def test_analysis_is_readonly_csrf_protected_and_confirmation_cannot_be_forged(tmp_path):
    svc = service(tmp_path)
    try:
        app = create_app(svc.reports.root, task_service=svc)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                    base_url="http://127.0.0.1:8000") as client:
            assert (await client.post("/api/project-intake/analyze",
                json={"document": document()})).status_code == 401
            token = (await client.get("/api/task-session")).json()["csrf_token"]
            assert (await client.post("/api/project-intake/analyze",
                json={"document": document()})).status_code == 403
            headers = {"x-agenttest-csrf": token}
            analysis = (await client.post("/api/project-intake/analyze", headers=headers,
                json={"document": document()})).json()
            assert analysis["model_calls"] == analysis["http_requests"] == 0
            assert not analysis["execution_authorized"]
            assert [o["operation_id"] for o in analysis["operations"]] == [
                "insertObject", "fetchObject"]
            assert svc.intake.list() == []
            saved = svc.intake.save(IntakeInput(name="Saved contract", document=document(),
                generic=setup()), "unit-owner")
            saved_analysis = await client.post("/api/project-intake/analyze", headers=headers,
                json={"project_id": saved["id"]})
            assert saved_analysis.status_code == 200
            assert saved_analysis.json() == analysis
            assert (await client.post("/api/project-intake/analyze", headers=headers,
                json={"project_id": "web_missing"})).status_code == 422
            assert (await client.post("/api/project-intake/analyze", headers=headers,
                json={})).status_code == 422
            unapproved = setup().model_copy(update={"isolated_confirmed": False}).model_dump()
            invalid = await client.post("/api/project-intake", headers=headers,
                json={"name": "unknown", "document": document(), "confirmed": True,
                      "generic": unapproved})
            assert invalid.status_code == 422 and len(svc.intake.list()) == 1
        suggestions = analyze_contract(load_openapi(document()))
        assert all(o["supported"] and not o["enabled"] for o in suggestions)
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_generic_login_uses_vault_and_runs_a_previously_unknown_protected_interface(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.workflow import FakeLLM

    doc = json.loads(document())
    doc["components"] = {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}
    doc["paths"] = {
        "/login": {"post": {"operationId": "signIn",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["username", "password"], "properties": {
                    "username": {"type": "string"}, "password": {"type": "string"}},
                "additionalProperties": False}}}}, "responses": {"200": {"description": "ok"}}}},
        "/private": {"get": {"operationId": "readPrivate", "security": [{"bearer": []}],
                             "responses": {"200": {"description": "ok"}}}}}
    raw = json.dumps(doc)
    config = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18987",
        isolated_confirmed=True, allow_mutations=True,
        roles=[dict(alias="tester", login_operation="signIn",
            username_env="AGENTTEST_TARGET_USERNAME", password_env="AGENTTEST_TARGET_PASSWORD",
            token_selector="$.auth_token")], operations=[
                dict(operation_id="signIn", alias="登录", role="tester", statuses=[200]),
                dict(operation_id="readPrivate", alias="读取私有内容", role="tester",
                    statuses=[200],
                    checks=[dict(select="$.value", expected=7)])]))
    project = build_generic_project("web_login", raw, load_openapi(raw), config)
    plan = dict(name="generic authenticated flow", steps=[
        dict(id="login", operation_id="signIn", role="tester", policy_id="web_rule_1"),
        dict(id="read", operation_id="readPrivate", role="tester", policy_id="web_rule_2")])
    seen = []

    def target(request):
        seen.append(request.url.path)
        if request.url.path == "/login":
            assert json.loads(request.content)["password"] == "private-test-password"
            return httpx.Response(200, json={"auth_token": "captured-unit-session"})
        assert request.headers["Authorization"] == "Bearer captured-unit-session"
        return httpx.Response(200, json={"value": 7})

    _, report = await run_goal(project, "读取私有内容", FakeLLM(json.dumps(plan)),
        ReportStore(tmp_path), environ={"AGENTTEST_TARGET_USERNAME": "unit-owner",
            "AGENTTEST_TARGET_PASSWORD": "private-test-password"},
        transport=httpx.MockTransport(target))
    assert report.outcome == Outcome.PASS and report.task_satisfied
    assert seen == ["/login", "/private"]
    assert "private-test-password" not in report.model_dump_json()
    assert "captured-unit-session" not in report.model_dump_json()
    config.operations[0].role = None
    with pytest.raises(ValueError, match="LOGIN_IDENTITY_MISMATCH"):
        build_generic_project("web_login", raw, load_openapi(raw), config)


@pytest.mark.asyncio
async def test_unconfirmed_generic_draft_cannot_store_known_target_credentials(tmp_path):
    svc = service(tmp_path)
    try:
        svc.environ["AGENTTEST_TARGET_BEARER"] = "private-draft-token"
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            svc.intake.save(IntakeInput(name="private-draft-token", document=document(),
                                       generic=setup()), "operator")
        assert not svc.intake.list()
    finally:
        await svc.close()
