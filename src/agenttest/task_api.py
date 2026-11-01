"""Session-protected local task API; browser inputs cannot supply targets or approvals."""

import asyncio
import hmac
import io
import json
import re
import secrets
import time
import zipfile

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field

from agenttest.plan_json import bounded_json
from agenttest.report import fingerprint
from agenttest.safety import StrictModel
from agenttest.task_store import TERMINAL, TaskConflict


class TaskSubmission(StrictModel):
    project_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    user_task: str = Field(default="", max_length=2000)
    submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    suite: bool = False
    parent_task_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    replace_pending: bool = False


class StartRequest(StrictModel):
    plan_version: int = Field(ge=1)


class ExportRequest(StrictModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ContractIntakeRequest(StrictModel):
    document: str = Field(min_length=1, max_length=1_000_000)


class FixedRunRequest(ExportRequest):
    task_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class PermissionAnswer(StrictModel):
    question_id: str = Field(pattern=r"^question_[0-9a-f]{20}$")
    status: int = Field(ge=400, le=499)
    project_version: int = Field(ge=1)


async def body(request, model, limit=12_000):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON required")
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > limit:
            raise HTTPException(413, "Task input too large")
        data.extend(part)
    try:
        return model.model_validate(bounded_json(data.decode("utf-8"), limit))
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Task input rejected") from None


def install_task_api(app, service):
    sessions = {}

    def enabled():
        if service is None:
            raise HTTPException(503, "Task execution is not configured")
        return service

    def owner(request, mutate=False):
        enabled()
        session = sessions.get(request.cookies.get("agenttest_task_session", ""))
        if session is None or session[2] < time.monotonic():
            raise HTTPException(401, "Local task session required")
        if mutate and not hmac.compare_digest(
                request.headers.get("x-agenttest-csrf", "").encode(), session[1].encode()):
            raise HTTPException(403, "Local task session verification failed")
        return session[0]

    def lookup(task_id, who):
        try:
            return enabled().task_view(enabled().db.get(task_id, who))
        except KeyError:
            raise HTTPException(404, "Task not found") from None

    @app.get("/api/task-session")
    async def session(request: Request, response: Response):
        enabled()
        now = time.monotonic()
        for key in [key for key, value in sessions.items() if value[2] < now]:
            del sessions[key]
        cookie = request.cookies.get("agenttest_task_session", "")
        if cookie in sessions:
            response.headers["Cache-Control"] = "no-store"
            return dict(csrf_token=sessions[cookie][1])
        if len(sessions) >= 64:
            raise HTTPException(429, "Local session limit reached")
        # The opaque HttpOnly bearer survives a local service restart; only its hash is in SQLite.
        cookie = cookie if re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie) else secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        sessions[cookie] = (fingerprint(cookie), csrf, now + 3600)
        response.set_cookie("agenttest_task_session", cookie, httponly=True, samesite="strict",
                            max_age=365 * 24 * 3600, path="/api")
        response.headers["Cache-Control"] = "no-store"
        return dict(csrf_token=csrf)

    @app.get("/api/projects")
    def projects(request: Request):
        owner(request)
        return dict(projects=enabled().project_list())

    @app.get("/api/project-intake")
    def intake(request: Request):
        owner(request)
        svc = enabled()
        return dict(projects=svc.intake.list(), profiles=svc.intake.profiles(),
                    registered_projects=svc.intake.registered(),
                    model_source=svc.model_status(),
                    model_usage_blocked=svc.db.model_usage_blocked(),
                    generic_intake=svc.generic_intake,
                    execution_target=("SYNTHETIC_MEMORY" if svc.synthetic
                                      else "AUTHORIZED_TEST_ENVIRONMENT"))

    @app.post("/api/project-intake/analyze")
    async def analyze_project(request: Request):
        owner(request, True)
        if not enabled().generic_intake:
            raise HTTPException(409, "Generic intake requires the configured workspace")
        payload = await body(request, ContractIntakeRequest, 1_100_000)
        from agenttest.generic_intake import analyze_contract

        try:
            return dict(operations=analyze_contract(enabled().intake.document(payload.document)),
                        execution_authorized=False, model_calls=0, http_requests=0)
        except (ValueError, OSError):
            raise HTTPException(422, "Invalid or unsupported OpenAPI document") from None

    @app.post("/api/project-intake", status_code=201)
    async def save_project(request: Request):
        return await save_intake(request)

    @app.post("/api/project-intake/{project_id}")
    async def update_project(project_id: str, request: Request):
        return await save_intake(request, project_id)

    async def save_intake(request, project_id=None):
        from agenttest.project_intake import IntakeInput

        who = owner(request, True)
        payload = await body(request, IntakeInput, 1_100_000)
        try:
            return enabled().intake.save(payload, who, project_id)
        except TaskConflict as exc:
            if str(exc) == "PROJECT_LIMIT_REACHED":
                raise HTTPException(409, "当前工作空间已接入100个项目，请联系操作员建立新的独立"
                                    "工作空间；现有记录会保留。") from None
            raise HTTPException(409, "项目版本已变化，请重新检查并确认当前配置。") from None
        except KeyError:
            raise HTTPException(422, "服务端没有此授权配置，请重新选择。") from None
        except ValueError as exc:
            code = getattr(getattr(exc, "diagnostic", None), "code", str(exc))
            public_codes = {
                "SELECT_UNIQUE_OPERATIONS", "ISOLATED_ENVIRONMENT_CONFIRMATION_REQUIRED",
                "TARGET_AUTHORIZATION_REQUIRED", "WRITE_AUTHORIZATION_REQUIRED",
                "ROLE_NOT_CONFIGURED", "PROTECTED_OPERATION_REQUIRES_ROLE",
                "LOGIN_IDENTITY_MISMATCH", "LOGIN_CONFIGURATION_REQUIRED",
                "RESOURCE_SOURCE_NOT_SELECTED", "RESOURCE_DEPENDENCY_CYCLE",
                "RESOURCE_ROLE_RELATION_REQUIRED", "REQUIRED_ARGUMENT_MISSING",
                "ARGUMENT_NOT_IN_CONTRACT", "PROJECT_CREDENTIAL_LITERAL", "OPENAPI_VALUE_TYPE",
                "OPENAPI_VALUE_ENUM", "OPENAPI_VALUE_LENGTH", "OPENAPI_VALUE_BOUND"}
            if code in public_codes:
                raise HTTPException(422, dict(code=code)) from None
            raise HTTPException(422,
                "无法保存：请检查项目名称、有效的 OpenAPI 文档及凭据配置。") from None
        except OSError:
            raise HTTPException(422,
                "无法保存：请检查项目名称、有效的 OpenAPI 文档及凭据配置。") from None

    @app.get("/api/tasks")
    def tasks(request: Request):
        return dict(tasks=[enabled().task_view(t) for t in enabled().db.list(owner(request))])

    @app.get("/api/saved-tests")
    def saved_tests(request: Request):
        return dict(tests=enabled().saved.list(owner(request)))

    @app.post("/api/saved-tests/run", status_code=202)
    async def fixed_run(request: Request):
        who = owner(request, True)
        payload = await body(request, FixedRunRequest)
        try:
            return await enabled().saved.submit(who, payload.task_id, payload.run_id,
                                                 payload.submission_id)
        except (TaskConflict, ValueError, OSError):
            raise HTTPException(409,
                "仅支持配置未变化的合成或只读真实固定测试；请重新检查项目与资产。") from None
        except KeyError:
            raise HTTPException(404, "Saved test not found") from None

    @app.post("/api/tasks", status_code=202)
    async def submit(request: Request):
        who = owner(request, True)
        payload = await body(request, TaskSubmission)
        try:
            result = await enabled().submit(who, payload.project_id, payload.user_task,
                payload.submission_id, suite=payload.suite, parent=payload.parent_task_id,
                replace_pending=payload.replace_pending)
            return enabled().task_view(result)
        except TaskConflict:
            raise HTTPException(409, "Task submission or context conflict") from None
        except (ValueError, OSError):
            raise HTTPException(422, "Project or task requires review") from None
        except KeyError:
            raise HTTPException(404, "Parent task not found") from None

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str, request: Request):
        return lookup(task_id, owner(request))

    @app.get("/api/tasks/{task_id}/timeline")
    def timeline(task_id: str, request: Request):
        who = owner(request)
        lookup(task_id, who)
        return dict(events=enabled().db.events(task_id, who))

    @app.post("/api/tasks/{task_id}/start")
    async def start(task_id: str, request: Request):
        who = owner(request, True)
        lookup(task_id, who)
        payload = await body(request, StartRequest)
        try:
            return await enabled().start(task_id, who, payload.plan_version)
        except TaskConflict:
            raise HTTPException(409, "Plan is stale or already started") from None

    @app.post("/api/tasks/{task_id}/permission-answer")
    async def answer_permission(task_id: str, request: Request):
        who = owner(request, True)
        payload = await body(request, PermissionAnswer)
        try:
            return enabled().intake.answer_permission(task_id, who, payload.question_id,
                                                      payload.status, payload.project_version)
        except TaskConflict:
            raise HTTPException(409, "规则或项目版本已变化，请重新核对。") from None
        except (ValueError, KeyError, OSError):
            raise HTTPException(422, "只可确认本项目当前补问中明确列出的只读身份预期。") from None

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: str, request: Request):
        who = owner(request, True)
        lookup(task_id, who)
        return await enabled().cancel(task_id, who)

    @app.post("/api/tasks/{task_id}/regression")
    async def regression(task_id: str, request: Request):
        who = owner(request, True)
        lookup(task_id, who)
        payload = await body(request, ExportRequest)
        try:
            directory = enabled().export_regression(task_id, who, payload.run_id)
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in directory.iterdir():
                    archive.writestr(path.name, path.read_bytes())
        except (TaskConflict, ValueError, OSError):
            raise HTTPException(409, "Regression export requires an unchanged completed run"
                                ) from None
        return Response(data.getvalue(), media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="agenttest-regression.zip"',
                     "Cache-Control": "no-store"})

    @app.get("/api/tasks/{task_id}/events")
    async def events(task_id: str, request: Request, after: int = 0):
        who = owner(request)
        lookup(task_id, who)
        try:
            cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
            if cursor > 2**63 - 1:
                raise ValueError("Invalid cursor")
        except ValueError:
            raise HTTPException(422, "Invalid event cursor") from None

        async def stream():
            nonlocal cursor
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                rows = enabled().db.events(task_id, who, cursor)
                for event in rows:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                if lookup(task_id, who)["status"] in TERMINAL:
                    return
                if await request.is_disconnected():
                    return
                if not rows:
                    yield ": waiting\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})
