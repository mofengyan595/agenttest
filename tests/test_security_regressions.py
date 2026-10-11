import asyncio
from pathlib import Path

import httpx
import pytest

from agenttest.contracts import ContractSummary, Operation, load_openapi
from agenttest.dsl import AssertionPolicy, Scenario, validate_plan
from agenttest.executor import Executor
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig
from agenttest.verifier import verify


@pytest.mark.parametrize("extra", [
    '"get":{"operationId":"x"}',
    '"get":{"responses":{"200":{}}}',
    '"get":{"responses":{"200":{"description":"ok"}},"parameters":[{"name":"x"}]}',
])
def test_invalid_openapi_structure(extra: str) -> None:
    with pytest.raises(ValueError):
        load_openapi('{"openapi":"3.1.0","info":{"title":"x","version":"1"},'
                     '"paths":{"/x":{' + extra + '}}}')


def operation(method="GET", path="/items", body=None) -> ContractSummary:
    return ContractSummary(version="3.1.0", operations=[Operation(
        method=method, path=path, operation_id="items", request_body=body,
    )])


def plan(**overrides) -> Scenario:
    return Scenario.model_validate({"name": "security", "steps": [{
        "id": "first", "operation_id": "items", "assertions": {
            "reviewed": True, "source": "security fixture", "status": {"codes": [200]},
        }, **overrides,
    }]})


def config(**overrides) -> TargetConfig:
    return TargetConfig(allowed_operations=["items"], environment="isolated", allow_mutations=True,
                        min_request_interval=0.0, **overrides)


@pytest.mark.parametrize("path", ["/../secret", "/%2e%2e/secret", "/%252e%252e/secret"])
def test_encoded_path_traversal_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        validate_plan(plan(), operation(path=path), config())


@pytest.mark.parametrize("overrides", [
    {"read_retries": 1},
    {"poll": {"select": "$.state", "terminal_values": ["DONE"]}},
])
def test_mutations_cannot_retry_or_poll(overrides: dict) -> None:
    with pytest.raises(ValueError):
        validate_plan(plan(**overrides), operation(method="POST"), config())


@pytest.mark.asyncio
async def test_total_timeout_has_infrastructure_outcome(tmp_path: Path) -> None:
    async def handler(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200)

    report = await Executor(config(run_timeout=0.03), operation(), ReportStore(tmp_path),
                            httpx.MockTransport(handler)).run(plan())
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.reason == "RUN_TIMEOUT"
    assert ReportStore(tmp_path).read(report.run_id).completed


@pytest.mark.asyncio
async def test_oversize_request_rejected_before_transmit(tmp_path: Path) -> None:
    def never(request):
        pytest.fail("Oversize request must not be sent")

    contract = operation(method="POST", body={"content": {"application/json": {}}})
    executor = Executor(config(max_body_bytes=10), contract, ReportStore(tmp_path),
                        httpx.MockTransport(never))
    report = await executor.run(plan(body={"text": {"value": "x" * 50}}))
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[0].reason == "REQUEST_BODY_LIMIT"


@pytest.mark.asyncio
async def test_cookies_never_cross_steps_and_unknown_policy_stops(tmp_path: Path) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        assert "Cookie" not in request.headers
        return httpx.Response(200, headers={"Set-Cookie": "session=private"})

    scenario = plan()
    scenario.steps.append(scenario.steps[0].model_copy(update={"id": "second"}))
    executor = Executor(config(), operation(), ReportStore(tmp_path), httpx.MockTransport(handler))
    assert (await executor.run(scenario)).outcome == Outcome.PASS and len(calls) == 2
    scenario.steps[0].assertions = AssertionPolicy()
    assert (await executor.run(scenario)).outcome == Outcome.NEEDS_REVIEW
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_poll_exhaustion_never_bug(tmp_path: Path) -> None:
    report = await Executor(
        config(), operation(), ReportStore(tmp_path),
        httpx.MockTransport(lambda request: httpx.Response(200, json={"state": "RUNNING"})),
    ).run(plan(poll={"select": "$.state", "terminal_values": ["DONE"],
                    "max_attempts": 2, "interval": 0.0}))
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[0].reason == "POLL_EXHAUSTED"


def test_sensitive_numeric_values_are_redacted() -> None:
    policy = AssertionPolicy.model_validate({
        "reviewed": True, "source": "sensitive regression", "fields": [
            {"select": "$.password", "expected": 123456},
        ],
    })
    _, checks = verify(policy, 200, {"password": 123456})
    assert "123456" not in checks[0].model_dump_json()
