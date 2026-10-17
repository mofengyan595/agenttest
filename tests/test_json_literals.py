"""Generic structured containers stay data and retain exact recursive JSON types."""

import json
import math

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.dsl import AssertionPolicy
from agenttest.executor import Executor
from agenttest.json_literal_check import CASE_IDS, case_inputs
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
            "from_step": None, "select": None, "json_value": None, **changes}


@pytest.mark.parametrize("value", [None, False, True, 0, 7, 0.125, -0.375, "原文",
                                   {}, [], {"nested": [False, 1, 1.5, None]},
                                   [{"note": "保留\"原样\"", "empty": []}]])
def test_scalar_and_container_types_survive_conversion_exactly(value):
    data = argument(value=None if type(value) in {dict, list} else value,
        json_value=json.dumps(value, ensure_ascii=False) if type(value) in {dict, list} else None)
    parsed = GenericDraftArgument.model_validate(data)
    assert json_equal(parsed.literal_value(), value)
    assert fingerprint(parsed.literal_value()) == fingerprint(value)


@pytest.mark.parametrize("changes", [
    {"json_value": "{bad"}, {"json_value": '{"x":1,"x":2}'},
    {"json_value": '{"x":NaN}'}, {"json_value": '[1e309]'},
    {"json_value": "null"}, {"json_value": "1.25"}, {"json_value": '"text"'},
    {"json_value": "{}", "value": "second source"},
    {"json_value": "{}", "from_step": "created", "select": "$.payload"},
    {"json_value": "{}", "location": "query"},
    {"json_value": "[" * 42 + "0" + "]" * 42},
    {"json_value": '["' + "x" * 20_000 + '"]'},
    {"value": math.nan}, {"value": math.inf},
])
def test_invalid_ambiguous_nonfinite_or_unbounded_json_is_rejected(changes):
    with pytest.raises(ValueError):
        GenericDraftArgument.model_validate(argument(**changes))


def test_existing_scalar_replies_and_non_generic_profiles_keep_their_contract():
    legacy = argument(value="original")
    legacy.pop("json_value")
    assert GenericDraftArgument.model_validate(legacy).literal_value() == "original"
    with pytest.raises(ValueError):
        DraftArgument.model_validate({**legacy, "value": 0.125})
    doc, target, registry, _, _, task, _, _ = case_inputs("J801")
    context = planning_context(load_openapi(doc), target, registry, task.user_task, {})
    settings = ModelSettings(model="offline", base_url="https://offline.invalid/v1", api_key="fake")
    generic = json.loads(OpenAICompatiblePlanner(
        settings, profile="generic").prepare_request(context))
    schema = generic["response_format"]["json_schema"]["schema"]
    for definition in schema["$defs"].values():
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])
    prop = schema["$defs"]["GenericDraftArgument"]["properties"]["json_value"]
    assert "default" not in prop and prop["anyOf"][0]["type"] == "string"
    assert schema["$defs"]["GenericDraftStep"]["properties"]["role"]["enum"] == [None]
    original = json.loads(OpenAICompatiblePlanner(settings).prepare_request(context))
    original_schema = original["response_format"]["json_schema"]["schema"]
    assert "GenericDraftArgument" not in original_schema["$defs"]


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
@pytest.mark.parametrize("case_id", CASE_IDS)
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
    data["steps"][0]["arguments"][1]["json_value"] = '{"private-fragment":NaN}'
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    planner = OpenAICompatiblePlanner(ModelSettings(model="offline",
        base_url="https://offline.invalid/v1", api_key="synthetic-credential"),
        httpx.MockTransport(lambda req: httpx.Response(200, json=response)), profile="generic")
    with pytest.raises(PlannerFailure) as error:
        await planner.generate(planning_context(load_openapi(doc), target, registry,
                                                task.user_task, {}))
    assert error.value.reason == "MODEL_INVALID_RESPONSE" and planner.stats.calls == 1
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
