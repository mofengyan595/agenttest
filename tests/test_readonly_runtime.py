import asyncio
import json

import httpx
import pytest
from test_planner_input import inputs, scenario

from agenttest.credentials import CredentialVault
from agenttest.demo import PROJECT_ROOT
from agenttest.executor import Executor
from agenttest.investigation import (
    FakeAnalyzer,
    InvestigationBudget,
    InvestigationConfig,
    ReadOnlyCapabilityRegistry,
)
from agenttest.report import Outcome, ReportStore, RunReport


class RecordingStore(ReportStore):
    original = None

    def save(self, report):
        if report.completed and not report.investigations:
            self.original = report.model_copy(deep=True)
        return super().save(report)


class SelectingAnalyzer(FakeAnalyzer):
    def __init__(self, transform):
        super().__init__()
        self.transform = transform

    async def generate(self, context):
        await super().generate(context)
        value = self.transform(context)
        return value if isinstance(value, str) else json.dumps(value)


def setup(tmp_path, *, fault="ownership", analyzer=None, enabled=True, budget=None,
          store=None, investigation_fault=None, resource_id=791, max_requests=40):
    contract, config, registry = inputs()
    config.max_requests = max_requests
    calls = []

    async def handler(req):
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("login"):
            user = json.loads(req.content)["username"]
            return httpx.Response(200, json={"access_token": "session-private-" + user})
        body = {"courseId": resource_id, "name": "线性代数"}
        if req.method == "POST":
            if fault == "create":
                return httpx.Response(500, json={"detail": "private-business-value"})
            return httpx.Response(201, json=body)
        reads = sum(method == "GET" for method, _ in calls)
        if reads >= (3 if fault == "ownership" else 2) and investigation_fault:
            response = await investigation_fault(req)
            if response is not None:
                return response
        if fault == "name":
            body["name"] = "private-wrong-name"
        elif fault == "id":
            body["courseId"] = resource_id + 100
        elif fault == "none" and req.headers["Authorization"].endswith("bob"):
            return httpx.Response(403, json={"detail": "Forbidden"})
        return httpx.Response(200, json=body)

    analyzer = analyzer or FakeAnalyzer()
    store = store or RecordingStore(tmp_path)
    executor = Executor(config, contract, store, httpx.MockTransport(handler), {
        "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": "synthetic-a-password",
        "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": "synthetic-b-password",
    }, registry, investigation=InvestigationConfig(enabled=enabled, budget=budget or
        InvestigationBudget(max_requests=1, max_rounds=1, max_model_calls=1, timeout=2.0)),
        capabilities=ReadOnlyCapabilityRegistry.from_json(
            (PROJECT_ROOT / "examples/nl-readonly-capabilities.json").read_bytes()),
        analyzer=analyzer)
    return executor, calls, analyzer


def assert_original_preserved(executor, report, calls):
    original = executor.store.original
    assert report.outcome == original.outcome == Outcome.ASSERTION_FAILED
    assert report.reason == original.reason and report.steps == original.steps
    assert report.revision > original.revision
    assert sum(m == "POST" and p == "/api/courses" for m, p in calls) == 1
    assert sum(p.endswith("login") for _, p in calls) == 2
    raw = report.model_dump_json()
    for secret in ("session-private", "synthetic-a-password", "synthetic-b-password",
                   "private-wrong-name", "private-business-value", "Authorization"):
        assert secret not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,enabled", [("none", True), ("ownership", False)])
async def test_pass_and_default_disabled_send_no_investigation(tmp_path, fault, enabled):
    executor, calls, analyzer = setup(tmp_path, fault=fault, enabled=enabled)
    report = await executor.run(scenario(True))
    assert len(calls) == 5 and not report.investigations and analyzer.calls == 0


@pytest.mark.asyncio
async def test_ownership_investigation_reuses_auth_and_independent_budget(tmp_path):
    executor, calls, analyzer = setup(tmp_path, max_requests=5)
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    investigation = report.investigations[0]
    assert investigation.outcome == Outcome.PASS and len(calls) == 6
    assert investigation.capability_id == "course_owner_read"
    assert investigation.role == "alice"
    assert investigation.parent_step_id == "forbidden" and investigation.source_step_id == "create"
    assert (investigation.requests_used == investigation.rounds_used
            == investigation.model_calls_used == 1)
    assert investigation.attempts[0].status_code == 200
    assert investigation.attempts[0].elapsed_ms > 0
    assert len(investigation.attempts[0].attempt_id) == 32
    assert investigation.attempts[0].response_sha256 and investigation.elapsed_ms > 0
    assert executor.store.read(report.run_id) == report
    raw = json.dumps(analyzer.contexts)
    for omitted in ("791", "线性代数", "session-private", "password", "base_url", "127.0.0.1"):
        assert omitted not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["name", "id"])
async def test_equality_failure_cannot_switch_resource_or_expectations(tmp_path, fault):
    executor, calls, _ = setup(tmp_path, fault=fault)
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].outcome == Outcome.ASSERTION_FAILED
    assert all(p == "/api/courses/791" for m, p in calls if m == "GET")
    assert report.investigations[0].checks == report.steps[-1].checks


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["max_requests", "max_rounds", "max_model_calls", "timeout"])
async def test_zero_independent_budget_never_borrows_business_budget(tmp_path, field):
    data = dict(max_requests=1, max_rounds=1, max_model_calls=1, timeout=2.0)
    data[field] = 0 if field != "timeout" else 0.0
    executor, calls, analyzer = setup(tmp_path, budget=InvestigationBudget(**data))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].reason == "INVESTIGATION_BUDGET_EXHAUSTED"
    assert len(calls) == 5 and analyzer.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["policy_id", "url", "headers", "raw_id", "assertions",
                                      "write", "getBatch", "unknown_get", "role", "secret",
                                      "invalid_json", "unknown_field", "resource"])
async def test_analyzer_injections_rejected_before_http(tmp_path, mutation):
    def transform(context):
        choice = dict(context["choices"][0])
        if mutation in {"write", "getBatch", "unknown_get"}:
            choice["capability_id"] = {"write": "createCourse", "getBatch": "getBatch",
                                       "unknown_get": "unapproved_get"}[mutation]
        elif mutation == "role":
            choice["role"] = "admin"
        elif mutation == "secret":
            choice["capability_id"] = "session-private-alice"
        elif mutation == "invalid_json":
            return "private-sensitive-invalid-json"
        elif mutation == "resource":
            choice["resource_ref"] = "f" * 32
        else:
            choice[mutation] = "private-sensitive-input"
        return choice

    executor, calls, _ = setup(tmp_path, analyzer=SelectingAnalyzer(transform))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].reason == "INVESTIGATION_SELECTION_REJECTED"
    assert len(calls) == 5 and not report.investigations[0].attempts
    assert "private-sensitive" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_handles_cannot_cross_runs_and_reports_cannot_resume(tmp_path):
    executor, calls, analyzer = setup(tmp_path)
    first = await executor.run(scenario(True))
    old_selection = json.dumps(analyzer.contexts[0]["choices"][0])
    executor.analyzer = FakeAnalyzer(old_selection)
    second = await executor.run(scenario(True))
    assert second.run_id != first.run_id and len(calls) == 11
    assert second.investigations[0].reason == "INVESTIGATION_SELECTION_REJECTED"
    for supplied in (first, executor.store.read(first.run_id), RunReport(
            run_id=first.run_id, scenario_sha256="", contract_sha256="",
            environment=first.environment, target_origin=first.target_origin)):
        with pytest.raises(ValueError, match="RUN_NOT_RESUMABLE"):
            await executor.run(scenario(True), report=supplied)
    assert len(calls) == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "transport", "tool", "cancel"])
async def test_failed_investigation_never_replays_writes(tmp_path, kind, monkeypatch):
    vaults = []
    close = CredentialVault.close

    def capture_close(vault):
        close(vault)
        vaults.append(vault)

    monkeypatch.setattr(CredentialVault, "close", capture_close)

    async def fail(req):
        if kind == "timeout":
            await asyncio.sleep(5)
        if kind == "transport":
            raise httpx.ReadTimeout("private-token", request=req)
        if kind == "tool":
            raise RuntimeError("private-token")
        if kind == "cancel":
            raise asyncio.CancelledError()

    executor, calls, _ = setup(tmp_path, investigation_fault=fail,
        budget=InvestigationBudget(max_requests=1, max_rounds=1,
                                   max_model_calls=1, timeout=.5))
    if kind == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await executor.run(scenario(True))
        directories = list(tmp_path.iterdir())
        report = executor.store.read(directories[0].name)
        assert report.investigations[0].reason == "ATTEMPT_OUTCOME_UNKNOWN"
    else:
        report = await executor.run(scenario(True))
        assert report.investigations[0].reason == {
            "timeout": "INVESTIGATION_TIMEOUT", "transport": "INVESTIGATION_TRANSPORT_ERROR",
            "tool": "INVESTIGATION_TOOL_ERROR"}[kind]
    assert_original_preserved(executor, report, calls)
    assert len(calls) == 6 and vaults
    assert all(not v._tokens and not v._secrets and not v._environ for v in vaults)


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before_send", "after_send"])
async def test_report_failure_is_fail_closed_without_http_replay(tmp_path, when):
    class FailingStore(RecordingStore):
        def save(self, report):
            if report.investigations:
                attempts = report.investigations[-1].attempts
                if attempts and (when == "before_send" or attempts[-1].status_code is not None):
                    raise OSError("private-file-detail")
            return super().save(report)

    store = FailingStore(tmp_path)
    executor, calls, _ = setup(tmp_path, store=store)
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigation_error == "INVESTIGATION_REPORT_ERROR"
    assert len(calls) == (5 if when == "before_send" else 6)
    assert "private-file-detail" not in report.model_dump_json()
    saved = store.read(report.run_id)
    assert saved.outcome == Outcome.ASSERTION_FAILED
    with pytest.raises(ValueError, match="RUN_NOT_RESUMABLE"):
        await executor.run(scenario(True), report=saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("what", ["target", "policy", "budget", "result"])
async def test_authority_and_business_result_are_frozen(tmp_path, what):
    executor, calls, _ = setup(tmp_path)

    def transform(context):
        if what == "target":
            executor.config.base_url = "http://169.254.169.254"
        elif what == "policy":
            executor.registry._entries.clear()
        elif what == "budget":
            executor.investigation.budget.max_requests = 50
        else:
            # Simulate an injected collaborator mutating the object held by a custom store.
            active_report.outcome = Outcome.PASS
        return context["choices"][0]

    executor.analyzer = SelectingAnalyzer(transform)
    active_report = RunReport(scenario_sha256="", contract_sha256="",
                              environment="isolated", target_origin=executor.config.base_url)
    report = await executor.run(scenario(True), report=active_report)
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].reason == "INVESTIGATION_AUTHORITY_CHANGED"
    assert len(calls) == 5 and executor.config.base_url != "http://169.254.169.254"


@pytest.mark.asyncio
async def test_failed_create_has_no_approved_resource_and_no_investigation(tmp_path):
    executor, calls, analyzer = setup(tmp_path, fault="create")
    report = await executor.run(scenario(True))
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert len(calls) == 3 and not report.investigations and analyzer.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["forged_policy", "write", "state_get", "scenario",
                                      "source_policy", "selector", "poll"])
async def test_capability_still_requires_contract_and_trusted_policy(tmp_path, mutation):
    executor, calls, analyzer = setup(tmp_path)
    entries = executor.capabilities.entries()[:1]
    capability = entries[0]
    if mutation == "forged_policy":
        capability.policy_id = "forged"
    elif mutation == "write":
        capability.operation_id = "createCourse"
    elif mutation == "state_get":
        capability.operation_id = "getBatch"
    elif mutation == "scenario":
        capability.business_scenario = "production"
    elif mutation == "source_policy":
        capability.source_policy_id = "nl_login_v1"
    elif mutation == "selector":
        capability.source_selector = "$.name"
    elif mutation == "poll":
        policy = json.loads(executor.registry._entries[capability.policy_id])
        policy["poll"] = dict(select="$.state", terminal_values=["SUCCEEDED"])
        executor.registry._entries[capability.policy_id] = json.dumps(policy)
    executor.capabilities = ReadOnlyCapabilityRegistry(entries)
    report = await executor.run(scenario(True))
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert len(calls) == 5 and not report.investigations and analyzer.calls == 0


@pytest.mark.asyncio
async def test_attempt_id_and_reserved_budget_are_persisted_before_send(tmp_path):
    observed = []

    async def inspect(req):
        saved = executor.store.read(executor.store.original.run_id)
        investigation = saved.investigations[0]
        attempt = investigation.attempts[0]
        observed.append(attempt.attempt_id)
        assert investigation.requests_used == 1 and attempt.status_code is None
        assert attempt.error_code == "ATTEMPT_OUTCOME_UNKNOWN"

    executor, calls, _ = setup(tmp_path, investigation_fault=inspect)
    report = await executor.run(scenario(True))
    assert observed == [report.investigations[0].attempts[0].attempt_id]
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_foreign_create_source_cannot_replace_failed_resource(tmp_path):
    def transform(context):
        return {**context["choices"][0], "source_step_id": "another_create"}

    executor, calls, _ = setup(tmp_path, analyzer=SelectingAnalyzer(transform))
    report = await executor.run(scenario(True))
    assert report.investigations[0].reason == "INVESTIGATION_SELECTION_REJECTED"
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_analyzer_errors_have_fixed_codes_and_no_http(tmp_path):
    def fail(context):
        raise RuntimeError("private-sensitive-model-error")

    executor, calls, _ = setup(tmp_path, analyzer=SelectingAnalyzer(fail))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].reason == "INVESTIGATION_TOOL_ERROR"
    assert len(calls) == 5
    assert "private-sensitive" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_analyzer_can_select_other_approved_policy_without_changing_failure(tmp_path):
    analyzer = SelectingAnalyzer(lambda context: next(
        c for c in context["choices"] if c["capability_id"] == "course_other_read"))
    executor, calls, _ = setup(tmp_path, analyzer=analyzer)
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].capability_id == "course_other_read"
    assert report.investigations[0].checks[0].expected == [403]
    assert report.investigations[0].outcome == Outcome.ASSERTION_FAILED
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_expired_authentication_does_not_trigger_login(tmp_path):
    async def expired(req):
        return httpx.Response(401, json={"detail": "private-auth-detail"})

    executor, calls, _ = setup(tmp_path, investigation_fault=expired)
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].reason == "INVESTIGATION_ASSERTION_FAILED"
    assert report.investigations[0].attempts[0].status_code == 401
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_authority_drift_during_investigation_is_detected_after_response(tmp_path):
    async def mutate(req):
        executor.investigation.budget.max_requests = 20

    executor, calls, _ = setup(tmp_path, investigation_fault=mutate)
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.investigations[0].reason == "INVESTIGATION_AUTHORITY_CHANGED"
    assert executor.investigation.budget.max_requests == 1 and len(calls) == 6
