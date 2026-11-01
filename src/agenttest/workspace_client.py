"""CLI and MCP adapters to the same session-protected TaskService HTTP API."""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field

from agenttest.report_files import pinned_directory, writer_lock
from agenttest.safety import StrictModel, TargetConfig
from agenttest.task_api import FixedRunRequest
from agenttest.task_store import TERMINAL


class Empty(StrictModel):
    pass


class TaskId(StrictModel):
    task_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class RunId(TaskId):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class SubmitTest(StrictModel):
    project_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    user_task: str = Field(default="", max_length=2000)
    submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class StartTest(TaskId):
    plan_version: int = Field(ge=1)
    review_approved: Literal[True]


REQUESTS = dict(list_projects=Empty, submit_test=SubmitTest, task_status=TaskId,
    read_evidence=RunId, save_test=RunId, list_saved_tests=Empty, run_saved_test=FixedRunRequest,
    cancel_test=TaskId, start_test=StartTest)
MUTATIONS = {"submit_test", "save_test", "run_saved_test", "cancel_test", "start_test"}


def workspace_origin(value):
    # Product server only: no target URLs, DNS, proxy, credentials, paths or redirects.
    origin = TargetConfig(base_url=value, allowed_operations=["workspace"]).base_url
    if not origin.startswith("http://"):
        raise ValueError("LOCAL_HTTP_WORKSPACE_REQUIRED")
    return origin


class WorkspaceClient:
    def __init__(self, server: str, session_file: Path, *, allow_model=False,
                 allow_reviewed_start=False, transport=None):
        self.server = workspace_origin(server)
        self.session_file = session_file.absolute()
        if ".." in session_file.parts:
            raise ValueError("SESSION_PATH_INVALID")
        self.allow_model = allow_model
        self.allow_reviewed_start = allow_reviewed_start
        self.transport = transport
        self._lock = asyncio.Lock()

    def _cookie(self, value=None):
        path = self.session_file
        with pinned_directory(path.parent, ".", create=True) as directory:
            with writer_lock(directory):
                try:
                    with os.fdopen(directory.open(path.name, readonly=True), "rb") as source:
                        raw = source.read(2001)
                    if len(raw) > 2000:
                        raise ValueError("SESSION_FILE_INVALID")
                    saved = json.loads(raw)
                    if (set(saved) != {"server", "cookie"} or saved["server"] != self.server
                            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", saved["cookie"])):
                        raise ValueError("SESSION_SERVER_OR_COOKIE_INVALID")
                    if value is not None and saved["cookie"] != value:
                        raise ValueError("SESSION_COOKIE_CHANGED")
                    return saved["cookie"]
                except FileNotFoundError:
                    if value is None:
                        return None
                    data = json.dumps(dict(server=self.server, cookie=value)).encode()
                    with os.fdopen(directory.open(path.name, create=True), "wb") as destination:
                        destination.write(data)
                    return value

    async def _response(self, client, method, path, payload=None, headers=None):
        async with client.stream(method, path, json=payload, headers=headers) as response:
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                if len(raw) > 2_000_000:
                    raise ValueError("WORKSPACE_RESPONSE_LIMIT")
            if response.status_code >= 300:
                raise ValueError("WORKSPACE_HTTP_" + str(response.status_code))
            return json.loads(raw)

    async def call(self, name, arguments=None):
        if name not in REQUESTS:
            return dict(ok=False, error="WORKSPACE_TOOL_NOT_AUTHORIZED")
        try:
            data = REQUESTS[name].model_validate(arguments or {}).model_dump()
            if name == "submit_test" and not self.allow_model:
                raise ValueError("MODEL_SUBMISSION_REQUIRES_EXPLICIT_REAL")
            if name == "start_test" and not self.allow_reviewed_start:
                raise ValueError("PLAN_START_REQUIRES_OPERATOR_REVIEW")
            # Per client serialize session initialization, never retry a failed mutation.
            async with self._lock:
                cookie = self._cookie()
                async with httpx.AsyncClient(base_url=self.server, transport=self.transport,
                        timeout=20., trust_env=False, follow_redirects=False,
                        headers={"Origin": self.server}, cookies={
                            "agenttest_task_session": cookie} if cookie else {}) as client:
                    session = await self._response(client, "GET", "/api/task-session")
                    token = session.get("csrf_token")
                    values = {c.value for c in client.cookies.jar
                              if c.name == "agenttest_task_session"}
                    current = next(iter(values)) if len(values) == 1 else None
                    if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
                            or not current or not re.fullmatch(r"[A-Za-z0-9_-]{43}", current)):
                        raise ValueError("WORKSPACE_SESSION_INVALID")
                    self._cookie(current)
                    task = data.get("task_id")
                    method, path, payload = "GET", "/api/projects", None
                    if name == "submit_test":
                        method, path, payload = "POST", "/api/tasks", data
                    elif name == "task_status":
                        path = f"/api/tasks/{task}"
                    elif name == "read_evidence":
                        path = f"/api/tasks/{task}/reports/{data['run_id']}"
                    elif name == "save_test":
                        method, path = "POST", f"/api/tasks/{task}/save-test"
                        payload = dict(run_id=data["run_id"])
                    elif name == "list_saved_tests":
                        path = "/api/saved-tests"
                    elif name == "run_saved_test":
                        method, path, payload = "POST", "/api/saved-tests/run", data
                    elif name == "cancel_test":
                        method, path, payload = "POST", f"/api/tasks/{task}/cancel", {}
                    elif name == "start_test":
                        method, path = "POST", f"/api/tasks/{task}/start"
                        payload = dict(plan_version=data["plan_version"])
                    result = await self._response(client, method, path, payload,
                        {"X-AgentTest-CSRF": token} if name in MUTATIONS else None)
            return dict(ok=True, data=result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # No raw HTTP exceptions, input values, cookies or local paths cross this adapter.
            code = str(exc)
            allowed = {"MODEL_SUBMISSION_REQUIRES_EXPLICIT_REAL", "SESSION_FILE_INVALID",
                "PLAN_START_REQUIRES_OPERATOR_REVIEW",
                "SESSION_SERVER_OR_COOKIE_INVALID", "SESSION_COOKIE_CHANGED",
                "WORKSPACE_RESPONSE_LIMIT", "WORKSPACE_SESSION_INVALID"}
            return dict(ok=False, error=code if code in allowed or re.fullmatch(
                r"WORKSPACE_HTTP_[0-9]{3}", code) else "WORKSPACE_REQUEST_REJECTED")

    async def wait(self, task_id, seconds=610):
        async with asyncio.timeout(seconds):
            while True:
                result = await self.call("task_status", dict(task_id=task_id))
                if not result["ok"] or result["data"]["status"] in TERMINAL:
                    return result
                if result["data"]["status"] == "WAITING_REVIEW":
                    return result
                await asyncio.sleep(.25)


def task_exit_code(row):
    if row["status"] != "FINISHED":
        return 2
    summary = row["summary"]
    if summary.get("fixed_regression_passed") is True:
        return 0
    if summary.get("outcome") == "ASSERTION_FAILED" or any(
            c.get("outcome") == "ASSERTION_FAILED" for c in summary.get("cases", [])):
        return 1
    if summary.get("complete") is True and summary.get("checks_passed") is True:
        return 0
    return 2
