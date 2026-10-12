import httpx
import pytest
from fastapi.testclient import TestClient

from agenttest.main import create_app


@pytest.mark.parametrize("host", ["localhost", "localhost:5173", "127.0.0.1:8000",
                                  "[::1]:8000", "testserver"])
def test_expected_local_hosts_and_import_still_work(tmp_path, host):
    client = TestClient(create_app(tmp_path))
    assert client.get("/health", headers={"Host": host}).status_code == 200
    response = client.post("/api/spec/summary", headers={"Host": host}, json={
        "openapi": "3.1.0", "info": {"title": "local", "version": "1"}, "paths": {}})
    assert response.status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "0.0.0.0",
                                  "192.168.1.10", "localhost@evil.example", "localhost:99999",
                                  "localhost/path", "localhost:", "localhost:80?x=1"])
def test_unexpected_hosts_fail_before_routes(tmp_path, host):
    response = TestClient(create_app(tmp_path)).get("/health", headers={"Host": host})
    assert response.status_code == 400


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": "http://127.0.0.1:9999"}, {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://127.0.0.1:5173", "Sec-Fetch-Site": "cross-site"},
])
def test_browser_cross_site_and_unapproved_origins_are_blocked(tmp_path, method, headers):
    client = TestClient(create_app(tmp_path))
    response = getattr(client, method)("/api/spec/summary", headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize("origin", ["http://127.0.0.1:5173", "http://localhost:4173",
                                    "http://[::1]:8000"])
def test_vite_and_preview_origins_work(tmp_path, origin):
    client = TestClient(create_app(tmp_path))
    assert client.get("/health", headers={"Origin": origin,
                                         "Sec-Fetch-Site": "same-origin"}).status_code == 200


@pytest.mark.asyncio
async def test_non_loopback_peer_and_forwarded_host_cannot_bypass_guard(tmp_path):
    transport = httpx.ASGITransport(app=create_app(tmp_path), client=("203.0.113.9", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.get("/health", headers={"X-Forwarded-For": "127.0.0.1"})
        assert response.status_code == 403


def test_duplicate_host_origin_and_custom_local_origin(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/health", headers=[("Host", "localhost"),
                                          ("Host", "evil.example")]).status_code == 400
    assert client.get("/health", headers=[("Origin", "http://localhost:5173"),
                                          ("Origin", "https://evil.example")]).status_code == 403
    custom = TestClient(create_app(tmp_path, allowed_origins=frozenset({"http://localhost:9000"})))
    assert custom.get("/health", headers={"Origin": "http://localhost:9000"}).status_code == 200


def test_get_cross_site_has_no_cors_or_credentials(tmp_path):
    response = TestClient(create_app(tmp_path)).get("/health", headers={"Origin": "https://evil"})
    assert response.status_code == 403 and "access-control-allow-origin" not in response.headers
