"""Fail-closed, read-only checks for the operator's existing local demo infrastructure."""

import json
import subprocess
from pathlib import Path

PROJECT = "courseinsight-demo"
PORTS = {"mysql": {"3306/tcp": "3307"}, "redis": {"6379/tcp": "6380"},
         "rocketmq-nameserver": {"9876/tcp": "9876"},
         "rocketmq-broker": {"10911/tcp": "10911", "10909/tcp": "10909", "5557/tcp": "5557"},
         "ai-service": {"8000/tcp": "8002"}}
VOLUMES = {"mysql": {"mysql_data": "/var/lib/mysql"}, "redis": {"redis_data": "/data"},
           "rocketmq-nameserver": {"rocketmq_nameserver_logs": "/home/rocketmq/logs"},
           "rocketmq-broker": {"rocketmq_broker_logs": "/home/rocketmq/logs",
                               "rocketmq_broker_store": "/home/rocketmq/store"}, "ai-service": {}}
SETTING_KEYS = {"MYSQL_DATABASE", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD",
                "REDIS_PORT", "ROCKETMQ_NAMESERVER_PORT", "ROCKETMQ_BROKER_PORT",
                "ROCKETMQ_BROKER_FAST_PORT", "ROCKETMQ_METRICS_PORT", "AI_STUB_PORT",
                "BOOTSTRAP_ADMIN_ENABLED", "BOOTSTRAP_ADMIN_USERNAME", "BOOTSTRAP_ADMIN_PASSWORD"}


class PreflightError(ValueError):
    """Only a fixed code may escape, never subprocess output or configuration values."""


def command(arguments: list[str]) -> str:
    """Run operator-owned read-only inspection commands, never model-authored commands."""
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PreflightError("INSPECTION_UNAVAILABLE") from None
    if result.returncode or len(result.stdout) > 10_000_000:
        raise PreflightError("INSPECTION_FAILED")
    return result.stdout.decode("utf-8", errors="strict").strip()


def demo_settings(root: Path) -> dict[str, str]:
    """Load only approved properties in memory. No interpolation, eval, or logging."""
    raw = (root / "demo.env").read_bytes()
    if len(raw) > 65536:
        raise PreflightError("DEMO_SETTINGS_INVALID")
    settings = {}
    for line in raw.decode("utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key.strip() in SETTING_KEYS:
            key, value = key.strip(), value.strip()
            if key in settings or not value or any(c in value for c in "\r\n\x00${}"):
                raise PreflightError("DEMO_SETTINGS_INVALID")
            settings[key] = value
    required = {"MYSQL_DATABASE": "courseinsight_demo", "MYSQL_PORT": "3307",
                "BOOTSTRAP_ADMIN_ENABLED": "true"}
    if any(settings.get(key) != value for key, value in required.items()):
        raise PreflightError("DEMO_SETTINGS_SCOPE")
    if any(not settings.get(key) for key in ["MYSQL_USER", "MYSQL_PASSWORD",
                                           "BOOTSTRAP_ADMIN_USERNAME", "BOOTSTRAP_ADMIN_PASSWORD"]):
        raise PreflightError("DEMO_CREDENTIALS_MISSING")
    return settings


def validate_docker(containers: list[dict], volumes: list[dict], settings: dict) -> list[dict]:
    """Require healthy demo-only volumes and loopback ports, including no foreign sharing."""
    expected_volumes = {f"{PROJECT}_{name}" for names in VOLUMES.values() for name in names}
    volume_map = {v["Name"]: v for v in volumes}
    if set(volume_map) != expected_volumes or any(
        (v.get("Labels") or {}).get("com.docker.compose.project") != PROJECT for v in volumes
    ):
        raise PreflightError("DEMO_VOLUME_SCOPE")
    evidence = []
    for service, ports in PORTS.items():
        name = "ai-stub" if service == "ai-service" else service
        matches = [c for c in containers if c["Name"] == f"/{PROJECT}-{name}"]
        if len(matches) != 1:
            raise PreflightError("DEMO_CONTAINER_MISSING")
        container = matches[0]
        labels = container["Config"].get("Labels") or {}
        if (labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.service") != service):
            raise PreflightError("DEMO_CONTAINER_SCOPE")
        state = container["State"]
        if not state.get("Running") or state.get("Health", {}).get("Status") != "healthy":
            raise PreflightError("DEMO_CONTAINER_UNHEALTHY")
        actual_ports = container["NetworkSettings"].get("Ports", {})
        published = {key: value for key, value in actual_ports.items() if value}
        if set(published) != set(ports) or any(
            published[key] != [{"HostIp": "127.0.0.1", "HostPort": port}]
            for key, port in ports.items()
        ):
            raise PreflightError("DEMO_PORT_SCOPE")
        mounts = container.get("Mounts", [])
        actual = {m["Name"]: m["Destination"] for m in mounts if m["Type"] == "volume"}
        expected = {f"{PROJECT}_{name}": dest for name, dest in VOLUMES[service].items()}
        if actual != expected:
            raise PreflightError("DEMO_VOLUME_SCOPE")
        nonvolumes = [m for m in mounts if m["Type"] != "volume"]
        if nonvolumes and not (service == "rocketmq-broker" and len(nonvolumes) == 1
                              and nonvolumes[0]["Type"] == "bind"
                              and nonvolumes[0].get("RW") is False
                              and nonvolumes[0]["Destination"].endswith("/conf/broker.conf")):
            raise PreflightError("DEMO_MOUNT_SCOPE")
        if service == "mysql":
            mysql_env = dict(item.split("=", 1) for item in container["Config"].get("Env", [])
                             if "=" in item)
            if any(mysql_env.get(key) != settings[key] for key in
                   ["MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"]):
                raise PreflightError("DEMO_DATABASE_SCOPE")
        evidence.append({"service": service, "container_id": container["Id"],
                         "health": "healthy", "volumes": sorted(expected), "ports": ports})
    approved_ids = {item["container_id"] for item in evidence}
    for container in containers:
        if any(m.get("Name") in expected_volumes for m in container.get("Mounts", [])):
            labels = container["Config"].get("Labels") or {}
            is_init = (container["Name"] == f"/{PROJECT}-rocketmq-volume-init"
                       and labels.get("com.docker.compose.project") == PROJECT
                       and labels.get("com.docker.compose.service") == "rocketmq-volume-init"
                       and not container["State"].get("Running"))
            if container["Id"] not in approved_ids and not is_init:
                raise PreflightError("DEMO_VOLUME_SHARED")
    return evidence


def preflight(root: Path) -> tuple[dict, dict[str, str]]:
    """Inspect existing infrastructure only. Never start/rebuild/delete containers or volumes."""
    from agenttest.courseinsight import FIXTURES

    review = json.loads((FIXTURES / "source-review.json").read_bytes())
    commit = command(["git", "-C", str(root), "rev-parse", "HEAD"])
    if commit != review["commit"]:
        raise PreflightError("SOURCE_VERSION_UNREVIEWED")
    # Refuse tracked or untracked source/config drift; unrelated .gitignore edits are preserved.
    if command(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "--",
                "courseinsight-server", "compose.yaml", "compose.demo.yaml", "demo.env",
                "scripts", "deploy/rocketmq"]):
        raise PreflightError("SOURCE_WORKTREE_DRIFT")
    import hashlib

    for relative, digest in review["files"].items():
        raw = (root / relative).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise PreflightError("SOURCE_FILE_DRIFT")
    settings = demo_settings(root)
    server = command(["docker", "info", "--format", "{{.ServerVersion}}"])
    ids = command(["docker", "ps", "-aq"]).splitlines()
    if not ids or any(len(value) != 12 or not all(c in "0123456789abcdef" for c in value)
                      for value in ids):
        raise PreflightError("DEMO_CONTAINER_MISSING")
    containers = json.loads(command(["docker", "inspect", *ids]))
    names = sorted({f"{PROJECT}_{name}" for names in VOLUMES.values() for name in names})
    volumes = json.loads(command(["docker", "volume", "inspect", *names]))
    evidence = validate_docker(containers, volumes, settings)
    selected = {item["container_id"] for item in evidence}
    for container in containers:
        if container["Id"] not in selected:
            continue
        labels = container["Config"].get("Labels") or {}
        check_compose_source(labels, root)
    return {"code": "ISOLATION_PREFLIGHT_PASSED", "source_commit": commit,
            "docker_version": server, "database": "courseinsight_demo",
            "containers": evidence, "origin": "http://127.0.0.1:18080"}, settings


def check_compose_source(labels: dict, root: Path) -> None:
    """A stale source path is not sufficient evidence of the approved startup configuration."""
    working = labels.get("com.docker.compose.project.working_dir", "")
    config = labels.get("com.docker.compose.project.config_files", "")
    if (Path(working).resolve() != root.resolve()
            or Path(config).resolve() != (root / "compose.demo.yaml").resolve()):
        raise PreflightError("DEMO_COMPOSE_SOURCE_MISMATCH")
