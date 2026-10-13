import asyncio
import json
from pathlib import Path

import httpx
import pytest
from support import registry

from agenttest.contracts import ContractSummary, Operation
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig


def setup(tmp_path: Path, handler, method="GET", **config_overrides):
    contract = ContractSummary(version="3.1.0", operations=[Operation(
        method=method, path="/items", operation_id="items",
    )])
    config = TargetConfig(
        environment="isolated", allow_mutations=True, allowed_operations=["items"],
        min_request_interval=0.0, **config_overrides,
    )
    return Executor(config, contract, ReportStore(tmp_path),
                    httpx.MockTransport(handler), registry=registry())


def plan(**step_overrides) -> Scenario:
    return Scenario.model_validate({"name": "executor", "steps": [{
        "id": "read", "operation_id": "items", "policy_id": "items_v1", **step_overrides,
    }]})


@pytest.mark.asyncio
async def test_pass_and_audit_evidence(tmp_path: Path) -> None:
    executor = setup(tmp_path, lambda req: httpx.Response(200, json={"token": "private-token"}))
    report = await executor.run(plan())
    assert report.outcome == Outcome.PASS and report.completed
    assert report.steps[0].attempts[0].response_sha256
    assert report.steps[0].attempts[0].status_code == 200
    raw = (tmp_path / report.run_id / "report.json").read_text()
    assert "private-token" not in raw and "Authorization" not in raw


@pytest.mark.asyncio
async def test_no_redirect_followed(tmp_path: Path) -> None:
    calls = []

    def handler(req):
        calls.append(req.url)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/secret"})

    report = await setup(tmp_path, handler).run(plan())
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[0].reason == "REDIRECT_BLOCKED" and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method,expected_calls", [("GET", 3), ("POST", 1)])
async def test_retry_reads_never_writes(tmp_path: Path, method: str, expected_calls: int) -> None:
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("contains-secret-token", request=req)

    executor = setup(tmp_path, handler, method)
    report = await executor.run(plan(read_retries=2 if method == "GET" else 0))
    assert report.outcome == Outcome.TEST_INFRA_ERROR and len(calls) == expected_calls
    assert "contains-secret-token" not in executor.store.read(report.run_id).model_dump_json()


@pytest.mark.asyncio
async def test_request_budget_and_poll_exhaustion(tmp_path: Path) -> None:
    executor = setup(tmp_path, lambda req: httpx.Response(200, json={"state": "RUNNING"}),
                     max_requests=2)
    report = await executor.run(plan(poll=True))
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[0].reason == "REQUEST_LIMIT"
    assert len(report.steps[0].attempts) == 2


@pytest.mark.asyncio
async def test_response_limit_and_tool_error(tmp_path: Path) -> None:
    report = await setup(tmp_path, lambda req: httpx.Response(200, content=b"x" * 500),
                         max_body_bytes=100).run(plan())
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[0].reason == "RESPONSE_BODY_LIMIT"

    def broken(req):
        raise RuntimeError("password=secret")

    report = await setup(tmp_path, broken).run(plan())
    assert report.outcome == Outcome.TOOL_ERROR
    assert "password=secret" not in json.dumps(report.model_dump(), default=str)


@pytest.mark.asyncio
async def test_rejected_plan_sends_nothing(tmp_path: Path) -> None:
    def never(req):
        pytest.fail("Rejected plan must never send a request")

    report = await setup(tmp_path, never).run(plan(operation_id="missing"))
    assert report.outcome == Outcome.TOOL_ERROR and report.reason == "PLAN_REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_cancellation_journals_unknown_request_without_replay(tmp_path, monkeypatch, method):
    from agenttest.credentials import CredentialVault

    entered = asyncio.Event()
    calls, closed = [], []
    original_close = CredentialVault.close

    def close(vault):
        original_close(vault)
        closed.append(vault)

    monkeypatch.setattr(CredentialVault, "close", close)

    async def interrupted(request):
        calls.append(request)
        entered.set()
        await asyncio.Event().wait()

    executor = setup(tmp_path, interrupted, method)
    execution = asyncio.create_task(executor.run(plan(read_retries=2 if method == "GET" else 0)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
    report = executor.store.read(next(tmp_path.iterdir()).name)
    assert report.outcome == Outcome.TEST_INFRA_ERROR and report.completed
    assert report.reason == "RUN_CANCELLED"
    assert report.steps[0].reason == "ATTEMPT_OUTCOME_UNKNOWN"
    assert report.steps[0].attempts[0].error_code == "ATTEMPT_OUTCOME_UNKNOWN"
    assert len(calls) == len(report.steps[0].attempts) == 1
    assert closed and all(not v._tokens and not v._secrets and not v._environ for v in closed)
    with pytest.raises(ValueError, match="RUN_NOT_RESUMABLE"):
        await executor.run(plan(), report=report)


@pytest.mark.asyncio
async def test_cancellation_before_next_send_does_not_mark_it_as_attempted(tmp_path):
    entered, calls = asyncio.Event(), []

    class RecordingStore(ReportStore):
        def save(self, report):
            path = super().save(report)
            if len(report.steps) == 2:
                entered.set()
            return path

    executor = setup(tmp_path, lambda req: calls.append(req) or httpx.Response(200, json={}))
    executor.store = RecordingStore(tmp_path)
    executor.config.min_request_interval = 5.0
    scenario = plan()
    scenario.steps.append(scenario.steps[0].model_copy(update={"id": "second"}))
    execution = asyncio.create_task(executor.run(scenario))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
    report = executor.store.read(next(tmp_path.iterdir()).name)
    assert len(calls) == 1 and report.steps[0].outcome == Outcome.PASS
    assert report.reason == report.steps[1].reason == "RUN_CANCELLED"
    assert report.steps[1].outcome == Outcome.TEST_INFRA_ERROR
    assert not report.steps[1].attempts
