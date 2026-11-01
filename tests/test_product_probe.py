"""Untrusted investigation choices cannot change approved operations or the original failure."""

import asyncio
import json

import httpx
import pytest
from test_resource_lifecycle import lifecycle_project, plan

from agenttest.dsl import AssertionPolicy, ExpectedStatus
from agenttest.executor import Executor
from agenttest.investigation import InvestigationConfig
from agenttest.policies import PolicyRegistry
from agenttest.product_probe import ProductProbeModel
from agenttest.report import ModelCallStats, Outcome, ReportStore, RunReport, fingerprint
from agenttest.safety import RoleCredential


async def execute(executor):
    return await executor.run(plan(), report=RunReport(
        scenario_sha256=fingerprint(plan().model_dump()),
        contract_sha256=fingerprint(executor.contract.model_dump()),
        environment=executor.config.environment, target_origin=executor.config.base_url,
        task_mode="model_designed"))


class SelectRead:
    stats = ModelCallStats(calls=1, total_tokens=3)

    def __init__(self, mode="normal"):
        self.contexts, self.mode = [], mode

    async def generate(self, context):
        self.contexts.append(context)
        if self.mode == "cancel":
            raise asyncio.CancelledError
        if self.mode == "unavailable":
            raise ValueError("private provider failure")
        first = context["choices"][0]
        return json.dumps(dict(selection="invented_write" if self.mode == "invalid"
                               else first["id"], purpose=first["purpose"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "invalid", "unavailable"])
async def test_failed_read_probes_keep_original_and_cleanup_budget(tmp_path, mode):
    project, _ = lifecycle_project()
    model, requests = SelectRead(mode), []

    def target(request):
        requests.append(request.method)
        return httpx.Response(201, json=dict(id="new_resource")) if request.method == "POST" else (
            httpx.Response(204) if request.method == "DELETE" else
            httpx.Response(200, json=dict(state="broken")))

    report = await execute(Executor(project.target, project.contract, ReportStore(tmp_path),
        httpx.MockTransport(target), {}, project.registry,
        investigation=InvestigationConfig(enabled=True, product_read_probe=True),
        analyzer=model))
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.steps[-1].step_id == "read" and not report.steps[-1].checks[-1].passed
    assert report.resource_lifecycle[0]["status"] == "CLEANED"
    assert requests == (["POST", "GET", "GET", "DELETE"] if mode == "normal" else
                        ["POST", "GET", "DELETE"])
    assert len(model.contexts) == 1
    assert "new_resource" not in json.dumps(model.contexts)
    assert report.failure_explanation["cause_confirmed"] is False
    assert report.failure_explanation["next_action"]
    if mode == "normal":
        assert "再次出现" in report.failure_explanation["confirmed_observation"]
        assert report.investigations[0].requests_used == 1
    else:
        assert not report.investigations
        assert report.probe_decisions[0]["state"] == "REJECTED_OR_UNAVAILABLE"


@pytest.mark.asyncio
async def test_probe_does_not_consume_last_owned_cleanup_request(tmp_path):
    project, _ = lifecycle_project()
    project.target.max_requests = 3
    model, requests = SelectRead(), []

    def target(request):
        requests.append(request.method)
        return httpx.Response(201, json=dict(id="new")) if request.method == "POST" else (
            httpx.Response(204) if request.method == "DELETE" else
            httpx.Response(200, json=dict(state="broken")))

    report = await execute(Executor(project.target, project.contract, ReportStore(tmp_path),
        httpx.MockTransport(target), {}, project.registry,
        investigation=InvestigationConfig(enabled=True, product_read_probe=True),
        analyzer=model))
    assert not model.contexts and requests == ["POST", "GET", "DELETE"]
    assert report.resource_lifecycle[0]["status"] == "CLEANED"


@pytest.mark.asyncio
async def test_unknown_creation_is_not_replayed_or_queried_with_guessed_id(tmp_path):
    project, _ = lifecycle_project()
    model, requests = SelectRead(), []

    def target(request):
        requests.append(request.method)
        raise httpx.ReadTimeout("write may have completed")

    report = await execute(Executor(project.target, project.contract, ReportStore(tmp_path),
        httpx.MockTransport(target), {}, project.registry,
        investigation=InvestigationConfig(enabled=True, product_read_probe=True),
        analyzer=model))
    assert requests == ["POST"] and not model.contexts
    assert report.resource_lifecycle[0]["resource_id"] is None


def test_model_decoder_rejects_new_targets_and_mismatched_purpose():
    from test_product_design import settings

    model = ProductProbeModel(settings(), profile="generic")
    context = dict(choices=[dict(id="approved", purpose="RECHECK_OBSERVATION")])
    for output in [dict(selection="write", purpose="RECHECK_OBSERVATION"),
                   dict(selection="approved", purpose="COMPARE_APPROVED_IDENTITY"),
                   dict(selection=None, purpose="RECHECK_OBSERVATION"),
                   dict(selection="approved", purpose="RECHECK_OBSERVATION", url="http://evil")]:
        with pytest.raises(ValueError):
            model._decode_output(json.dumps(output), context)


@pytest.mark.asyncio
async def test_agent_selects_two_approved_rules_and_never_a_third(tmp_path):
    project, _ = lifecycle_project()
    policies = project.registry.definitions()
    read = next(p for p in policies if p.operation_id == "readJob")
    for number in range(3):
        alternate = read.model_copy(deep=True, update={"id": f"approved_read_{number}"})
        policies.append(alternate)
    project.registry = PolicyRegistry(policies)
    requests, model = [], SelectRead()

    def target(request):
        requests.append(request.method)
        return httpx.Response(201, json=dict(id="new")) if request.method == "POST" else (
            httpx.Response(204) if request.method == "DELETE" else
            httpx.Response(200, json=dict(state="broken")))

    report = await execute(Executor(project.target, project.contract, ReportStore(tmp_path),
        httpx.MockTransport(target), {}, project.registry,
        investigation=InvestigationConfig(enabled=True, product_read_probe=True),
        analyzer=model))
    assert len(model.contexts) == len(report.investigations) == len(report.probe_decisions) == 2
    assert requests == ["POST", "GET", "GET", "GET", "DELETE"]
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.resource_lifecycle[0]["status"] == "CLEANED"


@pytest.mark.asyncio
async def test_cancel_during_agent_decision_keeps_owned_resource_without_write_cleanup(tmp_path):
    project, _ = lifecycle_project()
    requests, model = [], SelectRead("cancel")
    store = ReportStore(tmp_path)

    def target(request):
        requests.append(request.method)
        return httpx.Response(201, json=dict(id="new")) if request.method == "POST" else (
            httpx.Response(200, json=dict(state="broken")))

    with pytest.raises(asyncio.CancelledError):
        await execute(Executor(project.target, project.contract, store,
            httpx.MockTransport(target), {}, project.registry,
            investigation=InvestigationConfig(enabled=True, product_read_probe=True),
            analyzer=model))
    report = store.read(next(tmp_path.glob("*/report.json")).parent.name)
    assert requests == ["POST", "GET"]
    assert report.resource_lifecycle[0]["status"] == "OWNED"
    assert report.probe_decisions[0]["state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_agent_can_compare_only_an_already_approved_identity_on_same_resource(tmp_path):
    project, _ = lifecycle_project()
    project.target.roles = {role: RoleCredential(bearer_env="AGENTTEST_TARGET_" + role.upper())
                            for role in ("owner", "reader")}
    project.contract.security_schemes = dict(bearer=dict(type="http", scheme="bearer"))
    next(o for o in project.contract.operations if o.operation_id == "readJob").security = [
        dict(bearer=[])]
    policies = project.registry.definitions()
    for policy in policies:
        policy.roles = ["owner"]
        for binding in policy.binding_rules:
            if binding.source_policy_id:
                binding.role_relation = "same"
    owner_read = next(p for p in policies if p.operation_id == "readJob")
    reader = owner_read.model_copy(deep=True, update=dict(id="approved_reader", roles=["reader"]))
    reader.assertions = AssertionPolicy(reviewed=True, source="Operator approved reader denial",
                                       status=ExpectedStatus(codes=[403]))
    for binding in reader.binding_rules:
        if binding.source_policy_id:
            binding.role_relation = "different"
    project.registry = PolicyRegistry([*policies, reader])
    scenario = plan()
    for step in scenario.steps:
        step.role = "owner"
    model, reads = SelectRead(), []

    def target(request):
        if request.method == "POST":
            return httpx.Response(201, json=dict(id="new"))
        if request.method == "DELETE":
            return httpx.Response(204)
        reads.append(request.headers.get("authorization"))
        return (httpx.Response(403) if reads[-1] == "Bearer reader-secret"
                else httpx.Response(200, json=dict(state="broken")))

    report = await Executor(project.target, project.contract, ReportStore(tmp_path),
        httpx.MockTransport(target), {"AGENTTEST_TARGET_OWNER": "owner-secret",
            "AGENTTEST_TARGET_READER": "reader-secret"}, project.registry,
        investigation=InvestigationConfig(enabled=True, product_read_probe=True),
        analyzer=model).run(scenario, report=RunReport(
            scenario_sha256=fingerprint(scenario.model_dump()),
            contract_sha256=fingerprint(project.contract.model_dump()),
            environment="isolated", target_origin=project.target.base_url,
            task_mode="model_designed"))
    assert reads == ["Bearer owner-secret", "Bearer owner-secret", "Bearer reader-secret"]
    assert [d["purpose"] for d in report.probe_decisions] == [
        "RECHECK_OBSERVATION", "COMPARE_APPROVED_IDENTITY"]
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.resource_lifecycle[0]["status"] == "CLEANED"
    assert "owner-secret" not in report.model_dump_json()
    assert "reader-secret" not in json.dumps(model.contexts)
