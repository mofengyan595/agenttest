"""Generic structured containers stay data and retain exact recursive JSON types."""

import json
import math

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.dsl import AssertionPolicy
from agenttest.executor import Executor
from agenttest.json_literal_check import CASE_IDS, REPAIR_CASE_IDS, case_inputs, draft_value
from agenttest.json_values import json_equal
from agenttest.llm_planner import (
    DraftArgument,
    GenericDraftArgument,
    GenericPlannerDraft,
    ModelSettings,
    OpenAICompatiblePlanner,
)
from agenttest.planner_input import planning_context
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.request_validation import validate_value
from agenttest.verifier import verify


def argument(**changes):
    return {"location": "body", "name": "config", "value": None,
            "from_step": None, "select": None, **changes}


@pytest.mark.parametrize("value", [None, False, True, 0, 7, 0.125, -0.375, "原文",
                                   {}, [], {"nested": [False, 1, 1.5, None]},
                                   [{"note": "保留\"原样\"", "empty": []}]])
def test_scalar_and_container_types_survive_conversion_exactly(value):
    data = argument(value=draft_value(value))
    parsed = GenericDraftArgument.model_validate(data)
    assert json_equal(parsed.literal_value(), value)
    assert fingerprint(parsed.literal_value()) == fingerprint(value)


@pytest.mark.parametrize("changes", [
    {"value": {"unwrapped": True}}, {"value": []},
    {"value": {"object": [], "array": []}},
    {"value": {"object": [{"key": "x", "value": 1}, {"key": "x", "value": 2}]}},
    {"value": {"array": [math.nan]}},
    {"value": {"object": []}, "from_step": "created", "select": "$.payload"},
    {"value": {"object": []}, "location": "query"},
    {"value": {"array": ["x" * 20_000]}},
    {"value": math.nan}, {"value": math.inf},
])
def test_invalid_ambiguous_nonfinite_or_unbounded_json_is_rejected(changes):
    with pytest.raises(ValueError):
        GenericDraftArgument.model_validate(argument(**changes))


def test_existing_scalar_replies_and_non_generic_profiles_keep_their_contract():
    legacy = argument(value="original")
    assert GenericDraftArgument.model_validate(legacy).literal_value() == "original"
    with pytest.raises(ValueError):
        DraftArgument.model_validate({**legacy, "value": 0.125})
    doc, target, registry, _, _, task, _, _ = case_inputs("J801")
    context = planning_context(load_openapi(doc), target, registry, task.user_task, {})
    settings = ModelSettings(model="offline", base_url="https://offline.invalid/v1", api_key="fake")
    generic = json.loads(OpenAICompatiblePlanner(
        settings, profile="generic").prepare_request(context))
    schema = generic["response_format"]["json_schema"]["schema"]
    pending = [schema]
    while pending:
        definition = pending.pop()
        if isinstance(definition, dict):
            if definition.get("type") == "object":
                assert definition["additionalProperties"] is False
                assert set(definition["required"]) == set(definition["properties"])
            pending.extend(definition.values())
        elif isinstance(definition, list):
            pending.extend(definition)
    variants = schema["$defs"]["GenericDraftArgument"]["anyOf"]
    prop = variants[1]["properties"]["value"]
    assert all("$ref" in alternative for alternative in prop["anyOf"])
    assert all(variants[1]["properties"][p]["type"] == "null"
               for p in ("from_step", "select"))
    assert "json_value" not in variants[1]["properties"]
    role_ref = schema["$defs"]["GenericDraftStep"]["properties"]["role"]["$ref"]
    assert schema["$defs"][role_ref.rsplit("/", 1)[1]]["enum"] == [None]
    original = json.loads(OpenAICompatiblePlanner(settings).prepare_request(context))
    original_schema = original["response_format"]["json_schema"]["schema"]
    assert "GenericDraftArgument" not in original_schema["$defs"]


def test_outbound_schema_rejects_conflicting_sources_before_provider_generation():
    from jsonschema import Draft202012Validator

    doc, target, registry, _, _, task, draft, _ = case_inputs("J801")
    planner = OpenAICompatiblePlanner(ModelSettings(model="offline",
        base_url="https://offline.invalid/v1", api_key="synthetic-credential"), profile="generic")
    payload = json.loads(planner.prepare_request(planning_context(
        load_openapi(doc), target, registry, task.user_task, {})))
    validator = Draft202012Validator(payload["response_format"]["json_schema"]["schema"])
    valid = json.loads(draft.model_dump_json())
    assert validator.is_valid(valid)
    for field, value in (("from_step", "previous"), ("select", "$.payload"),
                         ("location", "query")):
        changed = json.loads(json.dumps(valid))
        changed["steps"][0]["arguments"][1][field] = value
        assert not validator.is_valid(changed)


@pytest.mark.parametrize("actual,expected", [
    ({"nested": [1]}, {"nested": [True]}), ({"nested": [1]}, {"nested": [1.0]}),
    ([{"enabled": False}], [{"enabled": 0}]), ([1, 2], [2, 1]),
])
def test_nested_types_and_array_order_cannot_falsely_pass_equals_or_membership(actual, expected):
    for operator, value in (("equals", expected), ("in", [expected])):
        policy = AssertionPolicy.model_validate({"reviewed": True, "source": "fixed test policy",
            "fields": [{"select": "$.value", "operator": operator, "expected": value}]})
        assert verify(policy, 200, {"value": actual})[0] == Outcome.ASSERTION_FAILED
    assert json_equal({"a": True, "b": [None]}, {"b": [None], "a": True})


def test_nested_enum_cannot_confuse_boolean_and_number():
    schema = {"type": "object", "properties": {"x": {"type": "number"}},
              "additionalProperties": False, "enum": [{"x": True}]}
    with pytest.raises(ValidationFailure, match="outside enum"):
        validate_value({"x": 1}, schema)


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", (*CASE_IDS, *REPAIR_CASE_IDS))
async def test_fixed_requests_and_faults_use_the_existing_executor(case_id, tmp_path):
    for broken in (False, True):
        doc, target, registry, env, transport, task, draft, item = case_inputs(case_id, broken)
        plan = draft.scenario()
        assert isinstance(GenericPlannerDraft.model_validate_json(draft.model_dump_json()),
                          GenericPlannerDraft)
        report = await Executor(target, load_openapi(doc), ReportStore(tmp_path), transport,
                                env, registry).run(plan)
        assert report.outcome == (Outcome.ASSERTION_FAILED if broken else Outcome.PASS)
        if broken:
            assert any(c.selector == item["failed_selector"] and not c.passed
                       for s in report.steps for c in s.checks)
        assert report.model_call is None and report.task_satisfied is None


@pytest.mark.asyncio
async def test_wrong_nested_literal_is_blocked_by_intent_before_requests(tmp_path):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.task_contract import task_validator

    doc, target, registry, env, _, task, draft, _ = case_inputs("J801")
    plan = draft.scenario()
    plan.steps[0].body["config"].value["enabled"] = 1
    calls = []
    transport = httpx.MockTransport(lambda req: calls.append(req))
    report = await workflow.Workflow(doc, target, registry,
        workflow.FakeLLM(plan.model_dump_json()), ReportStore(tmp_path), transport, env,
        user_task=task.user_task, intent_validator=task_validator(
            task, load_openapi(doc), target, registry)).run()
    assert report.outcome != Outcome.PASS and not calls
    assert report.task_satisfied is False


@pytest.mark.asyncio
async def test_generic_adapter_rejects_invalid_container_reply_without_raw_content():
    from agenttest.llm_planner import PlannerFailure

    doc, target, registry, _, _, task, draft, _ = case_inputs("J801")
    data = json.loads(draft.model_dump_json())
    data["steps"][0]["arguments"][1]["value"] = {"object": [
        {"key": "private-fragment", "value": 1}, {"key": "private-fragment", "value": 2}]}
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    planner = OpenAICompatiblePlanner(ModelSettings(model="offline",
        base_url="https://offline.invalid/v1", api_key="synthetic-credential"),
        httpx.MockTransport(lambda req: httpx.Response(200, json=response)), profile="generic")
    with pytest.raises(PlannerFailure) as error:
        await planner.generate(planning_context(load_openapi(doc), target, registry,
                                                task.user_task, {}))
    assert error.value.reason == "MODEL_INVALID_RESPONSE" and planner.stats.calls == 1
    assert planner.stats.diagnostic.code == "MODEL_SCHEMA_GENERIC_DUPLICATE_MEMBER"
    assert "private-fragment" not in planner.stats.model_dump_json()


@pytest.mark.asyncio
async def test_nested_plan_can_be_exported_and_replayed_as_a_fixed_regression(tmp_path):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.regression import RegressionRuntime, export_regression, run_regression
    from agenttest.task_contract import task_validator

    doc, target, registry, env, transport, task, draft, _ = case_inputs("J802")
    plan, store = draft.scenario(), ReportStore(tmp_path / "source")
    report = await workflow.Workflow(doc, target, registry,
        workflow.FakeLLM(plan.model_dump_json()), store, transport, env, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(doc), target, registry)).run()
    assert report.outcome == Outcome.PASS
    runtime = RegressionRuntime(doc, target, registry, task, env, None, store)
    output = tmp_path / "bundle"
    export_regression(output, runtime, plan, report)
    runtime.transport = case_inputs("J802")[4]
    result = await run_regression(output, runtime)
    assert result.outcome == Outcome.PASS and result.task_satisfied is None
    assert result.model_call is None


@pytest.mark.asyncio
async def test_development_archive_keeps_nine_positions_and_can_be_cold_scored():
    pytest.importorskip("agenttest.workflow")
    from uuid import uuid4

    from agenttest.demo import PROJECT_ROOT
    from agenttest.json_literal_check import check_literals, verify_literals

    output = PROJECT_ROOT / "runs" / ("pytest-json-literals-" + uuid4().hex)
    result = await check_literals(output)
    assert result["generation_successes"] == result["controls_passed"] == 3
    assert result["paired_detections"] == 3 and result["real_model_calls"] == 0
    saved = json.loads((output / "results.json").read_bytes())
    assert result["usage_status"] == "SIMULATED" and len(saved["results"]) == 9
    assert verify_literals(output)["rescored_runs"] == 9
