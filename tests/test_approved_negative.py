"""Negative requests cross exactly one operator-approved field boundary, never a gate bypass."""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.negative_demo import create_negative_target
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.project_goals import load_goal_project
from agenttest.report import Outcome, ReportStore

ROOT = Path(__file__).resolve().parents[1] / "examples/trusted-testing/development/validation"


def project():
    return load_goal_project(ROOT / "registered.project.json")


def scenario(policy="validation_normal", **body):
    values = dict(message="hello", category="a", count=2)
    values.update(body)
    return Scenario.model_validate(dict(name="approved input rejection", steps=[dict(
        id="create", operation_id="validateRecord", policy_id=policy,
        body={k: dict(value=v) for k, v in values.items()})]))


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["validation_normal", "missing_message", "wrong_message_type",
    "wrong_category_type", "outside_category", "below_count", "above_count", "short_message",
    "long_message"])
async def test_approved_mutations_reach_target_and_keep_normal_validation(tmp_path, policy):
    p = project()
    seen = []

    class Observed(httpx.ASGITransport):
        async def handle_async_request(self, request):
            seen.append(json.loads(await request.aread()))
            return await super().handle_async_request(request)

    result = await Executor(p.target, p.contract, ReportStore(tmp_path),
        Observed(app=create_negative_target()), {}, p.registry).run(scenario(policy))
    assert result.outcome == Outcome.PASS and len(seen) == 1
    step = result.steps[0]
    if policy == "validation_normal":
        assert step.input_validation == "NORMAL_VALIDATED" and not step.negative_test
    else:
        assert step.input_validation == "APPROVED_NEGATIVE"
        assert step.negative_rejected_as_expected and step.approved_violation_codes
        original = dict(message="hello", category="a", count=2)
        changed = {k for k in original if original[k] != seen[0].get(k)}
        assert len(changed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["accept_invalid", "wrong_error"])
async def test_target_acceptance_or_incorrect_error_is_assertion_failure(tmp_path, fault):
    p = project()
    result = await Executor(p.target, p.contract, ReportStore(tmp_path),
        httpx.ASGITransport(app=create_negative_target(fault)), {}, p.registry).run(
            scenario("missing_message"))
    assert result.outcome == Outcome.ASSERTION_FAILED
    assert result.steps[0].negative_rejected_as_expected is False
    assert any(not c.passed for c in result.steps[0].checks)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["baseline", "unknown_policy", "wrong_operation", "extra_field",
    "bad_role", "retry", "upload", "resource_source"])
async def test_unapproved_deviations_never_send(tmp_path, change):
    p = project()
    plan = scenario("missing_message")
    step = plan.steps[0]
    if change == "baseline":
        step.body["count"].value = -1
    elif change == "unknown_policy":
        step.policy_id = "model_invented_negative"
    elif change == "wrong_operation":
        step.operation_id = "unknown"
    elif change == "extra_field":
        step.body["extra"] = step.body["count"].model_copy()
    elif change == "bad_role":
        step.role = "intruder"
    elif change == "retry":
        step.read_retries = 1
    elif change == "upload":
        from agenttest.dsl import CSVUpload

        step.upload = CSVUpload(content="x")
    else:
        step.body["message"].from_step = "invented"
    calls = []
    result = await Executor(p.target, p.contract, ReportStore(tmp_path),
        httpx.MockTransport(lambda r: calls.append(r)), {}, p.registry).run(plan)
    assert result.outcome in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}
    assert calls == []


@pytest.mark.asyncio
async def test_negative_response_cannot_supply_a_resource(tmp_path):
    p = project()
    plan = scenario("missing_message")
    consumer = deepcopy(plan.steps[0])
    consumer.id, consumer.policy_id = "consumer", "validation_normal"
    from agenttest.dsl import ArgumentBinding

    consumer.body["message"] = ArgumentBinding(from_step="create", select="$.code")
    plan.steps.append(consumer)
    # Even when an operator relation exists, a rejection payload is not a created resource.
    entries = p.registry.definitions()
    normal = deepcopy(next(x for x in entries if x.id == "validation_normal"))
    normal.id = "validation_consumer"
    consumer.policy_id = normal.id
    normal.binding_rules[0].literal_schema = None
    normal.binding_rules[0].source_policy_id = "missing_message"
    normal.binding_rules[0].source_selector = "$.code"
    entries.append(normal)
    calls = []
    report = await Executor(p.target, p.contract, ReportStore(tmp_path),
        httpx.MockTransport(lambda r: calls.append(r)), {}, PolicyRegistry(entries)).run(plan)
    assert report.outcome == Outcome.NEEDS_REVIEW and not calls
    assert report.diagnostic.code == "NEGATIVE_RESPONSE_IS_NOT_A_RESOURCE"


@pytest.mark.parametrize("change", ["extra_violation", "missing_violation", "valid_value",
                                    "wrong_base", "no_error_status", "wrong_field"])
def test_malformed_operator_approval_is_rejected(change):
    p = project()
    entries = p.registry.definitions()
    bad = next(x for x in entries if x.id == "wrong_category_type")
    if change == "extra_violation":
        bad.negative.violation_codes.append("OPENAPI_VALUE_LENGTH")
    elif change == "missing_violation":
        bad.negative.violation_codes.remove("OPENAPI_VALUE_ENUM")
    elif change == "valid_value":
        bad.negative.value = "a"
    elif change == "wrong_base":
        bad.negative.base_policy_id = "missing"
    elif change == "no_error_status":
        bad.assertions.status.codes = [200]
    else:
        bad.negative.name = "unregistered_field"
    with pytest.raises(NeedsReview):
        registry = PolicyRegistry(entries)
        Executor(p.target, p.contract, registry=registry, environ={}).validate(
            scenario(bad.id))


@pytest.mark.asyncio
async def test_body_limit_and_infrastructure_failure_are_not_business_bugs(tmp_path):
    p = project()
    p.target.max_body_bytes = 1
    report = await Executor(p.target, p.contract, ReportStore(tmp_path / "size"),
        httpx.ASGITransport(app=create_negative_target()), {}, p.registry).run(
            scenario("missing_message"))
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[0].reason == "REQUEST_BODY_LIMIT" and not report.steps[0].attempts

    def timeout(request):
        raise httpx.ReadTimeout("synthetic", request=request)

    p.target.max_body_bytes = 1000
    report = await Executor(p.target, p.contract, ReportStore(tmp_path / "timeout"),
        httpx.MockTransport(timeout), {}, p.registry).run(scenario("missing_message"))
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[0].negative_rejected_as_expected is None


def test_missing_expectations_stay_pending_and_approved_suite_has_no_guessed_errors():
    from agenttest.discovery import discover
    from agenttest.goal_suite import build_suite

    suite = build_suite(project(), "测试创建接口")
    assert len([c for c in suite.cases if any(k.startswith("approved_negative:")
                                             for k in c.kinds)]) == 8
    pending = [c for c in suite.cases if c.reason == "EXPECTED_ERROR_BEHAVIOR_NOT_APPROVED"]
    assert pending and all(c.goal is None for c in pending)
    data = json.loads((ROOT / "policies.json").read_text())
    assert not load_openapi(project().document).operations[0].security
    assert all(x["assertions"]["source"] for x in data)
    p = project()
    discovered = discover(p.contract, p.registry, p.target)
    approved = [c for c in discovered.candidates if c.policy_refs
                and any(p.id in c.policy_refs for p in p.registry.definitions() if p.negative)]
    assert len(approved) == 8
    assert all(c.polarity == "negative" and c.executable_status == "STATIC_VALIDATED"
               and c.schema_valid is False and c.policy_match for c in approved)
