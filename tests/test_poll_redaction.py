import httpx
import pytest
from support import registry

from agenttest.contracts import ContractSummary, Operation
from agenttest.dsl import AssertionPolicy, FieldAssertion, Scenario, Step
from agenttest.executor import Executor
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig
from agenttest.verifier import verify


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (401, {"detail": "expired"}), (403, {}), (404, {}), (500, {"state": "RUNNING"}),
    (200, {}), (200, None), (200, {"state": 1}), (200, {"state": "unknown"}),
])
async def test_poll_business_contract_violations_are_assertion_failures(tmp_path, status, body):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json=body)

    executor = Executor(TargetConfig(allowed_operations=["items"]), ContractSummary(
        version="3.1.0", operations=[Operation(method="GET", path="/", operation_id="items")]),
        ReportStore(tmp_path), httpx.MockTransport(handler), registry=registry())
    report = await executor.run(Scenario(name="poll", steps=[Step(
        id="poll", operation_id="items", policy_id="items_v1", poll=True)]))
    assert report.outcome == Outcome.ASSERTION_FAILED and len(calls) == 1
    assert report.steps[0].reason == "POLL_CONTRACT_VIOLATION"
    assert any(not check.passed for check in report.steps[0].checks)


@pytest.mark.parametrize("field", [
    "password", "otp", "verification_code", "identity", "id_card", "phone", "user_id",
    "customer_private_number", "innocent_alias",
])
def test_numeric_evidence_is_private_by_default(field):
    policy = AssertionPolicy(reviewed=True, source="operator", fields=[
        {"select": "$." + field, "expected": 246810},
    ])
    outcome, checks = verify(policy, 200, {field: 246810})
    assert outcome == Outcome.PASS
    assert "246810" not in checks[0].model_dump_json()


def test_sensitive_field_overrides_public_evidence_allowlist():
    for field in ("otp", "identity", "custom_private"):
        policy = AssertionPolicy(reviewed=True, source="operator", fields=[
            {"select": "$." + field, "expected": 246810}], public_evidence=["$." + field])
        _, checks = verify(policy, 200, {field: 246810}, sensitive_fields=["custom_private"])
        assert checks[0].actual == checks[0].expected == "[REDACTED]"
    policy = AssertionPolicy(reviewed=True, source="operator", fields=[
        {"select": "$.count", "expected": 2}], public_evidence=["$.count"])
    assert verify(policy, 200, {"count": 2})[1][0].actual == 2


@pytest.mark.asyncio
async def test_known_numeric_secret_redacted_even_under_public_alias(tmp_path):
    step = Step(id="read", operation_id="items", policy_id="items_v1")
    approved = registry().resolve(step, "default")
    approved.assertions.fields = [FieldAssertion(select="$.count", expected=246810)]
    approved.assertions.public_evidence = ["$.count"]
    executor = Executor(TargetConfig(allowed_operations=["items"], roles={
        "user": {"password_env": "PRIVATE"}}), ContractSummary(version="3.1.0", operations=[
            Operation(method="GET", path="/", operation_id="items")]), ReportStore(tmp_path),
        httpx.MockTransport(lambda req: httpx.Response(200, json={"count": 246810})),
        {"PRIVATE": "246810"}, registry=PolicyRegistry([approved]))
    report = await executor.run(Scenario(name="alias", steps=[step]))
    assert report.outcome == Outcome.PASS
    assert "246810" not in report.model_dump_json()
    assert "246810" not in executor.store.read(report.run_id).model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,outcome", [
    (httpx.ReadTimeout("private"), Outcome.TEST_INFRA_ERROR),
    (RuntimeError("private"), Outcome.TOOL_ERROR),
])
async def test_poll_transport_and_tool_exceptions_remain_distinct(tmp_path, error, outcome):
    def handler(req):
        raise error

    executor = Executor(TargetConfig(allowed_operations=["items"]), ContractSummary(
        version="3.1.0", operations=[Operation(method="GET", path="/", operation_id="items")]),
        ReportStore(tmp_path), httpx.MockTransport(handler), registry=registry())
    report = await executor.run(Scenario(name="poll", steps=[Step(
        id="poll", operation_id="items", policy_id="items_v1", poll=True)]))
    assert report.outcome == outcome
    assert "private" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_poll_progress_fields_are_checked_before_terminal(tmp_path):
    step = Step(id="poll", operation_id="items", policy_id="items_v1", poll=True)
    policy = registry().resolve(step, "default")
    policy.poll.progress_fields = [FieldAssertion(select="$.count", expected=2)]
    executor = Executor(TargetConfig(allowed_operations=["items"]), ContractSummary(
        version="3.1.0", operations=[Operation(method="GET", path="/", operation_id="items")]),
        ReportStore(tmp_path), httpx.MockTransport(lambda req: httpx.Response(
            200, json={"state": "RUNNING", "count": 0})), registry=PolicyRegistry([policy]))
    report = await executor.run(Scenario(name="progress", steps=[step]))
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert len(report.steps[0].attempts) == 1
