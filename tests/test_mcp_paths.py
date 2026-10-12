"""MCP namespace compatibility must not canonicalize away pre-planted links."""

import ctypes
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from agenttest.executor import Executor
from agenttest.mcp_service import MCPService


def short_windows_path(path):
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    if not function(str(path), buffer, len(buffer)):
        raise ctypes.WinError(ctypes.get_last_error())
    alias = Path(buffer.value)
    if alias == path:
        pytest.skip("Filesystem does not provide distinct Windows 8.3 aliases")
    return alias


async def assert_once_and_readable(service, canonical_root, monkeypatch):
    run_id = uuid4().hex
    args = {"scenario_id": "mini_normal", "invocation_id": run_id}
    first = await service.call("run_scenario", args)
    assert first["ok"], first.get("error_code")
    report = first["report"]
    assert report["outcome"] == "PASS" and report["completed"] and report["revision"] > 1
    assert sum(s["operation_id"] == "createCourse" for s in report["steps"]) == 1
    assert (canonical_root / run_id / ".writer.lock").is_file()
    assert not list((canonical_root / run_id).glob("*.tmp"))

    async def forbidden(*args, **kwargs):
        pytest.fail("Cached MCP invocation must not replay Executor")

    monkeypatch.setattr(Executor, "run", forbidden)
    for instance in (service, MCPService(canonical_root)):
        assert (await instance.call("read_report", {"run_id": run_id}))["report"] == report
        repeated = await instance.call("run_scenario", args)
        assert repeated["ok"] and repeated["cached"] and not repeated["replayed"]
        assert repeated["report"] == report


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", [True, False])
async def test_regular_namespace_paths_remain_readable(tmp_path, monkeypatch, relative):
    monkeypatch.chdir(tmp_path)
    root = Path("long report namespace") if relative else tmp_path / "long report namespace"
    await assert_once_and_readable(
        MCPService(root), tmp_path / "long report namespace", monkeypatch)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 path aliases")
@pytest.mark.parametrize("existing_root", [True, False])
async def test_short_namespace_alias_does_not_reject_safe_directory(tmp_path, monkeypatch,
                                                                  existing_root):
    alias = short_windows_path(tmp_path)
    assert alias.resolve() == tmp_path.resolve()
    root = tmp_path / "reports"
    if existing_root:
        root.mkdir()
    service = MCPService(alias / "reports")
    assert service._store.root == alias / "reports"  # Keep ReportStore's unresolved trust root.
    await assert_once_and_readable(service, root, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["root", "ancestor", "run"])
async def test_namespace_links_rejected_before_claim_or_execution(tmp_path, monkeypatch, component):
    root, outside = tmp_path / "reports", tmp_path / "outside"
    outside.mkdir()
    run_id = uuid4().hex
    if component == "ancestor":
        link, root = tmp_path / "linked", tmp_path / "linked" / "reports"
    elif component == "run":
        root.mkdir()
        outside = root / "existing"  # Even a junction to a sibling inside root is forbidden.
        outside.mkdir()
        link = root / run_id
    else:
        link = root
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                              capture_output=True, check=False)
        if made.returncode:
            pytest.skip("Junction creation unavailable on this filesystem")
    else:
        link.symlink_to(outside, target_is_directory=True)

    async def forbidden(*args, **kwargs):
        pytest.fail("Linked MCP namespace must not execute")

    monkeypatch.setattr(Executor, "run", forbidden)
    service = MCPService(root)
    for name, args in (("run_scenario", {"scenario_id": "mini_normal", "invocation_id": run_id}),
                       ("read_report", {"run_id": run_id})):
        result = await service.call(name, args)
        assert result == {"ok": False, "error_code": "MCP_INTERNAL_ERROR"}
    assert service._runs == 0
    assert not list(outside.iterdir())
