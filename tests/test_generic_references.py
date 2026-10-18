"""Approved selectors constrain planning; policy and intent checks still authorize execution."""

import copy
import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator

from agenttest.argument_slot_check import REFERENCE_IDS, reference_inputs
from agenttest.demo import PROJECT_ROOT
from agenttest.diagnostics import ValidationFailure
from agenttest.generic_references import reference_schema, validate_generic_references
from agenttest.llm_planner import GenericPlannerDraft, ModelSettings, OpenAICompatiblePlanner


def adapter(transport=None):
    return OpenAICompatiblePlanner(ModelSettings(model="offline-references",
        base_url="https://offline.invalid/v1", api_key="synthetic-reference-key"),
        transport, profile="generic")


@pytest.mark.parametrize("key", REFERENCE_IDS)
def test_schema_accepts_nested_indexed_and_root_references_but_no_other_selectors(key):
    *_, draft, _, context = reference_inputs(key)
    original = copy.deepcopy(context)
    schema = json.loads(adapter().prepare_request(context))["response_format"]["json_schema"][
        "schema"]
    Draft202012Validator.check_schema(schema)
    checker = Draft202012Validator(schema)
    data = draft.model_dump(mode="json")
    assert checker.is_valid(data) and context == original
    expected = data["steps"][1]["arguments"][0]["select"]
    assert reference_schema(context) == {"type": "string", "enum": [expected]}
    for selector in ("$.invented", "$['key']", "$.tokens[*].resource", "private-fragment"):
        changed = copy.deepcopy(data)
        changed["steps"][1]["arguments"][0]["select"] = selector
        assert not checker.is_valid(changed)


@pytest.mark.parametrize("mutation", ["selector", "forward", "producer_policy", "operation",
                                     "literal_slot_reference", "wrong_policy_slot"])
def test_local_reference_gate_checks_slot_and_producer_without_repair(mutation):
    *_, draft, _, context = reference_inputs("GR1101")
    data = draft.model_dump(mode="json")
    reference = data["steps"][1]["arguments"][0]
    if mutation == "selector":
        reference["select"] = "$.other"
    elif mutation == "forward":
        data["steps"].reverse()
    elif mutation == "producer_policy":
        data["steps"][0]["policy_id"] = data["steps"][1]["policy_id"]
    elif mutation == "operation":
        data["steps"][0]["operation_id"] = data["steps"][1]["operation_id"]
    elif mutation == "literal_slot_reference":
        data["steps"][1]["arguments"][1].update(
            value=None, from_step="created", select=reference["select"])
    else:
        data["steps"][1]["policy_id"] = data["steps"][0]["policy_id"]
    parsed = GenericPlannerDraft.model_validate(data)
    before = parsed.model_dump(mode="json")
    with pytest.raises(ValidationFailure) as caught:
        validate_generic_references(parsed, context)
    assert caught.value.diagnostic.code == {
        "selector": "POLICY_DEPENDENCY_MISMATCH", "forward": "MODEL_SCHEMA_GENERIC_REFERENCE",
        "producer_policy": "POLICY_DEPENDENCY_MISMATCH", "operation": "POLICY_DEPENDENCY_MISMATCH",
        "literal_slot_reference": "POLICY_LITERAL_REQUIRED",
        "wrong_policy_slot": "POLICY_SCOPE_MISMATCH"}[mutation]
    assert parsed.model_dump(mode="json") == before
    assert caught.value.diagnostic.reference.branch == {
        "selector": "SELECTOR_POLICY", "forward": "SOURCE_NOT_PREVIOUS",
        "producer_policy": "SOURCE_POLICY", "operation": "SOURCE_OPERATION",
        "literal_slot_reference": "LITERAL_SLOT",
        "wrong_policy_slot": "CONSUMER_OPERATION"}[mutation]


def test_policy_specific_selector_cannot_borrow_another_visible_approved_selector():
    *_, draft, _, context = reference_inputs("GR1101")
    extra = copy.deepcopy(context["policies"][-1])
    extra["id"] = "another_policy"
    extra["binding_rules"][0]["source_selector"] = "$.alternate"
    context["policies"].append(extra)
    assert "$.alternate" in reference_schema(context)["enum"]
    draft.steps[1].arguments[0].select = "$.alternate"
    with pytest.raises(ValidationFailure):
        validate_generic_references(draft, context)


def test_open_catalog_preserves_supported_syntax_without_inventing_a_finite_allowlist():
    *_, draft, _, context = reference_inputs("GR1101")
    context["policies"][-1]["closed_arguments"] = False
    context["policies"][-1]["binding_rules"] = []
    wire = reference_schema(context)
    assert "enum" not in wire and wire["maxLength"] == 200
    checker = Draft202012Validator(wire)
    assert checker.is_valid("$.open[2].field") and not checker.is_valid("$['field']")
    draft.steps[1].arguments[0].select = "$.open[2].field"
    validate_generic_references(draft, context)


def test_closed_literal_only_catalog_cannot_generate_a_reference_and_bad_config_is_reviewed():
    from agenttest.llm_planner import PlannerFailure
    from agenttest.report import Outcome

    *_, _, _, context = reference_inputs("GR1101")
    context["policies"] = context["policies"][:1]
    assert reference_schema(context) is None
    schema = json.loads(adapter().prepare_request(context))["response_format"]["json_schema"][
        "schema"]
    assert len(schema["$defs"]["GenericDraftArgument"]["anyOf"]) == 2
    *_, _, _, bad = reference_inputs("GR1101")
    bad["policies"][-1]["binding_rules"][0]["source_selector"] = "$[*]"
    with pytest.raises(PlannerFailure) as caught:
        adapter().prepare_request(bad)
    assert caught.value.outcome == Outcome.NEEDS_REVIEW
    assert caught.value.reason == "MODEL_REFERENCE_INPUT_UNSUPPORTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("selector,outcome,diagnostic", [
    ("private-fragment", "TOOL_ERROR", "MODEL_SCHEMA_GENERIC_REFERENCE"),
    ("$.unapproved", "NEEDS_REVIEW", "POLICY_DEPENDENCY_MISMATCH")])
async def test_provider_ignoring_schema_is_rejected_before_business_without_raw_values(
    selector, outcome, diagnostic, tmp_path,
):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.contracts import load_openapi
    from agenttest.report import ReportStore
    from agenttest.task_contract import task_validator

    document, target, registry, task, draft, _, context = reference_inputs("GR1101")
    data = draft.model_dump(mode="json")
    data["steps"][1]["arguments"][0]["select"] = selector
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    model = adapter(httpx.MockTransport(lambda req: httpx.Response(200, json=response)))
    calls = []
    report = await workflow.Workflow(document, target, registry, model, ReportStore(tmp_path),
        httpx.MockTransport(lambda req: calls.append(req)), {}, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(document), target, registry)).run()
    assert report.outcome.value == outcome and not calls
    assert report.diagnostic.code == diagnostic
    assert report.diagnostic.reference.branch == (
        "SELECTOR_SYNTAX" if outcome == "TOOL_ERROR" else "SELECTOR_POLICY")
    assert report.diagnostic.reference.step_index == 1
    assert report.diagnostic.reference.argument_index == 0
    assert "private-fragment" not in model.stats.model_dump_json()


@pytest.mark.asyncio
async def test_new_reference_tasks_use_shared_runner_and_cold_regrading():
    pytest.importorskip("agenttest.workflow")
    from agenttest.argument_slot_check import check_slots, verify_slots

    output = PROJECT_ROOT / "runs" / ("pytest-references-" + uuid4().hex)
    result = await check_slots(output, references=True)
    assert result["controls_passed"] == 3 and result["generation_successes"] == 9
    assert result["model_calls"] == 9 and result["real_model_calls"] == 0
    assert result["mock_business_requests"] == 24 and result["usage_status"] == "SIMULATED"
    assert verify_slots(output)["rescored_runs"] == 12
