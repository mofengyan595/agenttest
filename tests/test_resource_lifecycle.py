"""Ownership and no replay boundaries for fresh model-disabled write regressions."""

import asyncio
import json

import httpx
import pytest
from test_product_design import project

from agenttest.contracts import load_openapi
from agenttest.dsl import ArgumentBinding, FieldAssertion, Scenario
from agenttest.executor import Executor
from agenttest.fixed_plan import FixedPlanSnapshot, replay_scope
from agenttest.generic_intake import IntakeOperation, build_generic_project
from agenttest.lifecycle import lifecycle_plan
from agenttest.regression import RegressionRuntime, export_regression, run_regression
from agenttest.report import Outcome, ReportStore
from agenttest.safety import ResourceLifecycle


def lifecycle_project():
    original, setup = project()
    document = json.loads(original.document)
    document["paths"]["/jobs/{jobId}"]["delete"] = dict(operationId="removeJob",
        parameters=[dict(name="jobId", **{"in": "path"}, required=True,
                         schema=dict(type="string"))], responses={"204": dict(description="gone")})
    setup.operations[1].checks = [FieldAssertion(select="$.state", operator="equals",
                                               expected="ready")]
    setup.operations.append(IntakeOperation(operation_id="removeJob", alias="清理任务",
        statuses=[204], arguments=[dict(location="path_parameters", name="jobId",
                                       source_operation="insertJob", selector="$.id")]))
    setup.resource_lifecycles = [ResourceLifecycle(create_operation="insertJob",
        delete_operation="removeJob", id_selector="$.id", path_parameter="jobId",
        fresh_resource_basis="隔离靶场保证每次成功创建分配全新资源，不返回已有资源。")]
    raw = json.dumps(document)
    return build_generic_project("jobs", raw, load_openapi(raw), setup), setup


def plan():
    return Scenario.model_validate(dict(name="fresh lifecycle", steps=[
        dict(id="create", operation_id="insertJob", policy_id="web_rule_1", body=dict(
            name=dict(value="regression"), quantity=dict(value=2),
            details=dict(value=dict(labels=["a"])))),
        dict(id="read", operation_id="readJob", policy_id="web_rule_2",
             path_parameters=dict(jobId=dict(from_step="create", select="$.id"))),
        dict(id="delete", operation_id="removeJob", policy_id="web_rule_3",
             path_parameters=dict(jobId=dict(from_step="create", select="$.id")))]))


def executor(tmp_path, handler, proj=None):
    proj = proj or lifecycle_project()[0]
    return Executor(proj.target, proj.contract, ReportStore(tmp_path),
                    httpx.MockTransport(handler), {}, proj.registry)


@pytest.mark.asyncio
async def test_fresh_runs_cleanup_only_their_resource_and_export_without_model(tmp_path):
    pytest.importorskip("langgraph")
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    proj, _ = lifecycle_project()
    resources, ids, calls = {"foreign": "ready"}, [], []

    def target(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            key = "job_" + str(len(ids))
            ids.append(key)
            resources[key] = "ready"
            return httpx.Response(201, json=dict(id=key))
        key = request.url.path.rsplit("/", 1)[1]
        if request.method == "DELETE":
            del resources[key]
            return httpx.Response(204)
        return httpx.Response(200, json=dict(state=resources[key]))

    scenario, store = plan(), ReportStore(tmp_path)
    report = await Workflow(proj.document, proj.target, proj.registry, FakeLLM(""), store,
        transport=httpx.MockTransport(target), environ={}, task_mode="model_designed",
        user_task="创建读取清理", prepared_plan=scenario,
        budget=WorkflowBudget(max_model_calls=0)).run()
    assert report.outcome == Outcome.PASS and report.workflow_completed
    snapshot = FixedPlanSnapshot(user_task="创建读取清理", source_run_id=report.run_id,
        scenario_sha256=report.scenario_sha256, contract_sha256=report.contract_sha256,
        target_config_sha256=report.target_config_sha256, policy_sha256=proj.registry.digest())
    runtime = RegressionRuntime(proj.document, proj.target, proj.registry, snapshot, {},
                                httpx.MockTransport(target), store)
    directory = tmp_path / "fixed"
    export_regression(directory, runtime, scenario, report)
    for _ in range(2):
        repeated = await run_regression(directory, runtime)
        assert repeated.outcome == Outcome.PASS and repeated.planner_calls == 0
        assert repeated.resource_lifecycle[0]["status"] == "CLEANED"
        assert repeated.resource_lifecycle[0]["owner_run_id"] == repeated.run_id
    assert ids == ["job_0", "job_1", "job_2"] and resources == {"foreign": "ready"}
    assert len(calls) == 9
    changed = proj.target.model_copy(deep=True)
    changed.resource_lifecycles.clear()
    runtime.target = changed
    with pytest.raises(ValueError):
        await run_regression(directory, runtime)
    assert len(calls) == 9


@pytest.mark.asyncio
async def test_read_failure_is_preserved_while_owned_cleanup_runs(tmp_path):
    requests = []

    def target(request):
        requests.append(request.method)
        return httpx.Response(201, json=dict(id="fresh")) if request.method == "POST" else (
            httpx.Response(204) if request.method == "DELETE" else
            httpx.Response(200, json=dict(state="broken")))

    report = await executor(tmp_path, target).run(plan())
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert [s.step_id for s in report.steps] == ["create", "read"]
    assert report.steps[-1].checks[-1].passed is False
    assert requests == ["POST", "GET", "DELETE"]
    assert report.lifecycle_cleanup[0].outcome == Outcome.PASS
    assert report.resource_lifecycle[0]["status"] == "CLEANED"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_unknown_create_never_guesses_deletes_or_replays(tmp_path, cancel):
    entered = asyncio.Event()
    calls = []

    async def target(request):
        calls.append(request.method)
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise httpx.ReadTimeout("private transport details")

    run = executor(tmp_path, target)
    task = asyncio.create_task(run.run(plan()))
    await asyncio.wait_for(entered.wait(), timeout=5)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reports = list(tmp_path.glob("*/report.json"))
        report = run.store.read(reports[0].parent.name)
    else:
        report = await task
    assert report.resource_lifecycle[0]["status"] == "CREATE_OUTCOME_UNKNOWN"
    assert report.resource_lifecycle[0]["resource_id"] is None and calls == ["POST"]
    assert "清理未完成" in report.cleanup
    with pytest.raises(ValueError, match="RUN_NOT_RESUMABLE"):
        await executor(tmp_path, target).run(plan(), report=report)
    assert calls == ["POST"]


@pytest.mark.parametrize("change", ["literal", "selector", "role", "no_cleanup", "path"])
def test_incomplete_or_unowned_mutations_cannot_become_write_regressions(change):
    proj, _ = lifecycle_project()
    scenario = plan()
    if change == "literal":
        scenario.steps[-1].path_parameters["jobId"] = ArgumentBinding(value="foreign")
    elif change == "selector":
        scenario.steps[-1].path_parameters["jobId"].select = "$.unrelated"
    elif change == "role":
        scenario.steps[-1].role = "other"
    elif change == "no_cleanup":
        scenario.steps.pop()
    else:
        next(o for o in proj.contract.operations if o.operation_id == "removeJob").path = (
            "/foreign/{jobId}")
    with pytest.raises(ValueError):
        lifecycle_plan(proj.contract, proj.target, scenario, proj.registry)
    assert replay_scope(proj.contract, proj.target, scenario,
                        proj.registry) == "MUTATION_LIFECYCLE_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["failure", "timeout", "cancel_read", "cancel_delete"])
async def test_incomplete_cleanup_keeps_evidence_and_is_not_replayed(tmp_path, mode):
    entered = asyncio.Event()
    calls = []

    async def target(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json=dict(id="only_owned"))
        if (request.method == "GET" and mode == "cancel_read"
                or request.method == "DELETE" and mode == "cancel_delete"):
            entered.set()
            await asyncio.Event().wait()
        if request.method == "GET":
            return httpx.Response(200, json=dict(state="ready"))
        if mode == "timeout":
            raise httpx.ReadTimeout("unknown deletion result")
        return httpx.Response(500)

    run = executor(tmp_path, target)
    task = asyncio.create_task(run.run(plan()))
    if mode.startswith("cancel"):
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        report = run.store.read(next(tmp_path.glob("*/report.json")).parent.name)
    else:
        report = await task
    row = report.resource_lifecycle[0]
    assert row["resource_id"] == "only_owned" and row["owner_run_id"] == report.run_id
    assert row["status"] == {"failure": "CLEANUP_PENDING", "timeout": "CLEANUP_OUTCOME_UNKNOWN",
        "cancel_read": "OWNED", "cancel_delete": "CLEANUP_OUTCOME_UNKNOWN"}[mode]
    assert calls.count("POST") == 1 and calls.count("DELETE") <= 1
    before = list(calls)
    with pytest.raises(ValueError, match="RUN_NOT_RESUMABLE"):
        await executor(tmp_path, target).run(plan(), report=report)
    assert calls == before and "清理未完成" in report.cleanup
