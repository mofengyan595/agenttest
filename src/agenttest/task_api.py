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
    user_task: str = Field(min_length=1, max_length=2000)
    submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    suite: bool = False
    parent_task_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    replace_pending: bool = False


class StartRequest(StrictModel):
    plan_version: int = Field(ge=1)


class ExportRequest(StrictModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


async def body(request, model):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON required")
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > 12_000:
            raise HTTPException(413, "Task input too large")
        data.extend(part)
    try:
        return model.model_validate(bounded_json(data.decode("utf-8"), 12_000))
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
            return enabled().db.get(task_id, who)
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
                            max_age=3600, path="/api")
        response.headers["Cache-Control"] = "no-store"
        return dict(csrf_token=csrf)

    @app.get("/api/projects")
    async def projects(request: Request):
        owner(request)
        return dict(projects=enabled().project_list())

    @app.get("/api/tasks")
    async def tasks(request: Request):
        return dict(tasks=enabled().db.list(owner(request)))

    @app.post("/api/tasks", status_code=202)
    async def submit(request: Request):
        who = owner(request, True)
        payload = await body(request, TaskSubmission)
        try:
            return await enabled().submit(who, payload.project_id, payload.user_task,
                payload.submission_id, suite=payload.suite, parent=payload.parent_task_id,
                replace_pending=payload.replace_pending)
        except TaskConflict:
            raise HTTPException(409, "Task submission or context conflict") from None
        except (ValueError, OSError):
            raise HTTPException(422, "Project or task requires review") from None
        except KeyError:
            raise HTTPException(404, "Parent task not found") from None

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str, request: Request):
        return lookup(task_id, owner(request))

    @app.post("/api/tasks/{task_id}/start")
    async def start(task_id: str, request: Request):
        who = owner(request, True)
        lookup(task_id, who)
        payload = await body(request, StartRequest)
        try:
            return await enabled().start(task_id, who, payload.plan_version)
        except TaskConflict:
            raise HTTPException(409, "Plan is stale or already started") from None

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
