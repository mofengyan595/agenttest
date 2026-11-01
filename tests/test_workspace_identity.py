import httpx
import pytest
from test_task_service import service

from agenttest.main import create_app
from agenttest.report import fingerprint


@pytest.mark.asyncio
async def test_workspace_identity_is_persistent_while_csrf_and_other_browser_isolation_remain(
        tmp_path):
    svc = service(tmp_path)
    try:
        app = create_app(svc.reports.root, task_service=svc)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                    base_url="http://127.0.0.1:8000") as first:
            session = await first.get("/api/task-session")
            assert "Max-Age=31536000" in session.headers["set-cookie"]
            assert "HttpOnly" in session.headers["set-cookie"]
            assert "SameSite=strict" in session.headers["set-cookie"]
            csrf = session.json()["csrf_token"]
            owner = fingerprint(first.cookies.get("agenttest_task_session"))
            svc.db.create("a" * 32, owner, "submission", "tickets", "a" * 64, "b" * 64,
                          "FINISHED", {"display_title": "持久记录"})
            cookies = first.cookies
        # A fresh API instance has no old in-memory sessions or CSRF tokens.
        restarted = create_app(svc.reports.root, task_service=svc)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(restarted), cookies=cookies,
                                    base_url="http://127.0.0.1:8000") as same_browser:
            fresh = (await same_browser.get("/api/task-session")).json()["csrf_token"]
            assert fresh != csrf
            assert (await same_browser.get("/api/tasks")).json()["tasks"][0]["id"] == "a" * 32
            denied = await same_browser.post("/api/tasks", headers={"x-agenttest-csrf": csrf},
                                              json={})
            assert denied.status_code == 403
        async with httpx.AsyncClient(transport=httpx.ASGITransport(restarted),
                                    base_url="http://127.0.0.1:8000") as other_browser:
            await other_browser.get("/api/task-session")
            assert (await other_browser.get("/api/tasks")).json()["tasks"] == []
    finally:
        await svc.close()
