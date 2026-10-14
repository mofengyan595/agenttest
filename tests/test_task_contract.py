import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.dsl import ArgumentBinding
from agenttest.llm_planner import GENERIC_PROMPT, ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.portability_demo import EXAMPLES, project_inputs, task_inputs
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.task_contract import task_validator


@pytest.mark.parametrize("project,name", [
    ("courses", "read"), ("courses", "ownership"), ("tickets", "read"),
    ("tickets", "ownership"), ("tickets", "close"),
])
def test_same_validator_uses_only_project_configuration(project, name):
    _, document, config, registry, _ = project_inputs(project)
    task, draft = task_inputs(project, name)
    validate = task_validator(task, load_openapi(document), config, registry)
    validate(task.user_task, draft.scenario())
    with pytest.raises(NeedsReview, match="INTENT_TASK_CONTRACT_TASK_MISMATCH"):
        validate(task.user_task + "额外目标", draft.scenario())


@pytest.mark.parametrize("changed", ["contract", "target", "policies"])
def test_task_contract_binds_all_trusted_inputs(changed):
    _, document, config, registry, _ = project_inputs("tickets")
    task, _ = task_inputs("tickets", "read")
    contract = load_openapi(document)
    if changed == "contract":
        contract.operations[0].operation_id = "changedOperation"
    elif changed == "target":
        config.max_requests += 1
    else:
        registry = PolicyRegistry(registry.definitions()[:-1])
    with pytest.raises(ValueError, match="trusted project inputs"):
        task_validator(task, contract, config, registry)


def test_validator_snapshots_requirements_and_does_not_repair_plan():
    _, document, config, registry, _ = project_inputs("tickets")
    task, draft = task_inputs("tickets", "read")
    validate = task_validator(task, load_openapi(document), config, registry)
    task.requirements.steps.clear()
    plan = draft.scenario()
    plan.steps[0].body["subject"] = ArgumentBinding(value="篡改主题")
    before = plan.model_dump_json()
    with pytest.raises(NeedsReview, match="INTENT_TASK_CONTRACT_PLAN_MISMATCH"):
        validate(task.user_task, plan)
    assert plan.model_dump_json() == before


def test_generic_request_has_full_catalog_and_no_task_contract_or_domain_recipe():
    assert "course" not in GENERIC_PROMPT.lower() and "ticket" not in GENERIC_PROMPT.lower()
    for project in ("courses", "tickets"):
        _, document, config, registry, _ = project_inputs(project)
        task, _ = task_inputs(project, "read")
        context = planning_context(load_openapi(document), config, registry, task.user_task, {})
        adapter = OpenAICompatiblePlanner(ModelSettings(model="offline-model",
            base_url="https://offline.invalid/v1", api_key="synthetic-fixture-key"),
            profile="generic")
        payload = json.loads(adapter.prepare_request(context))
        public = json.loads(payload["messages"][1]["content"])
        assert {o["id"] for o in public["operations"]} == set(config.allowed_operations)
        assert not {"requirements", "expected", "before", "task_contract"} & public.keys()
        assert "base_url" not in json.dumps(public) and "TICKET_OWNER_BEARER" not in str(public)
        assert payload["messages"][0]["content"] == GENERIC_PROMPT
        assert adapter.stats.calls == 0


@pytest.mark.asyncio
async def test_arbitrary_operation_policy_role_names_need_no_agent_code_changes(tmp_path):
    workflow = pytest.importorskip("agenttest.workflow")
    app, document, config, registry, environ = project_inputs("tickets")
    task, draft = task_inputs("tickets", "read")
    raw = json.loads(document)
    operations = {name: "opaque_" + str(i) for i, name in enumerate(config.allowed_operations)}
    roles = {"owner": "author_alias", "visitor": "reader_alias"}
    policies = {p.id: "rule_" + str(i) for i, p in enumerate(registry.definitions())}
    for path in raw["paths"].values():
        for method, op in path.items():
            if method in {"post", "get", "patch"}:
                op["operationId"] = operations[op["operationId"]]
    contract = load_openapi(json.dumps(raw))
    config.allowed_operations = list(operations.values())
    config.roles = {roles[k]: v for k, v in config.roles.items()}
    definitions = registry.definitions()
    for policy in definitions:
        policy.id = policies[policy.id]
        policy.operation_id = operations[policy.operation_id]
        policy.roles = [roles[r] for r in policy.roles]
        for rule in policy.binding_rules:
            if rule.source_policy_id:
                rule.source_policy_id = policies[rule.source_policy_id]
    registry = PolicyRegistry(definitions)
    for expected in task.requirements.steps:
        expected.operation_id = operations[expected.operation_id]
        expected.role = roles[expected.role]
        expected.policy_id = policies[expected.policy_id]
    for step in draft.steps:
        step.operation_id = operations[step.operation_id]
        step.role = roles[step.role]
        step.policy_id = policies[step.policy_id]
    task.contract_sha256 = fingerprint(contract.model_dump())
    task.target_config_sha256 = fingerprint(config.model_dump())
    task.policy_sha256 = registry.digest()
    response = {"choices": [{"finish_reason": "stop", "message": {
        "content": draft.model_dump_json()}}], "usage": {
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    planner = OpenAICompatiblePlanner(ModelSettings(model="offline-model",
        base_url="https://offline.invalid/v1", api_key="synthetic-fixture-key"),
        httpx.MockTransport(lambda request: httpx.Response(200, json=response)), profile="generic")
    report = await workflow.Workflow(json.dumps(raw).encode(), config, registry, planner,
        ReportStore(tmp_path), httpx.ASGITransport(app), environ, user_task=task.user_task,
        intent_validator=task_validator(task, contract, config, registry)).run()
    assert report.outcome == Outcome.PASS and report.task_satisfied is True
    assert [s.operation_id for s in report.steps] == [
        operations['openTicket'], operations['readTicket']]


def test_cli_loads_generic_task_contract_before_model_or_business_calls(
    monkeypatch, tmp_path, capsys,
):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.__main__ import main

    app, _, _, _, environ = project_inputs("tickets")
    original = workflow.Workflow

    def offline(*args, **kwargs):
        assert kwargs["intent_validator"] is not None
        return original(*args, transport=httpx.ASGITransport(app), environ=environ, **kwargs)

    monkeypatch.setattr(workflow, "Workflow", offline)
    directory = EXAMPLES / "tickets"
    task = task_inputs("tickets", "read")[0]
    arguments = ["agent", "--contract", str(directory / "openapi.json"), "--target",
                 str(directory / "target.json"), "--policies", str(directory / "policies.json"),
                 "--task-contract", str(directory / "read.task.json"), "--fake-plan",
                 str(tmp_path / "plan.json"), "--user-task", task.user_task,
                 "--runs-dir", str(tmp_path / "reports")]
    (tmp_path / "plan.json").write_text(
        task_inputs("tickets", "read")[1].scenario().model_dump_json(), encoding="utf-8")
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "PASS"
    arguments[arguments.index(task.user_task)] = "另一个未经绑定的任务"
    assert main(arguments) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "INVALID_INPUT_OR_REPORT_IO"


@pytest.mark.asyncio
async def test_task_contract_is_additional_to_builtin_mini_gate(tmp_path):
    workflow = pytest.importorskip("agenttest.workflow")
    from agenttest.natural_demo import natural_inputs

    app, config, registry, environ = natural_inputs()
    task, draft = task_inputs("courses", "read")
    task.contract_sha256 = fingerprint(load_openapi(json.dumps(app.openapi())).model_dump())
    task.target_config_sha256 = fingerprint(config.model_dump())
    task.policy_sha256 = registry.digest()
    task.requirements = task_inputs("courses", "ownership")[0].requirements
    report = await workflow.Workflow(json.dumps(app.openapi()).encode(), config, registry,
        workflow.FakeLLM(draft.scenario().model_dump_json()), ReportStore(tmp_path),
        httpx.ASGITransport(app), environ, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(json.dumps(app.openapi())),
                                        config, registry)).run()
    assert report.diagnostic.code == "INTENT_TASK_CONTRACT_PLAN_MISMATCH"
    assert report.outcome == Outcome.NEEDS_REVIEW and report.tool_calls == 0
