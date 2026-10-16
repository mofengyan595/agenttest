"""Exported Python is constant; authority, credentials and checks remain operator-owned."""

import hashlib
import json
from dataclasses import replace

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.eval_v2.scoring import capture_plan
from agenttest.executor import Executor
from agenttest.portability_demo import project_inputs, task_inputs
from agenttest.regression import (
    PYTEST_TEMPLATE,
    RegressionRuntime,
    assert_regression_passed,
    export_regression,
    load_regression,
    run_regression,
)
from agenttest.report import (
    AttemptEvidence,
    CheckResult,
    Outcome,
    PlanStepEvidence,
    ReportStore,
    RunReport,
    StepResult,
    fingerprint,
)


def synthetic_source(runtime, scenario):
    """Unit-test evidence only; graph integration below supplies actual execution reports."""
    contract = load_openapi(runtime.contract_document)
    operations, policies = Executor(runtime.target, contract, registry=runtime.registry,
                                    environ=runtime.environ).validate(scenario)
    proof = capture_plan(scenario)
    return RunReport(scenario_sha256=proof.scenario_sha256,
        contract_sha256=fingerprint(contract.model_dump()), outcome=Outcome.PASS,
        environment=runtime.target.environment, target_origin=runtime.target.base_url,
        target_config_sha256=fingerprint(runtime.target.model_dump()),
        policy_registry_sha256=runtime.registry.digest(),
        completed=True, workflow_completed=True, task_mode="natural_language", task_satisfied=True,
        plan_valid=True, dsl_valid=True, policy_authorized=True, intent_verified=True,
        user_task_sha256=fingerprint(runtime.task_contract.user_task),
        selected_policy_ids=[s.policy_id for s in scenario.steps],
        plan_structure=[PlanStepEvidence(**{k: getattr(s, k) for k in (
            "step_id", "operation_id", "role", "policy_id", "argument_sha256", "dependencies")})
            for s in proof.steps], steps=[StepResult(step_id=s.id, operation_id=s.operation_id,
                method=operations[s.operation_id].method,
                path_template=operations[s.operation_id].path,
                outcome=Outcome.PASS, reason="CHECKS_PASSED",
                policy_sha256=fingerprint(policies[s.id].model_dump()),
                policy_template_sha256=fingerprint(policies[s.id].model_dump()),
                checks=[CheckResult(kind="http_status", passed=True)],
                attempts=[AttemptEvidence(attempt=1, elapsed_ms=0, status_code=200)])
                for s in scenario.steps])


@pytest.fixture
def inputs(tmp_path):
    app, document, target, registry, environ = project_inputs("tickets")
    task, draft = task_inputs("tickets", "read")
    requests = []

    class ObservedASGI(httpx.ASGITransport):
        async def handle_async_request(self, request):
            requests.append(request.method)
            return await super().handle_async_request(request)

    runtime = RegressionRuntime(document, target, registry, task, environ, ObservedASGI(app),
                                ReportStore(tmp_path / "reports"))
    plan = draft.scenario()
    return runtime, plan, synthetic_source(runtime, plan), requests


def test_export_is_offline_constant_code_and_contains_no_authority_or_credentials(inputs, tmp_path):
    runtime, plan, source, requests = inputs
    output = tmp_path / "fixed_case"
    manifest = export_regression(output, replace(runtime, transport=None), plan, source)
    saved, replay = load_regression(output)
    assert saved == manifest and replay == plan and requests == []
    assert (output / manifest.test_module).read_text(encoding="utf-8") == PYTEST_TEMPLATE
    raw = b"".join(p.read_bytes() for p in output.iterdir())
    assert runtime.target.base_url.encode() not in raw
    for name, value in runtime.environ.items():
        assert name.encode() not in raw and value.encode() not in raw
        assert value not in repr(runtime)
    assert b'"assertions"' not in raw and b'"reviewed"' not in raw


@pytest.mark.parametrize("change", ["outcome", "complete", "goal", "mode", "intent", "policy",
                                    "plan", "contract", "structure", "step_policy", "checks"])
def test_rejects_partial_mismatched_or_unverified_source(inputs, tmp_path, change):
    runtime, plan, source, requests = inputs
    if change == "outcome":
        source.outcome = Outcome.ASSERTION_FAILED
    elif change == "complete":
        source.workflow_completed = False
    elif change == "goal":
        source.task_satisfied = False
    elif change == "mode":
        source.task_mode = "operator_plan"
    elif change == "intent":
        source.intent_verified = False
    elif change == "policy":
        source.policy_authorized = False
    elif change == "plan":
        source.scenario_sha256 = "0" * 64
    elif change == "contract":
        source.contract_sha256 = "0" * 64
    elif change == "structure":
        source.plan_structure.pop()
    elif change == "step_policy":
        source.steps[0].policy_template_sha256 = "0" * 64
    else:
        source.steps[0].checks[0].passed = False
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="SOURCE_NOT_VERIFIED"):
        export_regression(output, runtime, plan, source)
    assert not output.exists() and requests == []


@pytest.mark.parametrize("kind", ["credential", "model_key", "private_url", "sensitive_field"])
def test_export_rejects_sensitive_data_even_in_operator_selected_input(inputs, tmp_path, kind):
    runtime, plan, source, requests = inputs
    if kind == "sensitive_field":
        runtime.target.sensitive_fields = ["subject"]
        runtime.task_contract.target_config_sha256 = fingerprint(runtime.target.model_dump())
    else:
        if kind == "model_key":
            runtime.environ["SERVICE_API_KEY"] = "synthetic-private-service-credential"
        value = runtime.environ["TICKET_OWNER_BEARER"] if kind == "credential" else (
            runtime.environ["SERVICE_API_KEY"] if kind == "model_key" else "https://private.invalid")
        plan.steps[0].body["subject"].value = value
        runtime.task_contract.requirements.steps[0].arguments[0].value = value
    with pytest.raises(ValueError, match="SENSITIVE_DATA"):
        export_regression(tmp_path / "sensitive", runtime, plan, source)
    assert requests == []


def test_output_never_overwrites_and_model_text_is_only_json_data(inputs, tmp_path):
    runtime, plan, _, requests = inputs
    plan.name = "'); raise RuntimeError('INJECTED') #"
    source = synthetic_source(runtime, plan)
    output = tmp_path / "literal_data"
    manifest = export_regression(output, runtime, plan, source)
    before = (output / "manifest.json").read_bytes()
    assert (output / manifest.test_module).read_text(encoding="utf-8") == PYTEST_TEMPLATE
    assert load_regression(output)[1].name == plan.name
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        export_regression(output, runtime, plan, source)
    assert (output / "manifest.json").read_bytes() == before and requests == []


@pytest.mark.parametrize("kind", ["data", "template", "traversal", "hardlink"])
def test_changed_or_linked_package_is_rejected(inputs, tmp_path, kind):
    runtime, plan, source, _ = inputs
    output = tmp_path / "changed"
    manifest = export_regression(output, runtime, plan, source)
    if kind == "data":
        (output / "scenario.json").write_text("{}", encoding="utf-8")
    elif kind == "template":
        (output / manifest.test_module).write_text("raise RuntimeError('changed')",
                                                 encoding="utf-8")
    elif kind == "traversal":
        data = json.loads((output / "manifest.json").read_bytes())
        data["files"]["../outside"] = "0" * 64
        (output / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    else:
        import os

        other = tmp_path / "other.json"
        other.write_bytes((output / "scenario.json").read_bytes())
        (output / "scenario.json").unlink()
        os.link(other, output / "scenario.json")
    with pytest.raises(ValueError):
        load_regression(output)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "target", "task", "transport"])
async def test_replay_revalidates_authority_before_new_execution(inputs, tmp_path, change):
    runtime, plan, source, requests = inputs
    output = tmp_path / "replay"
    export_regression(output, runtime, plan, source)
    if change == "target":
        runtime.target.max_requests += 1
        runtime.task_contract.target_config_sha256 = fingerprint(runtime.target.model_dump())
    elif change == "task":
        runtime.task_contract.user_task += "额外任务"
    elif change == "transport":
        runtime.transport = None
    if change:
        with pytest.raises(ValueError):
            await run_regression(output, runtime)
        assert requests == []
    else:
        result = await run_regression(output, runtime)
        assert result.outcome == Outcome.PASS and result.run_id != source.run_id
        assert result.task_mode == "operator_plan" and result.task_satisfied is None
        assert result.model_call is None and requests == ["POST", "GET"]


@pytest.mark.asyncio
async def test_replay_preserves_business_fault_as_a_real_test_failure(inputs, tmp_path):
    runtime, plan, source, _ = inputs
    output = tmp_path / "fault"
    export_regression(output, runtime, plan, source)
    app, _, _, _, environ = project_inputs("tickets", "wrong_subject")
    broken = replace(runtime, environ=environ, transport=httpx.ASGITransport(app))
    result = await run_regression(output, broken)
    assert result.outcome == Outcome.ASSERTION_FAILED
    assert any(not c.passed and c.selector == "$.payload.subject"
               for s in result.steps for c in s.checks)
    with pytest.raises(AssertionError, match="REGRESSION_ASSERTION_FAILED"):
        assert_regression_passed(result)


def test_cli_exports_existing_plan_and_report_without_business_requests(
    inputs, tmp_path, monkeypatch, capsys,
):
    from agenttest.__main__ import main

    runtime, plan, source, requests = inputs
    runtime.store.save(source)
    for name, value in runtime.environ.items():
        monkeypatch.setenv(name, value)
    values = {"contract": runtime.contract_document,
              "target": runtime.target.model_dump_json().encode(),
              "policies": json.dumps([
                  p.model_dump() for p in runtime.registry.definitions()]).encode(),
              "task-contract": runtime.task_contract.model_dump_json().encode(),
              "plan": plan.model_dump_json().encode()}
    args = ["export-pytest", "--source-run", source.run_id,
            "--runs-dir", str(runtime.store.root), "--output", str(tmp_path / "cli")]
    for key, value in values.items():
        path = tmp_path / (key + ".json")
        path.write_bytes(value)
        args.extend(["--" + key, str(path)])
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["http_requests"] == 0 and requests == []


@pytest.mark.asyncio
async def test_capture_does_not_make_an_invalid_candidate_executable():
    from agenttest.regression import RegressionCapture

    class Reply:
        async def generate(self, context):
            return "print('not JSON')"

    capture = RegressionCapture(Reply())
    assert await capture.generate({}) == "print('not JSON')" and capture.scenario is None


def test_manifest_template_change_cannot_be_hidden_by_rehashing(inputs, tmp_path):
    runtime, plan, source, _ = inputs
    output = tmp_path / "rehash"
    manifest = export_regression(output, runtime, plan, source)
    changed = b"# altered trusted template\n"
    (output / manifest.test_module).write_bytes(changed)
    data = json.loads((output / "manifest.json").read_bytes())
    data["files"][manifest.test_module] = hashlib.sha256(changed).hexdigest()
    (output / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="TEMPLATE_CHANGED"):
        load_regression(output)


def test_legacy_report_hashes_stay_stable_but_do_not_gain_export_eligibility(inputs, tmp_path):
    runtime, plan, source, _ = inputs
    old = source.model_dump(mode="json")
    old.pop("target_config_sha256")
    old.pop("policy_registry_sha256")
    for step in old["steps"]:
        step.pop("policy_template_sha256")
    restored = RunReport.model_validate_json(json.dumps(old))
    assert fingerprint(restored.model_dump(mode="json")) == fingerprint(old)
    with pytest.raises(ValueError, match="SOURCE_NOT_VERIFIED"):
        export_regression(tmp_path / "legacy", runtime, plan, restored)
