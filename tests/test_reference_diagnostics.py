"""Reference diagnostics retain classifications without persisting rejected plans."""

import json
from uuid import uuid4

import httpx
import pytest

from agenttest.argument_slot_check import (
    REFERENCE_IDS,
    check_slots,
    reference_inputs,
    verify_slots,
)
from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.diagnostics import Diagnostic, Layer, ValidationFailure
from agenttest.generic_references import validate_generic_references
from agenttest.llm_planner import GenericPlannerDraft, ModelSettings, OpenAICompatiblePlanner
from agenttest.report import ReportStore
from agenttest.task_contract import task_validator


def test_legacy_diagnostic_serialization_is_unchanged():
    assert Diagnostic(layer=Layer.MODEL, code="MODEL_INVALID_RESPONSE").model_dump() == {
        "layer": Layer.MODEL, "code": "MODEL_INVALID_RESPONSE", "fields": []}


@pytest.mark.parametrize("mutation,branch", [
    ("unknown", "SOURCE_UNKNOWN"), ("self", "SOURCE_NOT_PREVIOUS"),
    ("policy", "CONSUMER_POLICY"), ("slot", "ARGUMENT_SLOT"),
])
def test_missing_source_and_policy_branches_have_only_bounded_locations(mutation, branch):
    *_, draft, _, context = reference_inputs("GR1103")
    data = draft.model_dump(mode="json")
    reference = data["steps"][1]["arguments"][0]
    if mutation == "unknown":
        reference["from_step"] = "private_credential_marker"
    elif mutation == "self":
        reference["from_step"] = data["steps"][1]["id"]
    elif mutation == "policy":
        data["steps"][1]["policy_id"] = "private_credential_marker"
    else:
        reference["name"] = "private_credential_marker"
    with pytest.raises(ValidationFailure) as caught:
        validate_generic_references(GenericPlannerDraft.model_validate(data), context)
    diagnostic = caught.value.diagnostic
    assert diagnostic.reference.model_dump() == {
        "branch": branch, "step_index": 1, "argument_index": 0}
    assert "private_credential_marker" not in diagnostic.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", REFERENCE_IDS)
@pytest.mark.parametrize("mutation,branch,outcome", [
    (None, None, "PASS"), ("selector", "SELECTOR_POLICY", "NEEDS_REVIEW"),
    ("policy", "SOURCE_POLICY", "NEEDS_REVIEW"),
    ("operation", "SOURCE_OPERATION", "NEEDS_REVIEW"),
    ("missing", "SOURCE_UNKNOWN", "TOOL_ERROR"),
])
async def test_diagnostic_survives_model_and_report_before_any_business_io(
    tmp_path, key, mutation, branch, outcome,
):
    workflow = pytest.importorskip("agenttest.workflow")
    document, target, registry, task, draft, business, _ = reference_inputs(key)
    data = draft.model_dump(mode="json")
    if mutation == "selector":
        data["steps"][1]["arguments"][0]["select"] = "$.private_credential_marker"
    elif mutation == "policy":
        data["steps"][0]["policy_id"] = data["steps"][1]["policy_id"]
    elif mutation == "operation":
        data["steps"][0]["operation_id"] = data["steps"][1]["operation_id"]
        data["steps"][0]["arguments"] = []
    elif mutation == "missing":
        data["steps"][1]["arguments"][0]["from_step"] = "private_credential_marker"
    model = OpenAICompatiblePlanner(ModelSettings(model="offline-diagnostics",
        base_url="https://offline.invalid/v1", api_key="synthetic-diagnostic-key"),
        httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})),
        profile="generic")
    calls = []

    async def capture(request):
        calls.append(request.method)
        return await business.handle_async_request(request)

    store = ReportStore(tmp_path)
    report = await workflow.Workflow(document, target, registry, model, store,
        httpx.MockTransport(capture), {}, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(document), target, registry)).run()
    assert report.outcome.value == outcome and report == store.read(report.run_id)
    if branch:
        assert not calls
        assert report.diagnostic.reference.branch == branch
        assert model.stats.diagnostic == report.diagnostic
    else:
        assert calls == ["POST", "GET"] and report.task_satisfied
    assert "private_credential_marker" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_fresh_diagnostic_tasks_reuse_runner_and_cold_regrading():
    pytest.importorskip("agenttest.workflow")
    directory = PROJECT_ROOT / "runs" / ("pytest-goal-diagnostics-" + uuid4().hex)
    result = await check_slots(directory, diagnostics=True)
    assert result["controls_passed"] == 3 and result["generation_successes"] == 9
    assert result["mock_business_requests"] == 24 and result["real_model_calls"] == 0
    assert result["usage_status"] == "SIMULATED"
    assert verify_slots(directory)["rescored_runs"] == 12
