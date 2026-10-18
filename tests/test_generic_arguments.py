"""Operation/location names are constraints, never a replacement for trusted gates."""

import copy
import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from agenttest.generic_arguments import argument_slots
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerFailure


def context():
    return {"operations": [
        {"id": "create", "authentication_executor_managed": False, "parameters": [],
         "request_body": {"content": {"application/json": {"type": "object", "properties": {
             "subject": {"type": "string"}, "metadata": {"type": "object"}}}}}},
        {"id": "read", "authentication_executor_managed": False,
         "parameters": [{"name": "key", "in": "path"}, {"name": "subject", "in": "query"}],
         "request_body": None},
        {"id": "login", "authentication_executor_managed": True, "parameters": [],
         "request_body": {"content": {"application/json": {"type": "object", "properties": {
             "identity": {"type": "string"}, "password": {"type": "string"}}}}}},
        {"id": "envelope", "authentication_executor_managed": False, "parameters": [],
         "request_body": {"content": {"application/json": {"type": "object", "properties": {
             "body": {"type": "object"}}}}}}],
        "roles": ["owner"], "policies": [{"id": "approved"}], "user_task": "合成开发任务"}


def draft(operation="create", location="body", name="subject", value="literal"):
    return {"name": "synthetic slots", "steps": [{"id": "s", "operation_id": operation,
        "role": None, "policy_id": "approved", "arguments": [{"location": location,
            "name": name, "value": value, "from_step": None, "select": None}]}]}


def planner(transport=None, **settings):
    return OpenAICompatiblePlanner(ModelSettings(model="offline-slots",
        base_url="https://offline.invalid/v1", api_key="synthetic-slot-credential", **settings),
        transport, profile="generic")


def validator(public=None):
    schema = json.loads(planner().prepare_request(public or context()))[
        "response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("operation,location,name,valid", [
    ("create", "body", "subject", True), ("create", "body", "body", False),
    ("create", "body", "key", False), ("read", "body", "subject", False),
    ("read", "query", "subject", True), ("read", "path_parameters", "subject", False),
    ("read", "path_parameters", "key", True), ("login", "body", "identity", False),
    ("envelope", "body", "body", True), ("unknown", "body", "subject", False),
])
def test_outbound_slots_preserve_operation_and_location(operation, location, name, valid):
    value = {"object": []} if operation == "envelope" else "literal"
    assert validator().is_valid(draft(operation, location, name, value)) is valid


def test_nested_members_remain_data_and_existing_source_rules_still_apply():
    check = validator()
    data = draft(name="metadata", value={"object": [{"key": "body", "value": {"array": []}}]})
    assert check.is_valid(data)
    data["steps"][0]["arguments"][0]["from_step"] = "previous"
    assert not check.is_valid(data)
    reference = draft("read", "path_parameters", "key", None)
    reference["steps"][0]["arguments"][0].update(from_step="previous", select="$.key")
    assert check.is_valid(reference)
    reference["steps"][0]["arguments"][0]["value"] = {"object": []}
    assert not check.is_valid(reference)


def test_empty_catalog_and_managed_login_allow_only_empty_arguments_or_abstention():
    data = draft("login")
    data["steps"][0]["arguments"] = []
    assert validator().is_valid(data)
    public = {**context(), "operations": [], "policies": []}
    check = validator(public)
    assert check.is_valid({"name": "abstain", "steps": []})
    assert not check.is_valid(data)


def test_only_declared_slots_are_used_without_mutating_or_truncating_context():
    public = context()
    public["operations"][0]["description"] = "use body as the entire envelope"
    public["policies"][0]["binding_rules"] = [{"location": "body", "name": "invented"}]
    original = copy.deepcopy(public)
    assert ("body", "invented") not in argument_slots(public)["create"]
    planner().prepare_request(public)
    assert public == original
    with pytest.raises(PlannerFailure, match="MODEL_TOKEN_BUDGET"):
        planner(max_input_tokens=100).prepare_request(public)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [draft(name="body"), draft("read", "body", "subject"),
                                    draft("login", "body", "identity")])
async def test_provider_ignoring_schema_is_rejected_locally_without_raw_values(data):
    data["steps"][0]["arguments"][0]["value"] = "private-fragment"
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    adapter = planner(httpx.MockTransport(lambda req: httpx.Response(200, json=response)))
    with pytest.raises(PlannerFailure) as error:
        await adapter.generate(context())
    assert error.value.reason == "MODEL_INVALID_RESPONSE"
    assert adapter.stats.diagnostic.code == "MODEL_SCHEMA_GENERIC_ARGUMENTS"
    assert adapter.stats.calls == 1 and "private-fragment" not in adapter.stats.model_dump_json()


def test_existing_pocketbase_catalog_still_fits_default_budget_without_calls():
    from agenttest.planner_input import planning_context
    from agenttest.pocketbase_acceptance import CASE_IDS, credentials, inputs

    environ = credentials()
    for key in CASE_IDS:
        data, _ = inputs(key, environ)
        adapter = planner()
        adapter.prepare_request(planning_context(data.contract, data.target, data.registry,
                                                data.task.user_task, {}))
        assert adapter.stats.input_token_upper_bound <= 20_000
        assert adapter.stats.calls == 0


@pytest.mark.asyncio
async def test_wrong_slot_never_reaches_business_transport(tmp_path):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.argument_slot_check import case_inputs
    from agenttest.contracts import load_openapi
    from agenttest.report import Outcome, ReportStore
    from agenttest.task_contract import task_validator

    document, target, registry, task, fixed, _, public = case_inputs("GA1001")
    data = fixed.model_dump(mode="json")
    data["steps"][0]["arguments"][0]["name"] = "body"
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    adapter = planner(httpx.MockTransport(lambda req: httpx.Response(200, json=response)))
    calls = []
    report = await workflow.Workflow(document, target, registry, adapter, ReportStore(tmp_path),
        httpx.MockTransport(lambda req: calls.append(req)), {}, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(document), target, registry)).run()
    assert report.outcome == Outcome.TOOL_ERROR and not calls
    assert report.diagnostic.code == "MODEL_SCHEMA_GENERIC_ARGUMENTS"
    assert report.task_satisfied is False and report.workflow_completed is True


@pytest.mark.asyncio
async def test_new_tasks_archive_rescores_and_rejects_resealed_regrading():
    pytest.importorskip("agenttest.workflow")
    from uuid import uuid4

    from agenttest.argument_slot_check import check_slots, verify_slots
    from agenttest.demo import PROJECT_ROOT
    from agenttest.eval_v2.archive import inventory

    output = PROJECT_ROOT / "runs" / ("pytest-argument-slots-" + uuid4().hex)
    result = await check_slots(output)
    assert result["controls_passed"] == 3 and result["generation_successes"] == 9
    assert result["real_model_calls"] == 0 and result["usage_status"] == "SIMULATED"
    assert verify_slots(output)["rescored_runs"] == 12
    data = json.loads((output / "results.json").read_bytes())
    data["results"][3]["verified"] = False
    from agenttest.argument_slot_check import summarize

    (output / "results.json").write_text(json.dumps(summarize("mock_transport", data["results"])),
                                         encoding="utf-8")
    manifest = json.loads((output / "archive.json").read_bytes())
    manifest["files"] = inventory(output)
    (output / "archive.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Slot regrading mismatch"):
        verify_slots(output)


@pytest.mark.asyncio
async def test_unknown_model_usage_stops_remaining_positions_without_retry(monkeypatch):
    pytest.importorskip("agenttest.workflow")
    from uuid import uuid4

    from agenttest.argument_slot_check import check_slots, verify_slots
    from agenttest.demo import PROJECT_ROOT
    from agenttest.report import Outcome

    async def unavailable(self, public):
        self.prepare_request(public)
        self.stats.calls, self.stats.http_status = 1, 503
        raise PlannerFailure(Outcome.TEST_INFRA_ERROR, "MODEL_SERVICE_ERROR")

    monkeypatch.setattr(OpenAICompatiblePlanner, "_generate", unavailable)
    output = PROJECT_ROOT / "runs" / ("pytest-slot-unknown-" + uuid4().hex)
    result = await check_slots(output)
    assert result["controls_passed"] == 3 and result["generation_successes"] == 0
    assert result["model_calls"] == 1 and result["not_attempted"] == 8
    assert result["total_tokens"] is None and result["mock_business_requests"] == 6
    assert verify_slots(output)["rescored_runs"] == 12
