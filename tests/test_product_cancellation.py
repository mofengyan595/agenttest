import asyncio
from uuid import uuid4

import httpx
import pytest
from test_product_design import case, project
from test_product_tasks import response, service

from agenttest.project_intake import IntakeInput


@pytest.mark.parametrize("state,message", [("CANCELLED", "已取消"),
    ("INTERRUPTED", "执行中断"), ("FINISHED", "前序步骤失败")])
def test_planned_unsent_operation_reports_actual_stop_instead_of_absent_plan(state, message):
    from agenttest.product_tasks import ProductTasks

    summary = dict(inventory=dict(total=2, operations=[dict(operation_id=op, state="READY",
        reason="可执行") for op in ("create", "delete")]),
        groups=[dict(primary=["create", "delete"], status="DESIGNED")],
        cases=[dict(status=state, planned_operations=["create", "delete"], planned_steps=2,
            executed_steps=1, executed_operations=["create"], http_requests=1)])
    ProductTasks.coverage(summary)
    assert len(summary["untested_operations"]) == 1
    assert message in summary["untested_operations"][0]["reason"]
    assert summary["coverage"]["completed_scenarios"] == 0


@pytest.mark.asyncio
async def test_cancel_after_write_preserves_partial_evidence_and_never_replays_on_restart(
        tmp_path, monkeypatch):
    entered_read = asyncio.Event()
    received = []
    paid = []
    design = case()
    design["plan"]["steps"].append(dict(id="read", operation_id="readJob",
        policy_id="web_rule_2", role=None, arguments=[dict(location="path_parameters",
            name="jobId", value=None, from_step="insert", select="$.id")]))

    def model(request):
        paid.append(1)
        return response(dict(interpretation="新建并读取任务", cases=[design],
                             questions=[], not_covered=[]))

    async def target(request):
        received.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json=dict(id="fresh-job"))
        entered_read.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="取消隔离任务", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        submission = uuid4().hex
        row = await svc.submit("operator", saved["id"], "创建后读取", submission)
        await asyncio.wait_for(entered_read.wait(), timeout=5)
        cancelled = await svc.cancel(row["id"], "operator")
        assert cancelled["status"] == "CANCELLED"
        summary = cancelled["summary"]
        assert not summary["complete"] and summary["sent_requests_may_have_completed"]
        current = summary["cases"][0]
        assert current["status"] == "CANCELLED" and current["run_id"]
        assert current["executed_operations"] == ["insertJob", "readJob"]
        assert current["evidence"][0]["checks"][0]["actual"] == 201
        assert summary["coverage"]["executed_operations"] == 2
        assert summary["coverage"]["started_scenarios"] == 1
        assert summary["coverage"]["completed_scenarios"] == 0
        assert received == ["POST", "GET"] and len(paid) == 1
        assert not svc.db.model_usage_blocked()
        await svc.close()
        svc = service(tmp_path, model)
        duplicate = await svc.submit("operator", saved["id"], "创建后读取", submission)
        assert duplicate["id"] == row["id"] and duplicate["status"] == "CANCELLED"
        assert not svc._workers and received == ["POST", "GET"] and len(paid) == 1
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_cancel_before_worker_starts_keeps_inventory_and_sends_nothing(tmp_path):
    svc = service(tmp_path, lambda request: pytest.fail("Cancelled task called a model"))
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="排队取消", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "创建后读取", uuid4().hex)
        stopped = await svc.cancel(row["id"], "operator")
        assert stopped["status"] == "CANCELLED"
        assert stopped["summary"]["mode"] == "MODEL_DESIGNED"
        assert stopped["summary"]["inventory"]["total"] == 2
        assert stopped["summary"]["context_usage"]["model_calls"] == 0
        assert stopped["summary"]["context_usage"]["requests"] == 0
        assert stopped["summary"]["coverage"]["executed_operations"] == 0
    finally:
        await svc.close()
