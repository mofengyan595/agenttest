"""Persistent ordinary product tasks, with no implicit write replay or invented intent proof."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_product_design import case, project, settings

from agenttest.generic_intake import GenericSetup, IntakeRole
from agenttest.main import create_app
from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.project_intake import IntakeInput
from agenttest.report import ModelCallStats, ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TaskConflict, TaskStore


def service(root, model):
    return TaskService({}, TaskStore(root / "tasks.sqlite3"), ReportStore(root / "reports"),
        lambda _: None, designer_factory=lambda _: DesignModel(settings(),
            transport=httpx.MockTransport(model), profile="generic"), generic_intake=True,
        environ={}, model_status=lambda: "CONFIGURED_MODEL")


async def finished(svc, row):
    for _ in range(200):
        current = svc.db.get(row["id"], "operator")
        if current["status"] in {"FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "CANCELLED"}:
            return current
        await asyncio.sleep(0.01)
    pytest.fail("Task did not terminate")


def response(design):
    return httpx.Response(200, json=dict(usage=dict(prompt_tokens=200, completion_tokens=150,
        total_tokens=350), choices=[dict(finish_reason="stop", message=dict(
            content=json.dumps(design, ensure_ascii=False)))]))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_count", [7, 70])
async def test_default_round_reserves_later_operations_before_extra_variations(
        tmp_path, monkeypatch, operation_count):
    from agenttest.generic_intake import IntakeOperation
    from agenttest.goal_suite import SuiteLedger

    requests, contexts = [], []
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="Budget reads", version="1"),
        paths={f"/items/{i}": {"get": dict(operationId=f"read{i}", parameters=[dict(
            name="n", **{"in": "query"}, schema=dict(type="integer", minimum=1, maximum=3))],
            responses={"200": dict(description="ok")})} for i in range(operation_count)}))

    class Designs:
        stats = ModelCallStats(calls=1, input_tokens=1, output_tokens=1, total_tokens=2)

        async def generate(self, context):
            contexts.append(context)
            policies = {p["operation_id"]: p["id"] for p in context["policies"]}
            cases = []
            for operation in context["primary_operations"]:
                cases.append(dict(title="正常读取接口", kind="normal", plan=dict(name="read",
                    steps=[dict(id="read", operation_id=operation, role=None,
                        policy_id=policies[operation], arguments=[dict(location="query",
                            name="n", value=1, from_step=None, select=None)])])))
            if len(context["primary_operations"]) == 6:
                for value in (2, 3):
                    extra = json.loads(json.dumps(cases[0]))
                    extra["plan"]["steps"][0]["arguments"][0]["value"] = value
                    cases.append(extra)
            return json.dumps(dict(interpretation="先覆盖每个接口，再增加合法输入变化",
                relevant=True, cases=cases, questions=[], not_covered=[]))

    def target(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    monkeypatch.setattr("agenttest.product_tasks.SuiteLedger", lambda *_: SuiteLedger(8, 9, 500000))
    svc = service(tmp_path, lambda _: pytest.fail("Use the deterministic design fixture"))
    svc.designer_factory = lambda _: Designs()
    try:
        setup = GenericSetup(base_url="http://127.0.0.1:18998", isolated_confirmed=True,
            assisted=True, operations=[IntakeOperation(operation_id=f"read{i}", alias=f"读取{i}",
                statuses=[200], arguments=[dict(location="query", name="n", value=1,
                                               generated=True)]) for i in range(operation_count)])
        saved = svc.intake.save(IntakeInput(name="分组预算", document=document,
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "", uuid4().hex)
        async with asyncio.timeout(60):
            while (done := svc.db.get(row["id"], "operator"))["status"] not in {
                    "FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "CANCELLED"}:
                await asyncio.sleep(0.05)
        assert len(requests) == 8
        held = [c for c in done["summary"]["cases"] if c["status"] == "NOT_EXECUTED"]
        if operation_count == 7:
            assert set(requests) == {f"/items/{i}" for i in range(7)}
            assert [c["remaining_http_requests"] for c in contexts] == [7, 1]
            assert len(held) == 1 and "保留后续接口" in held[0]["reason"]
        else:
            assert len(set(requests)) == 8
            assert set(requests) <= {f"/items/{i}" for i in range(operation_count)}
            assert [c["remaining_http_requests"] for c in contexts] == [6, 2]
            assert any("保留后续接口" in c["reason"] for c in held)
            assert done["summary"]["coverage"]["operation_denominator"] == 70
            assert len(done["summary"]["untested_operations"]) == 62
        assert not done["summary"]["complete"]
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_readiness_cache_is_detached_and_invalidates_record_credentials_and_authority(
        tmp_path, monkeypatch):
    svc = service(tmp_path, lambda request: pytest.fail("Readiness must not call a model"))
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="缓存隔离", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        record = svc.intake.record(saved["id"])
        original = svc.intake._inspect_generic
        calls = []

        def inspect(value):
            calls.append(1)
            return original(value)

        monkeypatch.setattr(svc.intake, "_inspect_generic", inspect)
        first = svc.intake.inspect_generic(record)
        first["inventory"]["operations"].clear()
        assert svc.intake.inspect_generic(record)["inventory"]["operations"]
        assert not calls  # Save already populated the exact snapshot.
        svc.environ["AGENTTEST_TARGET_NEW_BEARER"] = "new-private-credential"
        svc.intake.inspect_generic(record)
        assert len(calls) == 1
        changed = {**record, "version": record["version"] + 1, "name": "新版本"}
        assert svc.intake.inspect_generic(changed)["name"] == "新版本"
        assert len(calls) == 2
        svc.generic_intake = False
        assert not svc.intake.inspect_generic(changed)["ready"]
        assert len(calls) == 3
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_blank_round_asks_known_missing_account_and_executes_independent_flow(
        tmp_path, monkeypatch):
    from agenttest.generic_intake import IntakeOperation

    received = []
    flow = case()
    flow["plan"]["steps"].append(dict(id="read", operation_id="readJob", role=None,
        policy_id="web_rule_2", arguments=[dict(location="path_parameters", name="jobId",
            value=None, from_step="insert", select="$.id")]))

    def model(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert set(context["primary_operations"]) == {"insertJob", "readJob"}
        assert context["deferred_questions"] == []  # A default group gets no whole-project dump.
        return response(dict(interpretation="创建后读取本轮任务", cases=[flow],
                             questions=[], not_covered=[]))

    def target(request):
        received.append(request.method + " " + request.url.path)
        assert request.url.path != "/private"
        return httpx.Response(201 if request.method == "POST" else 200, json=dict(id="ready-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    try:
        proj, setup = project()
        doc = json.loads(proj.document)
        doc["components"] = dict(securitySchemes=dict(bearer=dict(type="http", scheme="bearer")))
        doc["paths"]["/private"] = {"get": dict(operationId="readPrivate", summary="受限配置",
            security=[dict(bearer=[])], responses={"200": dict(description="ok")})}
        setup.operations.append(IntakeOperation(operation_id="readPrivate", alias="受限配置",
                                                statuses=[200]))
        saved = svc.intake.save(IntakeInput(name="直接开始但缺账号", document=json.dumps(doc),
            generic=setup, confirmed=True), "operator")
        done = await finished(svc, await svc.submit("operator", saved["id"], "", uuid4().hex))
        assert received == ["POST /jobs", "GET /jobs/ready-job"]
        assert not done["summary"]["complete"]
        assert done["summary"]["coverage"]["operation_denominator"] == 3
        question = done["summary"]["questions"][0]
        assert question["operation_id"] == "readPrivate" and "身份" in question["question"]
        assert done["summary"]["context_usage"]["model_calls"] == 1
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_partial_product_task_keeps_evidence_after_restart(tmp_path, monkeypatch):
    model_calls, target_calls = [], []

    def model(request):
        model_calls.append(1)
        return response(dict(interpretation="检查创建；通知期限待补充", cases=[case()],
            questions=[dict(operation_id="insertJob", question="创建后应在多少秒内通知？",
                            requested_behavior="通知期限", kind="clarification", role=None,
                            status_choices=[])], not_covered=[dict(operation_id="insertJob",
                                reason="通知期限待确认", source_quote="通知期限")]))

    def target(request):
        target_calls.append(request)
        return httpx.Response(201, json=dict(id="new-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="任务系统", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        submission = uuid4().hex
        row = await svc.submit("operator", saved["id"], "创建任务并检查通知期限", submission)
        done = await finished(svc, row)
        assert done["status"] == "FINISHED", done
        assert not done["summary"]["complete"] and done["summary"]["questions"]
        assert done["summary"]["coverage"]["operation_denominator"] == 2
        assert done["summary"]["coverage"]["executed_operations"] == 1
        assert done["summary"]["context_usage"]["total_tokens"] == 350
        assert done["summary"]["task_satisfied"] is None
        run_id = done["summary"]["cases"][0]["run_id"]
        report = svc.reports.read(run_id)
        assert report.outcome.value == "PASS" and report.task_mode == "model_designed"
        assert report.intent_verified is False and report.task_satisfied is None
        duplicate = await svc.submit("operator", saved["id"], "创建任务并检查通知期限", submission)
        assert duplicate["id"] == row["id"]
        assert len(model_calls) == len(target_calls) == 1
        await svc.close()
        svc = service(tmp_path, model)
        assert svc.db.context(row["id"], "operator")["text"] == "创建任务并检查通知期限"
        asset = svc.saved.asset(svc.db.get(row["id"], "operator"), run_id)
        assert asset[0].steps[0].operation_id == "insertJob"
        with pytest.raises(ValueError, match="FIXED_WRITE_LIFECYCLE_REQUIRED"):
            svc.export_regression(row["id"], "operator", run_id)
        view = svc.task_view(svc.db.get(row["id"], "operator"))
        assert not view["summary"]["exports"][run_id]["available"]
        assert len(model_calls) == len(target_calls) == 1
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_successful_upload_explains_why_fixed_export_is_unavailable(tmp_path, monkeypatch):
    from test_generic_upload_poll import document, setup

    design = dict(title="上传并等待", kind="flow", plan=dict(name="upload", steps=[
        dict(id="send", operation_id="sendImport", role=None, policy_id="web_rule_1", arguments=[]),
        dict(id="wait", operation_id="readImport", role=None, policy_id="web_rule_2", arguments=[
            dict(location="path_parameters", name="job", value=None,
                 from_step="send", select="$.job")])]))

    calls = []

    def target(request):
        calls.append(request.method)
        return httpx.Response(201, json=dict(job="fresh")) if request.method == "POST" else (
            httpx.Response(200, json=dict(state="PENDING" if len(calls) == 2 else "DONE")))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, lambda _: response(dict(interpretation="执行合成文件上传与终态检查",
        cases=[design], questions=[], not_covered=[])))
    try:
        config = setup()
        config.operations[1].poll.interval = 0.02
        saved = svc.intake.save(IntakeInput(name="上传资源", document=document(), generic=config,
            confirmed=True), "operator")
        done = await finished(svc, await svc.submit("operator", saved["id"], "", uuid4().hex))
        case = done["summary"]["cases"][0]
        assert case["outcome"] == "PASS"
        view = svc.task_view(done)["summary"]["exports"][case["run_id"]]
        assert not view["available"] and "上传、轮询" in view["reason"]
        assert calls == ["POST", "GET", "GET"]
        timings = done["summary"]["timings"]
        assert timings["execution_ms"] >= 20 and timings["planning_ms"] > 0
        assert timings["total_ms"] >= timings["planning_ms"] + timings["execution_ms"] - 1
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_blank_focus_normal_api_runs_and_saved_read_repeats_without_a_model(tmp_path,
                                                                              monkeypatch):
    calls, requests = [], []
    plan = dict(title="检查状态", kind="normal", plan=dict(name="status", steps=[dict(
        id="read", operation_id="getState", policy_id="web_rule_1", role=None, arguments=[])]))

    def model(request):
        calls.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return response(dict(interpretation="检查接口状态", cases=[plan],
                             questions=[], not_covered=[]))

    def target(request):
        requests.append(1)
        return httpx.Response(200, json=dict(state="ready"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="State", version="1"), paths={
        "/state": {"get": dict(operationId="getState",
                               responses={"200": dict(description="ok")})}}))
    config = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998", assisted=True,
        isolated_confirmed=True, operations=[dict(operation_id="getState", alias="查看状态",
            statuses=[200])]))
    svc = service(tmp_path, model)
    try:
        saved = svc.intake.save(IntakeInput(name="状态系统", document=document, generic=config,
            confirmed=True), "operator")
        app = create_app(svc.reports.root, task_service=svc)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                    base_url="http://127.0.0.1:8000") as client:
            csrf = (await client.get("/api/task-session")).json()["csrf_token"]
            submitted = await client.post("/api/tasks", headers={"x-agenttest-csrf": csrf},
                json=dict(project_id=saved["id"], submission_id=uuid4().hex))
            assert submitted.status_code == 202, submitted.text
            # API owner is the session hash. Wait the actual worker instead of changing ownership.
            task = submitted.json()
            await asyncio.gather(*list(svc._workers.values()))
            done = (await client.get("/api/tasks/" + task["id"])).json()
            assert done["summary"]["complete"], done
            run_id = done["summary"]["cases"][0]["run_id"]
            exported = await client.post(f"/api/tasks/{task['id']}/regression",
                headers={"x-agenttest-csrf": csrf}, json=dict(run_id=run_id))
            assert exported.status_code == 200, exported.text
            listed = (await client.get("/api/saved-tests")).json()["tests"]
            assert listed[0]["project_name"] == "状态系统"
            repeated = await client.post("/api/saved-tests/run", headers={"x-agenttest-csrf": csrf},
                json=dict(task_id=task["id"], run_id=run_id, submission_id=uuid4().hex))
            assert repeated.status_code == 202, repeated.text
            await asyncio.gather(*list(svc._workers.values()))
            rerun = (await client.get("/api/tasks/" + repeated.json()["id"])).json()
            assert rerun["summary"]["fixed_regression_passed"], rerun
            assert rerun["summary"]["context_usage"]["model_calls"] == 0
            assert len(calls) == 1 and len(requests) == 2
            following = await client.post("/api/tasks", headers={"x-agenttest-csrf": csrf},
                json=dict(project_id=saved["id"], parent_task_id=task["id"],
                          submission_id=uuid4().hex))
            assert following.status_code == 202
            await asyncio.gather(*list(svc._workers.values()))
            from agenttest.product_tasks import DEFAULT_FOLLOWUP

            assert calls[-1]["user_task"] == DEFAULT_FOLLOWUP
            assert calls[-1]["previous_result"]["executed_operations"] == ["getState"]
            assert calls[-1]["previous_result"]["status"] == "FINISHED"
            assert calls[-1]["previous_result"]["complete"] is True
            assert len(calls) == 2 and len(requests) == 3
    finally:
        await svc.close()


def test_durable_unknown_or_pending_usage_blocks_new_calls_after_restart(tmp_path):
    db = TaskStore(tmp_path / "tasks.sqlite3")
    db.begin_model_call("unknown-call", "interrupted-task")
    restarted = TaskStore(tmp_path / "tasks.sqlite3")
    assert restarted.model_usage_blocked()
    with pytest.raises(TaskConflict, match="MODEL_USAGE_REQUIRES_RECONCILIATION"):
        restarted.begin_model_call("second", "another-task")
    restarted.finish_model_call("unknown-call", ModelCallStats(calls=1))
    assert restarted.model_usage_blocked()


@pytest.mark.asyncio
async def test_runtime_cache_is_detached_and_invalidated_by_record_or_credential_changes(
        tmp_path, monkeypatch):
    svc = service(tmp_path, lambda request: response({}))
    try:
        proj, setup = project()
        setup.roles = [IntakeRole(alias="unused", bearer_env="AGENTTEST_TARGET_CACHE_BEARER")]
        svc.environ["AGENTTEST_TARGET_CACHE_BEARER"] = "private-unit-initial-bearer"
        saved = svc.intake.save(IntakeInput(name="缓存检查", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        original = svc.intake.inspect
        checks = []

        def inspect(record):
            checks.append(record["version"])
            return original(record)

        monkeypatch.setattr(svc.intake, "inspect", inspect)
        first = svc.project(saved["id"])
        first.target.allowed_operations.clear()
        again = svc.project(saved["id"])
        assert again.target.allowed_operations
        assert checks == [1]
        svc.environ["AGENTTEST_TARGET_CACHE_BEARER"] = "private-unit-rotated-bearer"
        assert svc.project(saved["id"]).identity() != again.identity()
        assert checks == [1, 1]
        changed = svc.intake.save(IntakeInput(name="名称已修改", version=1,
            generic=setup, confirmed=True), "operator", saved["id"])
        assert svc.project(saved["id"]).identity() != again.identity()
        assert changed["version"] == 2 and checks[-1] == 2
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_saved_business_context_is_reused_without_becoming_a_permission(tmp_path,
                                                                            monkeypatch):
    observed = []

    def model(request):
        envelope = json.loads(request.content)
        observed.append(json.loads(envelope["messages"][1]["content"]))
        return response(dict(interpretation="按已有规则创建任务", cases=[case()],
                             questions=[], not_covered=[]))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda request: httpx.Response(201, json=dict(id="fresh-job"))))
    svc = service(tmp_path, model)
    try:
        proj, config = project()
        notes = "此项目用于联调。通知时限尚未确定。"
        saved = svc.intake.save(IntakeInput(name="业务资料", document=proj.document.decode(),
            business_notes=notes, generic=config, confirmed=True), "operator")
        assert saved["business_notes"] == notes
        changed = svc.intake.save(IntakeInput(name="新名称", version=1, generic=config,
            confirmed=True), "operator", saved["id"])
        assert changed["business_notes"] == notes
        row = await svc.submit("operator", saved["id"], "创建联调任务", uuid4().hex)
        await finished(svc, row)
        assert observed[0]["project_notes"] == notes
        assert not observed[0]["confirmed_access_rules"]
        assert observed[0]["user_task"] == "创建联调任务"
        config.roles = [IntakeRole(alias="unused", bearer_env="AGENTTEST_TARGET_NOTES_BEARER")]
        svc.environ["AGENTTEST_TARGET_NOTES_BEARER"] = "private-unit-notes-bearer"
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            svc.intake.save(IntakeInput(name="不能保存凭据", document=proj.document.decode(),
                business_notes="private-unit-notes-bearer", generic=config), "operator")
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("question_stage", ["design", "focus"])
async def test_permission_answer_is_version_bound_and_reused_without_granting_model_authority(
        tmp_path, monkeypatch, question_stage):
    calls, requests, focus_calls = [], [], []
    first_case = case()
    first_case["plan"]["steps"][0]["role"] = "owner"

    def model(request):
        calls.append(1)
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        questions = ([dict(existing_question_ref=context["deferred_questions"][0]["id"])]
            if question_stage == "focus" else [dict(operation_id="readJob", kind="permission",
                role="reader", status_choices=[403, 404],
                question="另一测试身份读取新建任务，应返回 403 还是 404？",
                requested_behavior="另一身份读取所有者新建任务")])
        return response(dict(interpretation="新建并核验另一身份的访问", cases=[first_case],
                             questions=questions, not_covered=[]))

    def target(request):
        requests.append(request)
        return httpx.Response(201, json=dict(id="fresh-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    if question_stage == "focus":
        from test_product_focus import focus_response

        from agenttest.product_focus import TestFocusModel

        def focus(request):
            context = json.loads(json.loads(request.content)["messages"][1]["content"])
            focus_calls.append(context.get("permission_question_roles"))
            return focus_response(dict(interpretation="新建任务，补问另一身份的读取规则",
                mode="selected", operation_ids=["insertJob", "readJob"], question=None,
                expectation_questions=[dict(operation_id="readJob", kind="permission",
                    role="reader", status_choices=[403, 404], source_quote="另一身份能否读取新任务",
                    question="另一身份读取新任务应返回403还是404？",
                    requested_behavior="另一身份读取所有者新建任务")]), request)
        svc.focus_factory = lambda _: TestFocusModel(
            settings(), transport=httpx.MockTransport(focus), profile="generic")
    try:
        proj, config = project()
        doc = json.loads(proj.document)
        doc["components"] = dict(securitySchemes=dict(bearer=dict(type="http", scheme="bearer")))
        for entry in doc["paths"].values():
            for operation in entry.values():
                operation["security"] = [dict(bearer=[])]
        config.roles = [IntakeRole(alias="owner", bearer_env="AGENTTEST_TARGET_OWNER_BEARER"),
                        IntakeRole(alias="reader", bearer_env="AGENTTEST_TARGET_READER_BEARER")]
        for operation in config.operations:
            operation.role = "owner"
        svc.environ.update(AGENTTEST_TARGET_OWNER_BEARER="private-unit-owner-bearer",
                           AGENTTEST_TARGET_READER_BEARER="private-unit-reader-bearer")
        saved = svc.intake.save(IntakeInput(name="权限规则", document=json.dumps(doc),
            generic=config, confirmed=True), "operator")
        assert saved["ready"], saved
        row = await svc.submit("operator", saved["id"], "看看另一身份能否读取新任务", uuid4().hex)
        done = await finished(svc, row)
        assert done["summary"]["questions"], (focus_calls, done["summary"]["design_records"])
        question = done["summary"]["questions"][0]
        assert not any("private-unit-" in str(r) for r in [done])
        with pytest.raises(TaskConflict, match="QUESTION_PROJECT_VERSION_CHANGED"):
            svc.intake.answer_permission(row["id"], "operator", question["id"], 403, 99)
        changed = svc.intake.answer_permission(row["id"], "operator", question["id"], 403, 1)
        assert changed["version"] == 2 and changed["ready"]
        assert len(calls) == len(requests) == 1  # confirmation does not execute or replay
        assert len(focus_calls) == int(question_stage == "focus")
        if focus_calls:
            assert focus_calls[0]["readJob"] == ["reader"]
        policies = svc.project(saved["id"]).registry.definitions()
        access = next(p for p in policies if p.id.endswith("access_1"))
        assert access.roles == ["reader"] and access.assertions.status.codes == [403]
        assert access.binding_rules[0].role_relation == "different"
        assert svc.db.get(row["id"], "operator")["summary"]["questions"]
        answered = svc.task_view(svc.db.get(row["id"], "operator"))
        assert answered["summary"]["questions"][0]["confirmed_statuses"] == [403]
        assert "confirmed_statuses" not in svc.db.get(row["id"], "operator")[
            "summary"]["questions"][0]
        with pytest.raises(TaskConflict):
            svc.intake.answer_permission(row["id"], "operator", question["id"], 404, 2)
        # An unavailable secondary account blocks only its permission scenario.
        del svc.environ["AGENTTEST_TARGET_READER_BEARER"]
        partial = svc.intake.inspect(svc.intake.record(saved["id"]))
        assert partial["ready"], partial
        assert partial["inventory"]["pending_checks"][0]["role"] == "reader"
        available = svc.project(saved["id"])
        assert "reader" not in available.target.roles
        assert all(p.roles != ["reader"] for p in available.registry.definitions())
        from agenttest.product_focus import select_focus

        observed = []

        class CaptureFocus:
            async def generate(self, context):
                observed.append(context)
                return json.dumps(dict(interpretation="保留已确认规则，等待另一身份凭据",
                    mode="selected", operation_ids=["readJob"], question=None,
                    pending_questions=[], not_covered=[]))

        await select_focus(available, CaptureFocus(),
            dict(current_request="按已确认规则检查另一身份", requests=[]),
            environ=svc.environ, inventory=partial["inventory"],
            available_identity_aliases=["owner", "reader"])
        pending = next(p for p in observed[0]["confirmed_access_rules"]
                       if p.get("pending_identity"))
        assert pending["roles"] == ["reader"] and pending["expected_statuses"] == [403]
        assert "reader" not in observed[0]["permission_question_roles"].get("readJob", [])
        assert len(requests) == 1
    finally:
        await svc.close()


def test_assisted_roles_can_share_the_documented_login_operation():
    import json

    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import analyze_contract, build_generic_project

    doc = dict(openapi="3.0.3", info=dict(title="Shared login", version="1"),
        components=dict(securitySchemes=dict(bearer=dict(type="http", scheme="bearer"))), paths={
            "/session": {"post": dict(operationId="login", requestBody=dict(required=True,
                content={"application/json": dict(schema=dict(type="object", properties=dict(
                    username=dict(type="string"), password=dict(type="string", format="password")),
                    required=["username", "password"]))}),
                    responses={"200": dict(description="ok")})},
            "/private": {"get": dict(operationId="private", security=[dict(bearer=[])],
                responses={"200": dict(description="ok"), "403": dict(description="forbidden")})}})
    raw = json.dumps(doc)
    contract = load_openapi(raw)
    analysis = analyze_contract(contract)
    login_suggestion = next(op for op in analysis if op["operation_id"] == "login")
    password = next(a for a in login_suggestion["arguments"] if a["name"] == "password")
    assert password["value"] is None
    config = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998", assisted=True,
        isolated_confirmed=True, allow_mutations=True, roles=[dict(alias=alias,
            login_operation="login", username_env=f"AGENTTEST_TARGET_{alias.upper()}_USERNAME",
            password_env=f"AGENTTEST_TARGET_{alias.upper()}_PASSWORD")
            for alias in ("owner", "other")],
        operations=[dict(operation_id="login", alias="登录", role="owner", statuses=[200]),
            dict(operation_id="private", alias="读取受限配置", role="owner", statuses=[200],
                access_checks=[dict(role="other", statuses=[403],
                                    rule="另一身份不允许访问此配置")])]))
    registered = build_generic_project("shared_login", raw, contract, config)
    login_policies = [p for p in registered.registry.definitions() if p.operation_id == "login"]
    assert {p.roles[0] for p in login_policies} == {"owner", "other"}
    config.allow_mutations = False
    config.default_role = "owner"
    for item in config.operations:
        item.role = None
    readonly = build_generic_project("shared_login", raw, contract, config)
    readonly.target.permit("login", "POST")
    assert not readonly.target.allow_mutations
    assert all(item.role is None for item in config.operations)
    private = next(p for p in readonly.registry.definitions() if p.operation_id == "private")
    assert private.roles == ["owner"]


def test_saved_legacy_multi_step_contract_retains_strict_json_ordering_edges():
    from agenttest.fixed_plan import load_saved_contract
    from agenttest.goal_derivation import derive_goal
    from agenttest.project_goals import load_goal_project

    path = Path("examples/trusted-testing/projects/tickets/registered.project.json")
    project = load_goal_project(path)
    task = derive_goal(project, "创建工单，关闭工单，查询工单，其他用户不能读取工单").task
    assert len(task.requirements.before) == 3
    restored = load_saved_contract(task.model_dump_json())
    assert restored.model_dump() == task.model_dump()
    invalid = json.loads(task.model_dump_json())
    invalid["unexpected_permission"] = True
    with pytest.raises(ValueError):
        load_saved_contract(json.dumps(invalid))


def test_readonly_environment_can_use_explicit_login_without_granting_business_writes():
    from agenttest.safety import RoleCredential, TargetConfig

    target = TargetConfig(environment="isolated", allowed_operations=["session", "create"],
        allow_mutations=False, roles=dict(owner=RoleCredential(login_operation="session",
            username_env="AGENTTEST_TARGET_USERNAME", password_env="AGENTTEST_TARGET_PASSWORD")))
    target.permit("session", "POST")
    with pytest.raises(ValueError):
        target.permit("create", "POST")
    with pytest.raises(ValueError):
        target.permit("session", "DELETE")
    with pytest.raises(ValueError):
        target.permit("not_allowed", "POST")


@pytest.mark.asyncio
async def test_all_blocked_assisted_intake_persists_without_bypassing_secret_checks(tmp_path):
    svc = service(tmp_path, lambda request: response({}))
    try:
        proj, config = project()
        config.allow_mutations = False
        saved = svc.intake.save(IntakeInput(name="待授权", document=proj.document.decode(),
            generic=config, confirmed=True), "operator")
        assert not saved["ready"] and saved["inventory"]["total"] == 2
        assert saved["confirmation"] and saved["missing"]
        config.roles = [IntakeRole(alias="unused", bearer_env="AGENTTEST_TARGET_UNUSED_BEARER")]
        svc.environ["AGENTTEST_TARGET_UNUSED_BEARER"] = "private-unused-bearer"
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            svc.intake.save(IntakeInput(name="private-unused-bearer",
                document=proj.document.decode(), generic=config, confirmed=True), "operator")
        assert len(svc.intake.list()) == 1
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_failed_read_gets_one_bounded_probe_without_changing_original_failure(tmp_path,
                                                                                 monkeypatch):
    requests = []
    read_case = dict(title="检查当前状态", kind="normal", plan=dict(name="state", steps=[dict(
        id="read", operation_id="readState", role=None, policy_id="web_rule_1", arguments=[])]))

    def model(request):
        return response(dict(interpretation="检查状态", cases=[read_case],
                             questions=[], not_covered=[]))

    def target(request):
        requests.append(request.method)
        return httpx.Response(500, json={"state": "broken"})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    try:
        document = json.dumps(dict(openapi="3.0.3", info=dict(title="State", version="1"),
            paths={"/state": {"get": dict(operationId="readState",
                                          responses={"200": dict(description="ok")})}}))
        config = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998", assisted=True,
            isolated_confirmed=True, max_requests=2, operations=[dict(operation_id="readState",
                alias="读取状态", statuses=[200])]))
        saved = svc.intake.save(IntakeInput(name="故障状态", document=document, generic=config,
            confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "检查状态是否正常", uuid4().hex)
        done = await finished(svc, row)
        assert done["status"] == "FINISHED", done
        assert done["summary"]["complete"]
        assert not done["summary"]["checks_passed"]
        case_row = done["summary"]["cases"][0]
        report = svc.reports.read(case_row["run_id"])
        assert report.outcome.value == "ASSERTION_FAILED"
        assert len(report.investigations) == 1 and report.investigations[0].requests_used == 1
        assert report.steps[0].checks[0].actual == 500
        assert done["summary"]["context_usage"]["requests"] == 2
        assert requests == ["GET", "GET"]
        assert case_row["investigations"][0]["cause_confirmed"] is False
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_failed_model_design_explains_each_unexecuted_interface_without_http(
        tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda request: pytest.fail("Invalid design sent a business request")))
    svc = service(tmp_path, lambda request: response({}))
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="方案失败", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "创建后读取任务", uuid4().hex)
        done = await finished(svc, row)
        assert not done["summary"]["complete"]
        assert done["summary"]["context_usage"]["requests"] == 0
        assert done["summary"]["coverage"]["operation_denominator"] == 2
        assert len(done["summary"]["untested_operations"]) == 2
        assert all("模型方案未通过格式校验" in op["reason"]
                   for op in done["summary"]["untested_operations"])
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_repeated_validated_input_facts_are_retained_without_repeating_the_write(
        tmp_path, monkeypatch):
    from test_product_focus import focus_response

    from agenttest.product_focus import TestFocusModel

    sent = []
    requirement = dict(operation_id="insertJob", location="body", name="quantity", path=[],
                       value=2, source_quote="数量2")
    def focus(request):
        return focus_response(dict(interpretation="创建一件数量为2的测试资料", mode="selected",
            operation_ids=["insertJob"], normal_operation_ids=["insertJob"], question=None,
            input_requirements=[requirement, requirement]), request)
    def target(request):
        sent.append(request)
        return httpx.Response(201, json=dict(id="one-new-job"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, lambda _: response(dict(interpretation="创建一次并核对响应",
        cases=[case()], questions=[], not_covered=[])))
    svc.focus_factory = lambda _: TestFocusModel(
        settings(), transport=httpx.MockTransport(focus), profile="generic")
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="重复证据不是重复写入",
            document=proj.document.decode(), generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "创建任务，数量2", uuid4().hex)
        done = await finished(svc, row)
        assert done["summary"]["complete"], done["summary"]
        assert len(done["summary"]["focus"]["input_requirements"]) == 2
        assert len(sent) == 1 and sent[0].method == "POST"
        assert json.loads(sent[0].content)["quantity"] == 2
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_historical_negative_label_without_an_actual_negative_cannot_export_or_replay(
        tmp_path, monkeypatch):
    import copy

    from test_negative_values import case as read_case
    from test_negative_values import read_project

    sent = []
    proj, setup = read_project()
    base = next(p for p in proj.registry.definitions() if not p.negative)
    def target(request):
        sent.append(request)
        return httpx.Response(200, json=dict(ok=True))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, lambda _: response(dict(interpretation="只执行真实正常读取",
        cases=[read_case(base.id)], questions=[], not_covered=[])))
    try:
        saved = svc.intake.save(IntakeInput(name="历史元数据保护",
            document=proj.document.decode(), generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "读取报告", uuid4().hex)
        done = await finished(svc, row)
        assert done["summary"]["complete"], done["summary"]
        run = done["summary"]["cases"][0]["run_id"]
        output = svc.export_regression(row["id"], "operator", run)
        original_report = svc.reports.read(run).model_dump_json()
        historical = copy.deepcopy(done["summary"])
        historical["cases"][0]["kinds"] = ["negative"]
        assert historical["cases"][0]["approved_negative_steps"] == 0
        svc.db.transition(row["id"], "operator", "FINISHED", "FINISHED", summary=historical)
        view = svc.task_view(svc.db.get(row["id"], "operator"))
        assert not view["summary"]["exports"][run]["available"]
        assert "没有实际受控异常检查" in view["summary"]["exports"][run]["reason"]
        with pytest.raises(TaskConflict, match="NEGATIVE_CHECK_NOT_EXECUTED"):
            svc.export_regression(row["id"], "operator", run)
        with pytest.raises(TaskConflict, match="NEGATIVE_CHECK_NOT_EXECUTED"):
            await svc.saved.submit("operator", row["id"], run, uuid4().hex)
        assert not svc.saved.list("operator")[0]["available"]
        assert output.exists() and svc.reports.read(run).model_dump_json() == original_report
        assert len(sent) == 1  # Merely viewing, exporting or replay admission adds no HTTP.
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_one_controlled_negative_flow_can_cover_its_real_normal_control_and_fixed_replay(
        tmp_path, monkeypatch):
    from test_negative_values import case as read_case
    from test_negative_values import policy, read_project
    from test_product_focus import focus_response

    from agenttest.product_checks import check_index
    from agenttest.product_focus import TestFocusModel

    proj, setup = read_project()
    base = next(p for p in proj.registry.definitions() if not p.negative)
    control = read_case(base.id)["plan"]["steps"][0]
    control["id"] = "control"
    negative = policy(proj)
    sent = []
    def focus(request):
        return focus_response(dict(interpretation="正常读取与受控类型检查", mode="selected",
            operation_ids=["readReports"], normal_operation_ids=["readReports"], question=None,
            requested_checks=[dict(policy_id=negative.id, source_quote="文档批准的类型检查")],
            input_requirements=[dict(operation_id="readReports", location="query", name="limit",
                path=[], value=1, source_quote="正常limit=1")]), request,
            approved_checks=check_index(proj))
    def designer(_):
        return response(dict(interpretation="同一场景先做真实正常对照，再检查错误类型",
            cases=[], required_cases={negative.id: dict(title="正常对照与类型拒绝",
                before=[control], after=[], check=read_case(negative.id)["plan"]["steps"][0])},
            questions=[], not_covered=[]))
    def target(request):
        sent.append(dict(request.url.params))
        return httpx.Response(200 if request.url.params["limit"] == "1" else 422,
                              json=dict(ok=True))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: TestFocusModel(
        settings(), transport=httpx.MockTransport(focus), profile="generic")
    try:
        saved = svc.intake.save(IntakeInput(name="真实正常对照",
            document=proj.document.decode(), generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "正常limit=1，再做文档批准的类型检查。",
                               uuid4().hex)
        done = await finished(svc, row)
        assert done["summary"]["complete"], done["summary"]
        assert len(done["summary"]["cases"]) == 1
        actual = done["summary"]["cases"][0]
        assert actual["approved_negative_steps"] == actual["negative_rejections_passed"] == 1
        assert sent == [dict(limit="1", order="asc"),
                        dict(limit=str(negative.negative.value), order="asc")]
        svc.export_regression(row["id"], "operator", actual["run_id"])
        fixed = await svc.saved.submit("operator", row["id"], actual["run_id"], uuid4().hex)
        replay = await finished(svc, fixed)
        assert replay["summary"]["fixed_regression_passed"]
        assert replay["summary"]["context_usage"]["model_calls"] == 0
        assert len(sent) == 4 and sent[:2] == sent[2:]
    finally:
        await svc.close()


def test_approved_capability_coverage_keeps_unrequested_and_blocked_checks_visible():
    from agenttest.product_tasks import ProductTasks

    catalog = [dict(policy_id=key, operation_id="read", field="limit", kind="boundary_outside",
                    example=str(i)) for i, key in enumerate(["done", "blocked", "unplanned"])]
    summary = dict(inventory=dict(total=48, operations=[]), complete=False,
        negative_check_catalog=catalog, requested_checks=[dict(policy_id="blocked")],
        cases=[dict(status="CANCELLED", outcome="TEST_INFRA_ERROR", planned_steps=2,
            executed_steps=1, http_requests=1, executed_operations=["read"],
            planned_policy_ids=["done", "blocked"], executed_policy_ids=["done"])])
    ProductTasks.coverage(summary)
    assert summary["coverage"]["approved_check_denominator"] == 3
    assert summary["coverage"]["attempted_approved_checks"] == 1
    assert summary["coverage"]["requested_check_denominator"] == 1
    assert summary["coverage"]["executed_requested_checks"] == 0
    assert [c["policy_id"] for c in summary["uncovered_negative_checks"]] == [
        "blocked", "unplanned"]
    assert "已要求或安排" in summary["uncovered_negative_checks"][0]["reason"]
    assert "本轮未安排" in summary["uncovered_negative_checks"][1]["reason"]
    assert summary["negative_check_catalog"] == catalog


@pytest.mark.asyncio
async def test_configured_provider_key_is_rejected_before_project_or_task_persistence(
        tmp_path, monkeypatch):
    from agenttest.goal_server import configured_task_service

    secret = "fixture-provider-private-credential-1837"
    model_env = tmp_path / "model.env"
    model_env.write_text("OPENAI_API_KEY=" + secret +
        "\nOPENAI_BASE_URL=https://unit.invalid/v1\nAGENTTEST_MODEL=unit-model\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unit.invalid/v1")
    monkeypatch.setenv("AGENTTEST_MODEL", "unit-model")
    workspace = tmp_path / "workspace"
    svc = configured_task_service(workspace, projects={}, model_env=model_env, generic_intake=True)
    try:
        # Provider credentials are not target credentials.
        assert "OPENAI_API_KEY" not in svc.environ
        proj, setup = project()
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            svc.intake.save(IntakeInput(name="不能保存提供方凭据",
                document=proj.document.decode(), business_notes=secret,
                generic=setup, confirmed=True), "operator")
        saved = svc.intake.save(IntakeInput(name="安全项目", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        with pytest.raises(ValueError, match="TASK_SECRET_REJECTED"):
            await svc.submit("operator", saved["id"], "请检查资料 " + secret, uuid4().hex)
        with svc.db.connect() as db:
            assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM model_usage_journal").fetchone()[0] == 0
    finally:
        await svc.close()
    assert all(secret.encode() not in p.read_bytes() for p in workspace.rglob("*") if p.is_file())
