"""Fresh source checkout setup is independent of runs, credentials and prior databases."""

import importlib
import json
from pathlib import Path

import pytest

from agenttest.report import ReportStore
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


@pytest.mark.asyncio
async def test_clean_lab_bootstrap_needs_no_model_or_http(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    prepare = importlib.import_module("prepare_colleague_workspace").prepare
    environment = {f"AGENTTEST_TARGET_LAB_{role}_{credential}": f"private-{role}-{credential}"
        for role in ("OWNER", "READER") for credential in ("USERNAME", "PASSWORD", "BEARER")}
    first = await prepare(tmp_path, 18420, environment)
    assert first["model_calls"] == first["target_requests"] == 0
    assert set(first["projects"]) == {"parcels", "reservations", "directory"}
    svc = TaskService({}, TaskStore(tmp_path / "workspace/tasks.sqlite3"),
        ReportStore(tmp_path / "workspace/reports"), lambda _: pytest.fail("No model"),
        generic_intake=True, environ=environment)
    try:
        projects = {kind: svc.project(key) for kind, key in first["projects"].items()}
        assert [len(projects[k].contract.operations) for k in first["projects"]] == [6, 5, 48]
        assert len(projects["parcels"].target.resource_lifecycles) == 1
        assert len(projects["reservations"].target.resource_lifecycles) == 1
        assert any(p.roles == ["reader"] and p.assertions.status.codes == [403]
                   for p in projects["reservations"].registry.definitions())
        assert not svc.db.list("lab_operator")
        assert "private-OWNER" not in json.dumps(svc.intake.records())
    finally:
        await svc.close()
    assert await prepare(tmp_path, 18420, environment) == first
    with pytest.raises(ValueError, match="EXISTING_LAB_PORT_CHANGED"):
        await prepare(tmp_path, 18430, environment)
