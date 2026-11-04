"""Credentials stay private through normal project compilation and execution."""

import json

import httpx
import pytest
from test_sensitive_intake import DOCUMENT, setup

from agenttest.contracts import load_openapi
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.generic_intake import build_generic_project
from agenttest.planner_input import planning_context
from agenttest.project_intake import IntakeInput
from agenttest.project_inventory import inventory
from agenttest.report import Outcome, ReportStore
from agenttest.safety import ResourceCreation
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


def configured():
    config = setup()
    item = config.operations[0]
    item.credential_inputs = dict(username="AGENTTEST_TARGET_NEW_NAME",
                                  password="AGENTTEST_TARGET_NEW_PASSWORD")
    for argument in item.arguments:
        argument.value, argument.needs_input = None, False
    return config


ENV = dict(AGENTTEST_TARGET_NEW_NAME="synthetic_member_147",
           AGENTTEST_TARGET_NEW_PASSWORD="synthetic-only-credential-147")


@pytest.mark.asyncio
@pytest.mark.parametrize("response_status,owned", [(201, True), (422, False)])
async def test_negative_creation_records_unexpected_new_resource_without_changing_grade(
        tmp_path, response_status, owned):
    from test_product_design import case, project

    from agenttest.product_design import DesignCase, validate_design_case

    proj, _ = project(negative=True)
    proj.target.resource_creations["insertJob"] = ResourceCreation(
        id_selector="$.id", fresh_resource_basis="Synthetic new jobs from this test only")
    policy = next(p for p in proj.registry.definitions() if p.negative
                  and p.negative.name == "quantity" and p.negative.kind == "boundary_outside")
    store = ReportStore(tmp_path)
    plan = validate_design_case(proj, DesignCase.model_validate(case(policy=policy.id)),
                                store, environ={})
    requests = []

    def target(request):
        requests.append(request)
        return httpx.Response(response_status, json=dict(id="unexpected-fresh-job"))

    report = await Executor(proj.target, proj.contract, store, httpx.MockTransport(target),
                            {}, proj.registry).run(plan)
    assert len(requests) == 1
    assert report.outcome == (Outcome.ASSERTION_FAILED if owned else Outcome.PASS)
    receipt = report.created_resources[0]
    assert receipt["negative_policy_id"] == policy.id
    assert receipt["status"] == ("OWNED" if owned else "NOT_CONFIRMED_CREATED")
    assert receipt["resource_id"] == ("unexpected-fresh-job" if owned else None)
    assert receipt["cleanup_result"] == "NOT_PERFORMED"


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind,status", [("created", "OWNED"),
    ("unknown", "CREATE_OUTCOME_UNKNOWN"), ("secret_id", "RESOURCE_ID_UNVERIFIED")])
async def test_creation_receipt_keeps_safe_id_and_unknown_write_is_not_retried(tmp_path,
                                                                           result_kind, status):
    config = configured()
    config.operations[0].creation = ResourceCreation(id_selector="$.data.id",
        fresh_resource_basis="Operator confirms POST creates a new exclusive synthetic member")
    contract = load_openapi(DOCUMENT)
    project = build_generic_project("web_receipt", DOCUMENT, contract, config, environ=ENV)
    plan = Scenario.model_validate(dict(name="登记资源归属", steps=[dict(id="register",
        operation_id="registerMember", policy_id=project.registry.definitions()[0].id)]))
    sent = []

    def target(request):
        sent.append(request)
        if result_kind == "unknown":
            raise httpx.ReadTimeout("unknown write result")
        return httpx.Response(201, json=dict(data=dict(id=ENV["AGENTTEST_TARGET_NEW_PASSWORD"]
            if result_kind == "secret_id" else 817,
            username=ENV["AGENTTEST_TARGET_NEW_NAME"],
            password=ENV["AGENTTEST_TARGET_NEW_PASSWORD"])))

    report = await Executor(project.target, contract, ReportStore(tmp_path),
        httpx.MockTransport(target), ENV, project.registry).run(plan)
    assert len(sent) == 1
    receipt = report.created_resources[0]
    assert receipt["status"] == status and receipt["cleanup_result"] == "NOT_PERFORMED"
    assert receipt["resource_id"] == (817 if result_kind == "created" else None)
    if result_kind == "created":
        assert receipt["owner_run_id"] == report.run_id
        assert receipt["response_sha256"] == report.steps[0].attempts[0].response_sha256
    text = report.model_dump_json()
    assert all(value not in text for value in ENV.values())


@pytest.mark.asyncio
async def test_private_registration_compiles_executes_and_scrubs_response_and_evidence(tmp_path):
    contract = load_openapi(DOCUMENT)
    config = configured()
    assert inventory(contract, config, ENV)["operations"][0]["state"] == "READY"
    project = build_generic_project("web_members", DOCUMENT, contract, config, environ=ENV)
    policy = project.registry.definitions()[0]
    assert policy.binding_rules == []
    plan = Scenario.model_validate(dict(name="注册专用成员", steps=[dict(id="register",
        operation_id="registerMember", policy_id=policy.id)]))
    observed = []

    def target(request):
        payload = json.loads(request.content)
        observed.append(payload)
        return httpx.Response(201, json=dict(success=True, echoed=payload["username"],
                                            nested=[payload["password"]]))

    report = await Executor(project.target, contract, ReportStore(tmp_path),
        httpx.MockTransport(target), ENV, project.registry).run(plan)
    assert report.outcome == Outcome.PASS
    assert observed == [dict(username=ENV["AGENTTEST_TARGET_NEW_NAME"],
                             password=ENV["AGENTTEST_TARGET_NEW_PASSWORD"])]
    text = report.model_dump_json() + json.dumps(planning_context(
        contract, project.target, project.registry, "注册测试成员", {}))
    assert not any(value in text for value in ENV.values())
    assert report.steps[0].attempts[0].status_code == 201


def test_missing_credentials_and_plan_supplied_credentials_fail_before_request(tmp_path):
    contract, config = load_openapi(DOCUMENT), configured()
    assert inventory(contract, config, {})["operations"][0]["state"] == "NEEDS_INPUT"
    config.assisted = False
    with pytest.raises(ValueError, match="CREDENTIAL_MISSING"):
        build_generic_project("web_members", DOCUMENT, contract, config, environ={})
    project = build_generic_project("web_members", DOCUMENT, contract, config, environ=ENV)
    plan = Scenario.model_validate(dict(name="不得注入", steps=[dict(id="register",
        operation_id="registerMember", policy_id=project.registry.definitions()[0].id,
        body=dict(username=dict(value="model-picked")))]))
    with pytest.raises(ValueError, match="EXECUTOR_OWNED"):
        Executor(project.target, contract, ReportStore(tmp_path), environ=ENV,
                 registry=project.registry).validate(plan)


@pytest.mark.asyncio
async def test_draft_rejects_controlled_ordinary_values_and_credential_aliases_as_public_prose(
        tmp_path):
    service = TaskService({}, TaskStore(tmp_path / "tasks.sqlite3"), ReportStore(tmp_path),
        lambda _: None, generic_intake=True, environ=ENV)
    try:
        config = configured()
        config.operations[0].arguments[0].value = ENV["AGENTTEST_TARGET_NEW_NAME"]
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            service.intake.save(IntakeInput(name="成员", document=DOCUMENT, generic=config), "op")
        config = configured()
        with pytest.raises(ValueError, match="PROJECT_CREDENTIAL_LITERAL"):
            service.intake.save(IntakeInput(name=ENV["AGENTTEST_TARGET_NEW_NAME"],
                document=DOCUMENT, generic=config), "op")
        assert not service.intake.records()
    finally:
        await service.close()
