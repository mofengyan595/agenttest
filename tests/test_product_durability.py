"""Exercise the locked Deep Agents graph and SQLite through fresh processes."""

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from test_product_agent import scripted, session, tool_turn
from test_product_design import settings

from agenttest.product_agent import run_agent
from agenttest.product_durability import GATEWAY_STOP, PAUSED, ProductDurability
from agenttest.report import ReportStore, fingerprint
from agenttest.task_service import TaskService
from agenttest.task_store import TaskConflict, TaskStore

OWNER = "operator"


def run_phase(root: Path, phase: str):
    async def invoke():
        fake, tasks, project, _, summary = session(root)
        svc, scope = fake.svc, fake.scope
        svc.db = TaskStore(root / "tasks.sqlite3")
        svc.project = lambda _identifier: project
        svc.private_input_guard = lambda _value: False
        svc.environ["AGENTTEST_DURABLE_TARGET_EPOCH"] = "isolated-instance-1"
        if phase == "start":
            svc.environ["AGENTTEST_DURABLE_PAUSE_AFTER_TOOLS"] = "1"
            row, _ = svc.db.create(scope.task_id, OWNER, "submission", "parcels",
                                   project.identity(), "test", "RUNNING", summary)
            model, _ = scripted(tool_turn("run_test_group", "one", group_id="group_1"))
        else:
            svc.db.interrupt_unfinished()
            row = svc.db.get(scope.task_id, OWNER)
            scope.recovery = await ProductDurability(svc, row, OWNER).load()
            row = svc.db.transition(scope.task_id, OWNER, "INTERRUPTED", "RUNNING")
            scope.summary = row["summary"]
            model, _ = scripted(tool_turn("run_test_group", "two", group_id="group_1"),
                                ("已读取已有证据。", {}))
        scope.durability = ProductDurability(svc, row, OWNER)
        config = settings()
        result = await run_agent(scope, tasks, config, model=model)
        if phase == "start":
            assert result.stop_reason == PAUSED, result.error
            assert result.checkpoint_id
            scope.durability.publish("AGENT", summary=scope.summary,
                checkpoint_id=result.checkpoint_id, state=dict(usage=scope.ledger.summary(),
                    session=result.session.durable_state(), agent_model_totals=result.model_totals,
                    agent_call_trace=result.call_trace))
        print(json.dumps(dict(reason=result.stop_reason, calls=len(tasks.calls),
                               cases=len(scope.summary["cases"]), error=result.error)))

    asyncio.run(invoke())


@pytest.mark.skipif(importlib.util.find_spec("deepagents") is None,
                    reason="Deep Agents extra is not installed")
def test_native_deep_agents_checkpoint_survives_process_exit(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    args = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), str(tmp_path)]
    first = subprocess.run([*args, "start"], capture_output=True, text=True,
                           encoding="utf-8", timeout=60, check=False)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout.strip().splitlines()[-1])["calls"] == 1
    assert (tmp_path / "tasks.checkpoints.sqlite3").stat().st_size > 0
    second = subprocess.run([*args, "resume"], capture_output=True, text=True,
                            encoding="utf-8", timeout=60, check=False)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1]) == dict(
        reason="ANSWER", calls=0, cases=1, error=None)
    (tmp_path / "tasks.checkpoints.sqlite3").write_bytes(b"invalid sqlite database")
    corrupt = subprocess.run([*args, "resume"], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, check=False)
    assert corrupt.returncode != 0
    assert "DURABLE_CHECKPOINT_INVALID" in corrupt.stderr


@pytest.mark.asyncio
async def test_unconfirmed_execution_epoch_change_and_incompatible_version_fail_closed(tmp_path):
    fake, _, project, _, summary = session(tmp_path)
    svc, task_id = fake.svc, fake.scope.task_id
    svc.db = TaskStore(tmp_path / "tasks.sqlite3")
    svc.project = lambda _identifier: project
    svc.private_input_guard = lambda _value: False
    svc.environ["AGENTTEST_DURABLE_TARGET_EPOCH"] = "instance-1"
    row, _ = svc.db.create(task_id, OWNER, "submission", "project", project.identity(),
                           "test", "RUNNING", summary)
    durable = ProductDurability(svc, row, OWNER)
    durable.publish("ACTIVE", summary=summary)
    with pytest.raises(TaskConflict, match="DURABLE_EXECUTION_UNCONFIRMED"):
        await durable.load()
    svc.environ["AGENTTEST_DURABLE_TARGET_EPOCH"] = "instance-2"
    with pytest.raises(TaskConflict, match="PROJECT_VERSION_CHANGED"):
        await ProductDurability(svc, row, OWNER).load()
    svc.db.save_durable_state(task_id, OWNER, {"version": "future"})
    with pytest.raises(TaskConflict, match="DURABLE_CHECKPOINT_VERSION_MISMATCH"):
        await durable.load()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_gateway_pause_after_tool_preserves_evidence_and_dispatch_accounting(
        tmp_path, status):
    pytest.importorskip("deepagents")
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    fake, tasks, project, _, summary = session(tmp_path)
    svc, scope = fake.svc, fake.scope
    svc.db = TaskStore(tmp_path / "tasks.sqlite3")
    svc.project = lambda _identifier: project
    svc.private_input_guard = lambda _value: False
    svc.environ["AGENTTEST_DURABLE_TARGET_EPOCH"] = "isolated-instance-1"
    row, _ = svc.db.create(scope.task_id, OWNER, "submission", "parcels",
                           project.identity(), "test", "RUNNING", summary)
    scope.durability = ProductDurability(svc, row, OWNER)
    statuses = []

    def gateway(request):
        body = json.loads(request.content)
        if not statuses:
            statuses.append(200)
            return httpx.Response(200, json=dict(model="unit-model",
                usage=dict(prompt_tokens=200, completion_tokens=150, total_tokens=350),
                choices=[dict(finish_reason="tool_calls", message=dict(content="", tool_calls=[
                    dict(id="call_1", type="function", function=dict(name="run_test_group",
                        arguments=json.dumps(dict(group_id="group_1"))))
                ]))]))
        assert any(message["role"] == "tool" for message in body["messages"])
        statuses.append(status)
        return httpx.Response(status, json={"error": {"code": "model_concurrency_exceeded"}})

    result = await run_agent(scope, tasks, settings(), transport=httpx.MockTransport(gateway))
    assert result.stop_reason == GATEWAY_STOP, result.error
    assert result.checkpoint_id
    assert statuses == [200, status, status, status]
    assert scope.ledger.model_calls == 4
    assert scope.ledger.known_tokens == 350 and not scope.ledger.usage_unknown
    assert len(summary["cases"]) == len(tasks.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_before_admission", [False, True])
async def test_cancel_and_competing_resume_use_the_same_task_claim(
        tmp_path, monkeypatch, cancel_before_admission):
    _, _, project, _, _ = session(tmp_path)
    db = TaskStore(tmp_path / "tasks.sqlite3")
    svc = TaskService({}, db, ReportStore(tmp_path / "runs"), lambda _: None,
        environ=dict(AGENTTEST_DURABLE_EXECUTION="1",
                     AGENTTEST_DURABLE_TARGET_EPOCH="instance"))
    monkeypatch.setattr(svc, "project", lambda _key: project)
    queued = []
    monkeypatch.setattr(svc, "_enqueue", lambda task, owner: queued.append((task, owner)))
    text = "恢复当前任务"
    summary = dict(budget=dict(max_requests=5, max_model_calls=8, max_total_tokens=10000))
    row, _ = db.create("task", OWNER, "submission", "project", project.identity(),
                       fingerprint(text), "RUNNING", summary)
    db.save_context("task", OWNER, dict(text=text))
    ProductDurability(svc, row, OWNER).publish("FOCUS", summary=summary, state=dict(
        usage=dict(requests=0, model_calls=1, known_tokens=0, usage_known=True)))
    db.transition("task", OWNER, "RUNNING", "INTERRUPTED")
    old_load = ProductDurability.load
    reached, release = asyncio.Event(), asyncio.Event()

    async def deferred_load(self):
        state = await old_load(self)
        reached.set()
        await release.wait()
        return state

    monkeypatch.setattr(ProductDurability, "load", deferred_load)
    try:
        first = asyncio.create_task(svc.resume("task", OWNER))
        await reached.wait()
        if cancel_before_admission:
            assert (await svc.cancel("task", OWNER))["status"] == "CANCELLED"
            release.set()
            assert isinstance((await asyncio.gather(first, return_exceptions=True))[0],
                              TaskConflict)
            assert not queued
        else:
            second = asyncio.create_task(svc.resume("task", OWNER))
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
            assert sum(isinstance(item, TaskConflict) for item in results) == 1
            assert queued == [("task", OWNER)]
    finally:
        await svc.close()


if __name__ == "__main__":
    run_phase(Path(sys.argv[1]), sys.argv[2])
