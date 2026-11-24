"""Runtime clocks do not participate in report identity."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from test_p3b26_focus_completeness import release_context, wire_with_checks
from test_product_design import settings

from agenttest import llm_planner, model_timing
from agenttest.model_timing import ModelCallTelemetry
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.report import ModelCallStats, ReportStore, RunReport, fingerprint


def report_with_call(telemetry):
    return RunReport(run_id="a" * 32, started_at="2026-11-24T00:00:00+00:00",
                     scenario_sha256="", contract_sha256="", environment="test",
                     target_origin="https://example.test",
                     model_call=ModelCallStats(calls=1, telemetry=telemetry))


@pytest.mark.parametrize("has_telemetry", [True, False], ids=["telemetry", "legacy"])
def test_report_json_and_store_roundtrip(tmp_path, has_telemetry):
    telemetry = ModelCallTelemetry(stages_ms={"request_prepared": 0.0,
                                              "call_ended": 30.5},
                                   interruption_source="HTTPX_READ",
                                   usage_evidence="INCOMPLETE_RESPONSE") if has_telemetry else None
    report = report_with_call(telemetry)
    store = ReportStore(tmp_path)
    saved = store.save(report)
    payload = json.loads(saved.read_bytes())
    reread = RunReport.model_validate_json(saved.read_bytes())
    assert ("telemetry" in payload["model_call"]) is has_telemetry
    assert "_started" not in payload.get("model_call", {}).get("telemetry", {})
    assert reread == store.read(report.run_id) == report
    assert reread.model_dump_json() == report.model_dump_json()
    assert fingerprint(reread.model_dump(mode="json")) == fingerprint(payload)
    assert reread.model_call.total_tokens is None


def test_different_clock_origins_yield_equal_reports(monkeypatch):
    readings = iter((12.25, 82.25))
    monkeypatch.setattr(model_timing, "time", SimpleNamespace(monotonic=readings.__next__))
    first, second = ModelCallTelemetry(), ModelCallTelemetry()
    first.mark("headers_received", started=12.0)
    second.mark("headers_received", started=82.0)
    first_report, second_report = report_with_call(first), report_with_call(second)
    assert first_report.model_call.telemetry.stages_ms == {"headers_received": 250.0}
    assert second_report == RunReport.model_validate_json(first_report.model_dump_json())


@pytest.mark.parametrize("change", [
    {"stages_ms": {"call_ended": 11.0}},
    {"interruption_source": "REQUEST_DEADLINE"},
    {"usage_evidence": "USAGE_MISSING"},
], ids=["stages", "interruption", "usage"])
def test_different_observed_telemetry_changes_report_identity(change):
    original = report_with_call(ModelCallTelemetry(stages_ms={"call_ended": 10.0}))
    payload = json.loads(original.model_dump_json())
    payload["model_call"]["telemetry"].update(change)
    changed = RunReport.model_validate_json(json.dumps(payload))
    assert original != changed
    assert fingerprint(original.model_dump(mode="json")) != fingerprint(
        changed.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_overlapping_planner_calls_keep_independent_timing(monkeypatch):
    clock = SimpleNamespace(now=100.0)

    def now():
        return clock.now

    wall_clock = llm_planner.time.time
    monkeypatch.setattr(llm_planner, "time", SimpleNamespace(monotonic=now, time=wall_clock))
    monkeypatch.setattr(model_timing, "time", SimpleNamespace(monotonic=now))
    entered, release = asyncio.Event(), asyncio.Event()
    context, source = release_context()
    payload = json.dumps(wire_with_checks(source, "check_1", "check_2"))
    requests = []

    async def handler(request):
        requests.append(request)
        number = len(requests)
        if number == 1:
            entered.set()
            await release.wait()
        return httpx.Response(200, headers={"x-request-id": f"mock-call-{number}"},
                              json={"usage": {"prompt_tokens": number, "completion_tokens": 2,
                                              "total_tokens": number + 2},
                                    "choices": [{"finish_reason": "stop",
                                                 "message": {"content": payload}}]})

    planner = FocusModel(settings(), profile="generic", transport=httpx.MockTransport(handler))

    async def run_call():
        await planner.generate(context)
        return planner.stats

    async with asyncio.TaskGroup() as group:
        first = group.create_task(run_call())
        await asyncio.wait_for(entered.wait(), timeout=5)
        clock.now = 105.0
        second = await run_call()
        clock.now = 108.0
        release.set()

    first = first.result()
    assert len(requests) == first.calls + second.calls == 2
    assert (first.provider_request_id, first.total_tokens) == ("mock-call-1", 3)
    assert (second.provider_request_id, second.total_tokens) == ("mock-call-2", 4)
    assert first.telemetry.stages_ms["headers_received"] == 8000.0
    assert first.telemetry.stages_ms["call_ended"] == 8000.0
    assert second.telemetry.stages_ms["headers_received"] == 0.0
    assert second.telemetry.stages_ms["call_ended"] == 0.0
