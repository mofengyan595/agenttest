"""Offline isolation gates; no Docker Engine, Java, subprocess or HTTP is required."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from agenttest import courseinsight_env as env


def docker_fixture():
    containers, volumes = [], []
    for service, ports in env.PORTS.items():
        name = "ai-stub" if service == "ai-service" else service
        mounts = [{"Type": "volume", "Name": f"{env.PROJECT}_{name}", "Destination": dest}
                  for name, dest in env.VOLUMES[service].items()]
        containers.append({"Name": f"/{env.PROJECT}-{name}", "Id": service,
                           "Config": {"Labels": {"com.docker.compose.project": env.PROJECT,
                                                 "com.docker.compose.service": service},
                                      "Env": ["MYSQL_DATABASE=courseinsight_demo",
                                              "MYSQL_USER=user",
                                              "MYSQL_PASSWORD=synthetic-password"]},
                           "State": {"Running": True, "Health": {"Status": "healthy"}},
                           "Mounts": mounts, "NetworkSettings": {"Ports": {
                               key: [{"HostIp": "127.0.0.1", "HostPort": port}]
                               for key, port in ports.items()}}})
        volumes.extend({"Name": mount["Name"],
                        "Labels": {"com.docker.compose.project": env.PROJECT}} for mount in mounts)
    settings = {"MYSQL_DATABASE": "courseinsight_demo", "MYSQL_USER": "user",
                "MYSQL_PASSWORD": "synthetic-password"}
    return containers, volumes, settings


def test_isolated_existing_demo_evidence_is_safe():
    evidence = env.validate_docker(*docker_fixture())
    assert len(evidence) == 5
    assert "synthetic-password" not in json.dumps(evidence)


def test_compose_source_must_match_operator_directory(tmp_path):
    labels = {"com.docker.compose.project.working_dir": str(tmp_path),
              "com.docker.compose.project.config_files": str(tmp_path / "compose.demo.yaml")}
    env.check_compose_source(labels, tmp_path)
    labels["com.docker.compose.project.working_dir"] = str(tmp_path / "old-location")
    with pytest.raises(env.PreflightError, match="^DEMO_COMPOSE_SOURCE_MISMATCH$"):
        env.check_compose_source(labels, tmp_path)


@pytest.mark.parametrize("fault,code", [
    ("missing", "DEMO_CONTAINER_MISSING"), ("project", "DEMO_CONTAINER_SCOPE"),
    ("service", "DEMO_CONTAINER_SCOPE"), ("health", "DEMO_CONTAINER_UNHEALTHY"),
    ("running", "DEMO_CONTAINER_UNHEALTHY"), ("ip", "DEMO_PORT_SCOPE"),
    ("port", "DEMO_PORT_SCOPE"), ("extra_port", "DEMO_PORT_SCOPE"),
    ("mount", "DEMO_VOLUME_SCOPE"), ("bind", "DEMO_MOUNT_SCOPE"),
    ("volume_label", "DEMO_VOLUME_SCOPE"), ("shared", "DEMO_VOLUME_SHARED"),
    ("duplicate_service_shared", "DEMO_VOLUME_SHARED"), ("database", "DEMO_DATABASE_SCOPE"),
    ("password", "DEMO_DATABASE_SCOPE"),
])
def test_reject_unconfirmed_infrastructure(fault, code):
    containers, volumes, settings = docker_fixture()
    c = containers[0]
    if fault == "missing":
        containers.pop(0)
    elif fault in {"project", "service"}:
        c["Config"]["Labels"][f"com.docker.compose.{fault}"] = "foreign"
    elif fault == "health":
        c["State"]["Health"]["Status"] = "unhealthy"
    elif fault == "running":
        c["State"]["Running"] = False
    elif fault in {"ip", "port"}:
        key = "HostIp" if fault == "ip" else "HostPort"
        c["NetworkSettings"]["Ports"]["3306/tcp"][0][key] = "0.0.0.0"
    elif fault == "extra_port":
        c["NetworkSettings"]["Ports"]["9999/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "9"}]
    elif fault == "mount":
        c["Mounts"][0]["Name"] = "courseinsight-cloud_mysql_data"
    elif fault == "bind":
        c["Mounts"].append({"Type": "bind", "Destination": "/data", "RW": True})
    elif fault == "volume_label":
        volumes[0]["Labels"]["com.docker.compose.project"] = "courseinsight-cloud"
    elif fault in {"shared", "duplicate_service_shared"}:
        other = copy.deepcopy(c)
        other["Name"], other["Id"] = "/foreign", "foreign"
        if fault == "shared":
            other["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
        containers.append(other)
    elif fault == "database":
        settings["MYSQL_DATABASE"] = "courseinsight"
    else:
        settings["MYSQL_PASSWORD"] = "wrong-secret"
    with pytest.raises(env.PreflightError, match=f"^{code}$"):
        env.validate_docker(containers, volumes, settings)


def test_load_only_necessary_credentials_no_interpolation(tmp_path):
    base = ("MYSQL_DATABASE=courseinsight_demo\nMYSQL_PORT=3307\nMYSQL_USER=user\n"
            "MYSQL_PASSWORD=synthetic\nBOOTSTRAP_ADMIN_ENABLED=true\n"
            "BOOTSTRAP_ADMIN_USERNAME=synthetic-admin\nBOOTSTRAP_ADMIN_PASSWORD=synthetic\n")
    path = tmp_path / "demo.env"
    path.write_text(base + "OPENAI_API_KEY=never-load\n", encoding="utf-8")
    settings = env.demo_settings(tmp_path)
    assert "OPENAI_API_KEY" not in settings
    path.write_text(base + "MYSQL_PASSWORD=${OTHER_SECRET}\n", encoding="utf-8")
    with pytest.raises(env.PreflightError, match="^DEMO_SETTINGS_INVALID$"):
        env.demo_settings(tmp_path)


@pytest.mark.parametrize("change", ["MYSQL_DATABASE=courseinsight", "MYSQL_PORT=3306",
                                    "BOOTSTRAP_ADMIN_ENABLED=false"])
def test_demo_config_scope_is_not_a_claim(tmp_path, change):
    path = tmp_path / "demo.env"
    path.write_text("MYSQL_DATABASE=courseinsight_demo\nMYSQL_PORT=3307\n"
                    "BOOTSTRAP_ADMIN_ENABLED=true\n" + change, encoding="utf-8")
    with pytest.raises(env.PreflightError):
        env.demo_settings(tmp_path)


@pytest.fixture
def script():
    path = Path(__file__).resolve().parents[1] / "scripts/run_courseinsight_http.py"
    spec = importlib.util.spec_from_file_location("courseinsight_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_default_preflight_failure_never_launches_or_executes(script, monkeypatch, tmp_path):
    def blocked(root):
        raise env.PreflightError("INSPECTION_FAILED")
    def never(*args, **kwargs):
        pytest.fail("Blocked environment cannot launch or execute")
    monkeypatch.setattr(script, "preflight", blocked)
    monkeypatch.setattr(script, "launch", never)
    monkeypatch.setattr(script, "execute", never)
    with pytest.raises(env.PreflightError, match="INSPECTION_FAILED"):
        await script.live(tmp_path, script.ReportStore(tmp_path / "runs"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["health", "drift", "report", "cancel"])
async def test_live_failure_stops_owned_process_and_never_replays(script, monkeypatch,
                                                               tmp_path, failure):
    import asyncio

    calls, stopped, inspections = [], [], []
    def preflight(root):
        inspections.append(1)
        return {"code": "OK", "database": "changed" if failure == "drift"
                and len(inspections) == 2 else "courseinsight_demo"}, {
                    "BOOTSTRAP_ADMIN_USERNAME": "synthetic", "BOOTSTRAP_ADMIN_PASSWORD": "secret"}
    async def health(process):
        if failure == "health":
            raise env.PreflightError("JAVA_HEALTH_TIMEOUT")
        return 1
    async def execute(*args, **kwargs):
        calls.append("one_execution")
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise OSError("private details must not be published")
    monkeypatch.setattr(script, "preflight", preflight)
    monkeypatch.setattr(script, "launch", lambda *args: object())
    monkeypatch.setattr(script, "stop", lambda process: stopped.append(1))
    monkeypatch.setattr(script, "wait_health", health)
    monkeypatch.setattr(script, "execute", execute)
    error = (asyncio.CancelledError if failure == "cancel" else OSError if failure == "report"
             else env.PreflightError)
    with pytest.raises(error):
        await script.live(tmp_path, script.ReportStore(tmp_path / "runs"))
    assert stopped == [1]
    assert calls == (["one_execution"] if failure in {"report", "cancel"} else [])
    raw = next((tmp_path / "runs").glob("courseinsight-*/isolation.json")).read_text()
    assert "secret" not in raw and "private details" not in raw


def test_launch_cannot_inherit_model_credentials_or_spring_overrides(script, monkeypatch, tmp_path):
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def bind(self, address):
            assert address == ("127.0.0.1", 18080)
    captured = {}
    def popen(args, **kwargs):
        captured.update(kwargs)
        captured["args"] = args
        return object()
    monkeypatch.setattr(script.socket, "socket", Socket)
    monkeypatch.setattr(script.subprocess, "Popen", popen)
    monkeypatch.setenv("SPRING_APPLICATION_JSON", "unsafe override")
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "unsafe override")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    script.launch(tmp_path, {"MYSQL_DATABASE": "courseinsight_demo"})
    assert all(key not in captured["env"] for key in
               ["SPRING_APPLICATION_JSON", "JAVA_TOOL_OPTIONS", "OPENAI_API_KEY"])
    args = " ".join(captured["args"])
    assert "--server.address=127.0.0.1" in args and "--server.port=18080" in args
    assert "courseinsight_demo" in args and "-o" in captured["args"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response,code", [
    ("healthy", None), ("oversize", "JAVA_HEALTH_BODY_LIMIT"),
    ("redirect", "JAVA_HEALTH_REDIRECT_BLOCKED"), ("list", "JAVA_HEALTH_INVALID_RESPONSE"),
])
async def test_readiness_is_bounded_and_never_follows_redirects(
    script, monkeypatch, response, code,
):
    import httpx

    calls = []
    original = httpx.AsyncClient
    def handler(request):
        calls.append(request)
        assert request.url == "http://127.0.0.1:18080/actuator/health"
        if response == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        if response == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.com"})
        return httpx.Response(200, json=[] if response == "list" else {"status": "UP"})
    class Process:
        def poll(self):
            return None
    monkeypatch.setattr(script.httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    if code:
        with pytest.raises(env.PreflightError, match=f"^{code}$"):
            await script.wait_health(Process())
    else:
        assert await script.wait_health(Process()) == 1
    assert len(calls) == 1
