"""Transport evidence follows the existing call, usage and cancellation decisions."""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from test_p3b26_focus_completeness import release_context, wire_with_checks
from test_product_design import project, settings
from test_product_tasks import finished, service

from agenttest.llm_planner import PlannerFailure
from agenttest.model_timing import model_deadline
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.product_tasks import JournalPlanner
from agenttest.project_intake import IntakeInput
from agenttest.report import ModelCallStats
from agenttest.task_store import TaskStore

STAGES = ["request_prepared", "dispatch_started", "headers_received", "first_body_chunk",
          "body_complete", "json_parsed", "usage_validated", "call_ended"]


class BrokenBody(httpx.AsyncByteStream):
    def __init__(self, partial):
        self.partial = partial

    async def __aiter__(self):
        if self.partial:
            yield b"x" * 16384
        raise httpx.ReadTimeout("synthetic read failure")


def wire_response(content, **usage):
    data = {"choices": [dict(finish_reason="stop", message=dict(content=content))], **usage}
    return httpx.Response(200, json=data, headers={"x-request-id": "mock-request"})


def response_case(case, content):
    if case == "connect":
        raise httpx.ConnectTimeout("synthetic connect failure")
    if case == "read-before-headers":
        raise httpx.ReadTimeout("synthetic read failure")
    if case in ("headers-only", "partial"):
        return httpx.Response(200, stream=BrokenBody(case == "partial"),
                              headers={"x-request-id": "mock-request"})
    if case == "bad-json":
        return httpx.Response(200, content=b"{")
    if case == "missing":
        return wire_response(content)
    usage = dict(prompt_tokens=10, completion_tokens=4, total_tokens=14)
    if case == "invalid":
        usage["prompt_tokens"] = True
    elif case == "inconsistent":
        usage["total_tokens"] = 13
    return wire_response(content, usage=usage)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["planner", "agent"])
@pytest.mark.parametrize("case, expected_stages, origin, evidence", [
    ("ok", STAGES, None, "VALIDATED"),
    ("connect", [*STAGES[:2], STAGES[-1]], "HTTPX_CONNECT", "INCOMPLETE_RESPONSE"),
    ("read-before-headers", [*STAGES[:2], STAGES[-1]], "HTTPX_READ", "INCOMPLETE_RESPONSE"),
    ("headers-only", [*STAGES[:3], STAGES[-1]], "HTTPX_READ", "INCOMPLETE_RESPONSE"),
    ("partial", [*STAGES[:4], STAGES[-1]], "HTTPX_READ", "INCOMPLETE_RESPONSE"),
    ("bad-json", [*STAGES[:5], STAGES[-1]], None, "RESPONSE_PARSE_FAILED"),
    ("missing", [*STAGES[:6], STAGES[-1]], None, "USAGE_MISSING"),
    ("invalid", [*STAGES[:6], STAGES[-1]], None, "USAGE_INVALID"),
    ("inconsistent", [*STAGES[:6], STAGES[-1]], None, "USAGE_TOTAL_MISMATCH"),
])
async def test_model_transport_telemetry(adapter, case, expected_stages, origin, evidence):
    context, source = release_context()
    content = json.dumps(wire_with_checks(source, "check_1", "check_2"))
    requests = []

    def transport(request):
        requests.append(request.content)
        return response_case(case, content)

    transport = httpx.MockTransport(transport)
    if adapter == "agent":
        pytest.importorskip("langchain_core")
        from agenttest.deep_harness.chat_model import ModelHarnessError, build_chat_model

        model = build_chat_model(settings(), transport=transport)
        call = model.ainvoke("本地模拟")
        fails = origin or case == "bad-json"
        failure = ModelHarnessError
    else:
        model = FocusModel(settings(), transport=transport, profile="generic")
        call = model.generate(context)
        fails = case != "ok"
        failure = PlannerFailure
        before = model.prepare_request(context)

    if fails:
        with pytest.raises(failure):
            await call
    else:
        await call
    stats = model.stats
    telemetry = stats.telemetry
    assert len(requests) == stats.calls == 1
    assert b"stages_ms" not in requests[0]
    assert stats.total_tokens == (14 if case == "ok" else None)
    assert stats.usage_state == ("KNOWN" if case == "ok" else "UNKNOWN")
    assert list(telemetry.stages_ms) == expected_stages
    assert list(telemetry.stages_ms.values()) == sorted(telemetry.stages_ms.values())
    assert telemetry.interruption_source == origin and telemetry.usage_evidence == evidence
    if origin:
        assert stats.timeout_kind == origin.removeprefix("HTTPX_")
        assert stats.error_type == "MODEL_TIMEOUT"
    if case == "partial":
        assert (stats.http_status, stats.response_bytes, stats.provider_request_id) == (
            200, 16384, "mock-request")
    if adapter == "agent":
        assert model.call_trace[-1]["telemetry"] == telemetry.model_dump()
    else:
        assert requests == [before]


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [None, "TASK_DEADLINE", "FOCUS_DEADLINE", "DESIGN_DEADLINE"])
async def test_earliest_asyncio_deadline_is_recorded_without_changing_ledger(tmp_path, outer):
    async def wait_for_headers(_):
        await asyncio.sleep(10)
        pytest.fail("Deadline should interrupt the local mock")

    context, _ = release_context()
    db = TaskStore(tmp_path / "tasks.sqlite3")
    model = FocusModel(settings().model_copy(update={"timeout": 0.04 if outer is None else 1.0}),
                       transport=httpx.MockTransport(wait_for_headers), profile="generic")
    journal = JournalPlanner(model, db, "test-task", phase="focus")
    if outer:
        with pytest.raises(TimeoutError):
            async with model_deadline(0.04, outer):
                await journal.generate(context)
    else:
        with pytest.raises(PlannerFailure, match="MODEL_TIMEOUT"):
            async with model_deadline(1, "TASK_DEADLINE"):
                await journal.generate(context)
    stored = db.latest_model_call("test-task")
    assert stored == journal.stats.model_dump(mode="json")
    assert stored["telemetry"]["interruption_source"] == (outer or "REQUEST_DEADLINE")
    assert stored["timeout_kind"] == (None if outer else "DEADLINE")
    assert stored["calls"] == 1 and stored["http_status"] is None
    assert stored["usage_state"] == "UNKNOWN" and stored["total_tokens"] is None
    assert db.model_usage_blocked()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["USER_CANCELLED", "SERVICE_SHUTDOWN", "TASK_DEADLINE"])
async def test_product_interruption_reports_the_same_sqlite_call(tmp_path, monkeypatch, source):
    entered = asyncio.Event()
    calls = []

    async def focus(request):
        calls.append(request)
        entered.set()
        await asyncio.sleep(10)
        pytest.fail("Expected task cancellation")

    def deny_target(_):
        pytest.fail("The Focus request has not finished; no target call is authorized")

    if source == "TASK_DEADLINE":
        monkeypatch.setattr("agenttest.product_tasks.model_deadline",
                            lambda _, label: model_deadline(1, label))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(deny_target))
    svc = service(tmp_path, deny_target)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile="generic")
    try:
        proj, setup = project(negative=True)
        saved = svc.intake.save(IntakeInput(name="本地遥测", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "发布前请完整验收整个项目。", uuid4().hex)
        await asyncio.wait_for(entered.wait(), timeout=8)
        if source == "SERVICE_SHUTDOWN":
            await svc.close()
            result = svc.db.get(row["id"], "operator")
        elif source == "USER_CANCELLED":
            result = await svc.cancel(row["id"], "operator")
        else:
            result = await finished(svc, row)
        assert result["status"] == ("CANCELLED" if source == "USER_CANCELLED" else "INTERRUPTED")
        stored = svc.db.latest_model_call(row["id"])
        assert stored == result["summary"]["latest_model_call"]
        assert stored["telemetry"]["interruption_source"] == source
        assert len(calls) == stored["calls"] == 1 and stored["usage_state"] == "UNKNOWN"
        assert stored["total_tokens"] is None
        assert TaskStore(svc.db.path).model_usage_blocked()
    finally:
        if source != "SERVICE_SHUTDOWN":
            await svc.close()


def test_legacy_or_unfinished_calls_do_not_gain_invented_telemetry(tmp_path):
    db = TaskStore(tmp_path / "tasks.sqlite3")
    original = dict(local_call_id="legacy", calls=1, usage_state="UNKNOWN", timeout_kind="READ")
    stats = ModelCallStats.model_validate(original)
    assert stats.telemetry is None and "telemetry" not in stats.model_dump()
    # A PENDING row survived process exit; it does not prove dispatch or a specific cause.
    db.begin_model_call("legacy", "task", ModelCallStats(local_call_id="legacy"))
    restarted = TaskStore(db.path)
    saved = deepcopy(restarted.latest_model_call("task"))
    assert saved["usage_state"] == "PENDING" and "telemetry" not in saved
    assert saved["total_tokens"] is None and restarted.model_usage_blocked()
