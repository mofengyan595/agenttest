"""One-shot acceptance claims survive failures; no real model or Java process."""

import importlib.util
import json
from pathlib import Path

import pytest

from agenttest.llm_planner import ModelSettings

pytest.importorskip("langgraph")


@pytest.fixture
def script(monkeypatch):
    directory = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("courseinsight_ai", directory /
                                                 "run_courseinsight_ai.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["health", "model", "report", "cancel"])
async def test_claim_blocks_replay_and_stops_owned_process(script, monkeypatch, tmp_path, failure):
    import asyncio

    fixture = tmp_path / "fixture.json"
    script.prepare(fixture, script.DEFAULT_TASK)
    calls, stopped = [], []
    monkeypatch.setattr(script, "_git", lambda root, *args: "" if args[0] == "status"
                        else "synthetic-commit")
    monkeypatch.setattr(script, "preflight", lambda root: ({"code": "OK"}, {
        "BOOTSTRAP_ADMIN_USERNAME": "synthetic", "BOOTSTRAP_ADMIN_PASSWORD": "private"}))
    monkeypatch.setattr(script.ModelSettings, "from_env_file", lambda path: ModelSettings(
        model="ecnu-plus", base_url="https://chat.ecnu.edu.cn/open/api/v1",
        api_key="synthetic-private-model-key", protocol="chatecnu"))
    monkeypatch.setattr(script, "launch", lambda *args: object())
    monkeypatch.setattr(script, "stop", lambda process: stopped.append(1))

    async def health(process):
        if failure == "health":
            raise OSError("private health failure")
        return 1

    async def execute(*args, **kwargs):
        calls.append(1)
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise OSError("private model or report failure")

    monkeypatch.setattr(script, "wait_health", health)
    monkeypatch.setattr(script, "run", execute)
    store = script.ReportStore(tmp_path / "runs")
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else OSError):
        await script.live(tmp_path, fixture, tmp_path / ".env", store)
    with pytest.raises(FileExistsError):
        await script.live(tmp_path, fixture, tmp_path / ".env", store)
    assert len(calls) <= 1 and stopped == [1]
    audit = next(store.root.glob("courseinsight-ai-*/audit.json")).read_text()
    assert "private" not in audit
    assert json.loads(audit)["state"] == "CLAIMED_NO_RESUME"


def test_fixture_is_exclusive_and_validated(script, tmp_path):
    path = tmp_path / "fixture.json"
    first = script.prepare(path, script.DEFAULT_TASK)
    assert script.load_fixture(path) == first
    with pytest.raises(FileExistsError):
        script.prepare(path, script.DEFAULT_TASK)
    for key, value in [("course_code", "unapproved"), ("course_code", None),
                       ("course_code", 1), ("version", True), ("invocation_id", None)]:
        invalid = {**first, key: value}
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            script.load_fixture(path)
