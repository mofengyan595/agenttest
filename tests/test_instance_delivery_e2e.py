"""Deterministic M2/F1 delivery: instance bindings, construction, executor, HTTP, report.

No model, no network: the target is a stateful ``httpx.MockTransport`` handler. Every test runs
the production path — instance bindings, ``construct_plan``, the design gate, DSL and policy
validation, dynamic argument resolution, the executor, response verification, the lifecycle
journal and report persistence — and asserts the actual HTTP sequence, not only the outcome.
"""

import asyncio
import json
from functools import lru_cache

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.dsl import FieldAssertion
from agenttest.executor import Executor
from agenttest.generic_intake import (
    GenericSetup,
    IntakeExpectation,
    IntakeOperation,
    build_generic_project,
)
from agenttest.llm_planner import GenericPlannerDraft
from agenttest.plan_construction import construct_plan
from agenttest.product_design import TestDesign as Design
from agenttest.report import Outcome, ReportStore
from agenttest.request_requirements import RequestRequirement, validate_plan_requirements
from agenttest.safety import ResourceCreation, ResourceLifecycle

APPROVED = ("createItem", "readItem", "updateItem", "replaceItem", "deleteItem")
CREATION_BASIS = "隔离模拟靶场为本轮创建独立条目，不会返回既有条目。"

DOCUMENT = dict(openapi="3.0.3", info=dict(title="Items CRUD", version="1"), paths={
    "/items": {"post": dict(operationId="createItem", summary="创建条目",
        requestBody=dict(required=True, content={"application/json": dict(schema={
            "type": "object", "required": ["name"],
            "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 20}}})}),
        responses={"201": dict(description="created", links=dict(read=dict(
            operationId="readItem", parameters=dict(itemId="$response.body#/id"))))})},
    "/items/{itemId}": {
        "get": dict(operationId="readItem", summary="读取条目",
            parameters=[dict(name="itemId", **{"in": "path"}, required=True,
                             schema=dict(type="string"))],
            responses={"200": dict(description="ok"), "404": dict(description="missing")}),
        "patch": dict(operationId="updateItem", summary="更新条目",
            parameters=[dict(name="itemId", **{"in": "path"}, required=True,
                             schema=dict(type="string"))],
            requestBody=dict(required=True, content={"application/json": dict(schema={
                "type": "object", "required": ["name"],
                "properties": {"name": {"type": "string"}}})}),
            responses={"200": dict(description="ok"), "404": dict(description="missing")}),
        "put": dict(operationId="replaceItem", summary="替换条目",
            parameters=[dict(name="itemId", **{"in": "path"}, required=True,
                             schema=dict(type="string"))],
            requestBody=dict(required=True, content={"application/json": dict(schema={
                "type": "object", "required": ["name"],
                "properties": {"name": {"type": "string"}}})}),
            responses={"200": dict(description="ok"), "404": dict(description="missing")}),
        "delete": dict(operationId="deleteItem", summary="删除条目",
            parameters=[dict(name="itemId", **{"in": "path"}, required=True,
                             schema=dict(type="string"))],
            responses={"204": dict(description="gone"), "404": dict(description="missing")}),
    },
})


def path_argument():
    return dict(location="path_parameters", name="itemId",
                source_operation="createItem", selector="$.id")


def setup():
    def checks(select, expected):
        return [FieldAssertion(select=select, operator="equals", expected=expected)]

    return GenericSetup(base_url="http://127.0.0.1:18996", isolated_confirmed=True,
        assisted=True, approve_negative_tests=True, allow_mutations=True, roles=[],
        resource_lifecycles=[ResourceLifecycle(create_operation="createItem",
            delete_operation="deleteItem", id_selector="$.id", path_parameter="itemId",
            fresh_resource_basis=CREATION_BASIS)],
        operations=[
            IntakeOperation(operation_id="createItem", alias="创建条目", statuses=[201],
                arguments=[dict(location="body", name="name", value="first")],
                creation=ResourceCreation(id_selector="$.id",
                                          fresh_resource_basis=CREATION_BASIS)),
            IntakeOperation(operation_id="readItem", alias="读取条目", statuses=[200],
                arguments=[path_argument()], checks=checks("$.name", "first"),
                check_source="操作员确认的隔离模拟断言。",
                expectations=[IntakeExpectation(statuses=[404],
                    rule="删除后回读返回 404，说明资源确实已不存在。")]),
            IntakeOperation(operation_id="updateItem", alias="更新条目", statuses=[200],
                arguments=[path_argument(),
                           dict(location="body", name="name", value="renamed")],
                checks=checks("$.name", "renamed"),
                check_source="操作员确认的隔离模拟断言。"),
            IntakeOperation(operation_id="replaceItem", alias="替换条目", statuses=[200],
                arguments=[path_argument(),
                           dict(location="body", name="name", value="replaced")],
                checks=checks("$.name", "replaced"),
                check_source="操作员确认的隔离模拟断言。"),
            IntakeOperation(operation_id="deleteItem", alias="删除条目", statuses=[204],
                arguments=[path_argument()]),
        ])


@lru_cache(maxsize=1)
def project():
    document = json.dumps(DOCUMENT, ensure_ascii=False)
    return build_generic_project("items", document, load_openapi(document), setup())


def clause(source_id, operation, quote=None, before=()):
    return dict(source_id=source_id, source_quote=quote or source_id, disposition="required",
                actions=[dict(operation_id=operation, role=None)], before=list(before),
                same_resource=[], response_checks=[], forbidden_operation_ids=[],
                blocked_operation_ids=[])


def order(earlier, later, earlier_source, later_source):
    return dict(earlier=earlier, later=later, earlier_source_id=earlier_source,
                earlier_index=0, later_source_id=later_source, later_index=0)


def requirements(first_operation="readItem"):
    return [
        clause("request_1", "createItem", "先创建一个条目。"),
        clause("request_2", first_operation, "再读取该条目。",
               before=[order("createItem", first_operation, "request_1", "request_2")]),
    ]


def draft(key, operation, policy_id, source_id, *, index=0, path=True, name=None):
    arguments = []
    if path:
        arguments.append(dict(location="path_parameters", name="itemId", value=None,
                              from_step="c", select="$.id"))
    if name is not None:
        arguments.append(dict(location="body", name="name", value=name,
                              from_step=None, select=None))
    return dict(id=key, operation_id=operation, role=None, policy_id=policy_id,
                arguments=arguments, requirement_source_id=source_id,
                requirement_action_index=index)


def m2():
    requirements_ = [
        clause("request_1", "createItem", "先创建一个条目。"),
        clause("request_2", "readItem", "再读取该条目。",
               before=[order("createItem", "readItem", "request_1", "request_2")]),
        clause("request_3", "deleteItem", "删除该条目。",
               before=[order("readItem", "deleteItem", "request_2", "request_3")]),
        clause("request_4", "readItem", "删除后再读取一次，确认已经不存在。",
               before=[order("deleteItem", "readItem", "request_3", "request_4")]),
    ]
    steps = [
        draft("c", "createItem", "web_rule_1", "request_1", path=False, name="first"),
        draft("read_before", "readItem", "web_rule_2", "request_2"),
        draft("d", "deleteItem", "web_rule_5", "request_3"),
        draft("read_after", "readItem", "web_rule_2_expectation_1", "request_4"),
    ]
    return requirements_, steps


def f1():
    requirements_ = [
        clause("request_1", "createItem", "先创建一个条目。"),
        clause("request_2", "readItem", "读取该条目。",
               before=[order("createItem", "readItem", "request_1", "request_2")]),
        clause("request_3", "updateItem", "修改条目名称。",
               before=[order("readItem", "updateItem", "request_2", "request_3")]),
        clause("request_4", "replaceItem", "整体替换该条目。",
               before=[order("updateItem", "replaceItem", "request_3", "request_4")]),
        clause("request_5", "deleteItem", "删除该条目。",
               before=[order("replaceItem", "deleteItem", "request_4", "request_5")]),
        clause("request_6", "readItem", "删除后再读取一次，确认已经不存在。",
               before=[order("deleteItem", "readItem", "request_5", "request_6")]),
    ]
    steps = [
        draft("c", "createItem", "web_rule_1", "request_1", path=False, name="first"),
        draft("r1", "readItem", "web_rule_2", "request_2"),
        draft("p", "updateItem", "web_rule_3", "request_3", name="renamed"),
        draft("u", "replaceItem", "web_rule_4", "request_4", name="replaced"),
        draft("d", "deleteItem", "web_rule_5", "request_5"),
        draft("r2", "readItem", "web_rule_2_expectation_1", "request_6"),
    ]
    return requirements_, steps


def context(requirements_):
    proj = project()
    return dict(
        policies=[dict(id=policy.id, operation_id=policy.operation_id, roles=policy.roles,
                       negative_test=policy.negative.kind if policy.negative else None)
                  for policy in proj.registry.definitions()],
        operations=[dict(id=operation.operation_id, method=operation.method)
                    for operation in proj.contract.operations],
        request_requirements=requirements_,
        request_atoms=[dict(source_id=item["source_id"], start=index * 10,
                            end=index * 10 + 5, text=item["source_quote"], ordered=True,
                            same_resource=False, constraint=False, future=False,
                            future_only=False)
                       for index, item in enumerate(requirements_)])


def construct(requirements_, steps):
    """The production chain up to a DSL scenario: design gate, then instance construction."""
    design = Design(interpretation="按原文实例顺序执行", cases=[dict(
        title="闭环", kind="flow", plan=dict(name="闭环", steps=steps))],
        questions=[], not_covered=[])
    validate_plan_requirements(design, [RequestRequirement.model_validate(item)
                                        for item in requirements_], list(APPROVED))
    built, stats = construct_plan(dict(name="闭环", steps=steps), context(requirements_))
    return built, stats, GenericPlannerDraft.model_validate(built).scenario()


class CrudTarget:
    """A stateful isolated CRUD target that records every request and can inject one fault."""

    def __init__(self, fault=None, entered=None):
        self.resources, self.calls, self.bodies = {}, [], []
        self.fault, self.entered = fault, entered

    async def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        if request.method == "POST":
            body = json.loads(request.content or b"{}")
            self.bodies.append(body)
            key = f"item_{len(self.bodies)}"
            self.resources[key] = dict(id=key, name=body["name"])
            return httpx.Response(201, json=self.resources[key])
        key = request.url.path.rsplit("/", 1)[1]
        if self.fault is not None:
            override = self.fault(request, self, key)
            if override is not None:
                return override
        if self.entered is not None and request.method == "GET":
            self.entered.set()
            await asyncio.Event().wait()
        if request.method == "DELETE":
            if key not in self.resources:
                return httpx.Response(404, json={"error": "not found"})
            del self.resources[key]
            return httpx.Response(204)
        if key not in self.resources:
            return httpx.Response(404, json={"error": "not found"})
        if request.method in {"PATCH", "PUT"}:
            body = json.loads(request.content or b"{}")
            self.bodies.append(body)
            self.resources[key]["name"] = body["name"]
            return httpx.Response(200, json=self.resources[key])
        return httpx.Response(200, json=self.resources[key])


async def deliver(tmp_path, scenario, target):
    proj = project()
    executor = Executor(proj.target, proj.contract, ReportStore(tmp_path),
                        httpx.MockTransport(target), {}, proj.registry)
    return await executor.run(scenario)


def stop_calls(handler_holder):
    return handler_holder.calls


def test_the_minimal_crud_plan_orders_instances_and_explains_each_edge():
    requirements_, steps = m2()
    built, stats, scenario = construct(requirements_, steps)
    assert [step["id"] for step in built["steps"]] == ["c", "read_before", "d", "read_after"]
    assert stats["node_dependencies"]["read_after"] == ["c", "d"]
    # The deletion's empty 204 body is never a data source: the readback id comes from CREATE
    # and only the written order puts it after DELETE.
    assert stats["edge_reasons"]["read_after<=c"] == "MODEL_REFERENCE"
    assert stats["edge_reasons"]["read_after<=d"] == "TEXT_ORDER"
    assert [step.id for step in scenario.steps] == ["c", "read_before", "d", "read_after"]
    assert scenario.steps[3].policy_id == "web_rule_2_expectation_1"


@pytest.mark.asyncio
async def test_m2_posts_reads_deletes_and_reads_the_absence(tmp_path):
    requirements_, steps = m2()
    built, stats, scenario = construct(requirements_, steps)
    target = CrudTarget()
    report = await deliver(tmp_path, scenario, target)

    assert report.outcome == Outcome.PASS and report.completed
    assert report.cleanup == "本轮资源已清理。"
    # One created id is reused by every later request, including the post-delete readback.
    assert target.calls == [("POST", "/items"), ("GET", "/items/item_1"),
                            ("DELETE", "/items/item_1"), ("GET", "/items/item_1")]
    assert [step.step_id for step in report.steps] == ["c", "read_before", "d", "read_after"]
    assert [step.attempts[-1].status_code for step in report.steps] == [201, 200, 204, 404]
    assert all(step.outcome == Outcome.PASS for step in report.steps)
    # The 404 readback is verified as the expected outcome, not treated as an ordinary failure.
    readback = report.steps[-1]
    assert readback.checks[0].kind == "http_status" and readback.checks[0].passed
    assert readback.negative_rejected_as_expected is None
    assert target.resources == {}
    row = report.resource_lifecycle[0]
    assert row["status"] == "CLEANED" and row["cleanup_result"] == "PASS"
    assert row["resource_id"] == "item_1" and row["cleanup_step"] == "d"
    assert report.created_resources[0]["resource_id"] == "item_1"
    assert report.lifecycle_cleanup == []
    assert report.cleanup_error is None
    # Request accounting covers every step attempt and no hidden request.
    attempts = sum(len(step.attempts) for step in report.steps)
    assert attempts == len(target.calls)
    assert all(step.attempts[-1].response_sha256 for step in report.steps)
    assert report.scenario_sha256 and report.contract_sha256 and report.target_config_sha256


@pytest.mark.asyncio
async def test_f1_runs_the_full_crud_chain_against_one_resource(tmp_path):
    requirements_, steps = f1()
    built, stats, scenario = construct(requirements_, steps)
    assert [step["id"] for step in built["steps"]] == ["c", "r1", "p", "u", "d", "r2"]
    target = CrudTarget()
    report = await deliver(tmp_path, scenario, target)

    assert report.outcome == Outcome.PASS
    assert target.calls == [("POST", "/items"), ("GET", "/items/item_1"),
                            ("PATCH", "/items/item_1"), ("PUT", "/items/item_1"),
                            ("DELETE", "/items/item_1"), ("GET", "/items/item_1")]
    assert [step.attempts[-1].status_code for step in report.steps] == [201, 200, 200, 200, 204,
                                                                       404]
    assert [step.step_id for step in report.steps] == ["c", "r1", "p", "u", "d", "r2"]
    assert target.bodies[1] == {"name": "renamed"} and target.bodies[2] == {"name": "replaced"}
    assert all(check.passed for step in report.steps for check in step.checks)
    assert [len(step.checks) for step in report.steps] == [1, 2, 2, 2, 1, 1]
    assert target.resources == {}
    assert report.resource_lifecycle[0]["status"] == "CLEANED"
    assert report.cleanup == "本轮资源已清理。" and report.cleanup_error is None


def arrangements(steps, orders):
    return [[steps[index] for index in order] for order in orders]


M2_ARRANGEMENTS = ((0, 1, 2, 3), (3, 2, 1, 0), (2, 0, 3, 1), (1, 3, 0, 2))
F1_ARRANGEMENTS = ((0, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 0), (4, 1, 5, 0, 3, 2),
                   (2, 5, 0, 4, 1, 3))


@pytest.mark.parametrize("order_index", range(len(M2_ARRANGEMENTS)))
def test_every_m2_array_arrangement_constructs_the_same_semantics(order_index):
    requirements_, steps = m2()
    canonical, canonical_stats, _ = construct(requirements_, steps)
    shuffled = arrangements(steps, [M2_ARRANGEMENTS[order_index]])[0]
    built, stats, scenario = construct(requirements_, shuffled)
    assert [step["id"] for step in built["steps"]] == [step["id"] for step in
                                                       canonical["steps"]]
    assert stats["node_dependencies"] == canonical_stats["node_dependencies"]
    assert stats["edge_reasons"] == canonical_stats["edge_reasons"]
    assert [step.id for step in scenario.steps] == [step["id"] for step in canonical["steps"]]


@pytest.mark.parametrize("order_index", range(len(F1_ARRANGEMENTS)))
def test_every_f1_array_arrangement_constructs_the_same_semantics(order_index):
    requirements_, steps = f1()
    canonical, canonical_stats, _ = construct(requirements_, steps)
    shuffled = arrangements(steps, [F1_ARRANGEMENTS[order_index]])[0]
    built, stats, _ = construct(requirements_, shuffled)
    assert [step["id"] for step in built["steps"]] == [step["id"] for step in
                                                       canonical["steps"]]
    assert stats["node_dependencies"] == canonical_stats["node_dependencies"]


@pytest.mark.asyncio
async def test_a_shuffled_proposal_sends_the_same_http_requests(tmp_path):
    requirements_, steps = m2()
    shuffled = arrangements(steps, [(2, 0, 3, 1)])[0]
    _, _, scenario = construct(requirements_, shuffled)
    target = CrudTarget()
    report = await deliver(tmp_path, scenario, target)
    assert report.outcome == Outcome.PASS
    assert target.calls == [("POST", "/items"), ("GET", "/items/item_1"),
                            ("DELETE", "/items/item_1"), ("GET", "/items/item_1")]


@pytest.mark.asyncio
async def test_a_read_timeout_after_creation_still_cleans_the_owned_resource(tmp_path):
    requirements_, steps = m2()
    _, _, scenario = construct(requirements_, steps)

    def timeout(request, target, key):
        if request.method == "GET":
            raise httpx.ReadTimeout("slow read")
        return None

    target = CrudTarget(fault=timeout)
    report = await deliver(tmp_path, scenario, target)
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert [step.step_id for step in report.steps] == ["c", "read_before"]
    assert report.steps[-1].reason == "ReadTimeout"
    # Cleanup ran once, on the already-approved deletion, and did not rewrite the failure.
    assert target.calls == [("POST", "/items"), ("GET", "/items/item_1"),
                            ("DELETE", "/items/item_1")]
    assert report.lifecycle_cleanup[0].step_id == "d"
    assert report.lifecycle_cleanup[0].outcome == Outcome.PASS
    assert report.cleanup == "本轮资源已清理。" and report.cleanup_error is None
    assert report.resource_lifecycle[0]["status"] == "CLEANED"
    assert target.resources == {}


@pytest.mark.asyncio
async def test_a_failed_assertion_cleans_up_without_becoming_a_business_success(tmp_path):
    requirements_, steps = f1()
    _, _, scenario = construct(requirements_, steps)

    def wrong_patch(request, target, key):
        if request.method == "PATCH":
            return httpx.Response(200, json=dict(id=key, name="wrong"))
        return None

    target = CrudTarget(fault=wrong_patch)
    report = await deliver(tmp_path, scenario, target)
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert [step.step_id for step in report.steps] == ["c", "r1", "p"]
    assert report.steps[-1].outcome == Outcome.ASSERTION_FAILED
    assert [check.passed for check in report.steps[-1].checks] == [True, False]
    # The approved deletion runs as cleanup, once, and the business failure stays visible.
    assert target.calls == [("POST", "/items"), ("GET", "/items/item_1"),
                            ("PATCH", "/items/item_1"), ("DELETE", "/items/item_1")]
    assert report.lifecycle_cleanup[0].step_id == "d"
    assert report.resource_lifecycle[0]["status"] == "CLEANED"
    assert report.cleanup == "本轮资源已清理。"
    assert target.resources == {}


@pytest.mark.asyncio
async def test_a_delete_500_is_a_known_failure_and_is_not_replayed(tmp_path):
    requirements_, steps = m2()
    _, _, scenario = construct(requirements_, steps)

    def delete_500(request, target, key):
        if request.method == "DELETE":
            return httpx.Response(500, json={"error": "boom"})
        return None

    target = CrudTarget(fault=delete_500)
    report = await deliver(tmp_path, scenario, target)
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert [step.step_id for step in report.steps] == ["c", "read_before", "d"]
    assert [step.attempts[-1].status_code for step in report.steps] == [201, 200, 500]
    assert [method for method, _ in target.calls].count("DELETE") == 1
    assert report.lifecycle_cleanup == []
    row = report.resource_lifecycle[0]
    assert row["status"] == "CLEANUP_PENDING" and row["cleanup_result"] == "ASSERTION_FAILED"
    assert "清理未完成" in report.cleanup and "不自动重放" in report.cleanup
    assert report.cleanup_error is None
    assert list(target.resources) == ["item_1"]


@pytest.mark.asyncio
async def test_a_delete_with_unknown_outcome_keeps_the_resource_and_never_retries(tmp_path):
    requirements_, steps = m2()
    _, _, scenario = construct(requirements_, steps)

    def delete_unknown(request, target, key):
        if request.method == "DELETE":
            raise httpx.ReadError("connection lost after send")
        return None

    target = CrudTarget(fault=delete_unknown)
    report = await deliver(tmp_path, scenario, target)
    assert report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.steps[-1].reason == "ReadError"
    assert report.steps[-1].attempts[-1].error_code == "ReadError"
    assert [method for method, _ in target.calls].count("DELETE") == 1
    row = report.resource_lifecycle[0]
    assert row["status"] == "CLEANUP_OUTCOME_UNKNOWN"
    assert row["cleanup_result"] == "OUTCOME_UNKNOWN"
    assert "清理未完成" in report.cleanup and "不自动重放" in report.cleanup
    assert report.lifecycle_cleanup == []
    assert list(target.resources) == ["item_1"]


@pytest.mark.asyncio
async def test_cancellation_after_creation_keeps_the_resource_and_sends_no_cleanup(tmp_path):
    requirements_, steps = m2()
    _, _, scenario = construct(requirements_, steps)
    entered = asyncio.Event()
    target = CrudTarget(entered=entered)
    proj = project()
    store = ReportStore(tmp_path)
    executor = Executor(proj.target, proj.contract, store, httpx.MockTransport(target), {},
                        proj.registry)
    task = asyncio.create_task(executor.run(scenario))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = store.read(next(tmp_path.glob("*/report.json")).parent.name)
    assert report.completed and report.outcome == Outcome.TEST_INFRA_ERROR
    assert report.reason == "RUN_CANCELLED"
    assert [step.step_id for step in report.steps] == ["c", "read_before"]
    # Cancellation never turns into a hidden write, and the resource is kept for the operator.
    assert [method for method, _ in target.calls] == ["POST", "GET"]
    row = report.resource_lifecycle[0]
    assert row["status"] == "OWNED" and row["cleanup_result"] == "NOT_SENT"
    assert "清理未完成" in report.cleanup and "不自动重放" in report.cleanup
    assert list(target.resources) == ["item_1"]


def test_illegal_plans_stop_before_any_http_request():
    requirements_, steps = m2()
    target = CrudTarget()

    def refused(requirements_for_case, steps_for_case, code):
        with pytest.raises(ValidationFailure) as error:
            construct_plan(dict(name="闭环", steps=steps_for_case),
                           context(requirements_for_case))
        assert error.value.diagnostic.code == code

    # A real cycle: the written order says read-before-create while the read needs the id.
    cycle = [clause("request_1", "createItem", "创建条目。"),
             clause("request_2", "readItem", "先读取再创建。",
                    before=[order("readItem", "createItem", "request_2", "request_1")])]
    refused(cycle, [draft("c", "createItem", "web_rule_1", "request_1", path=False,
                          name="first"),
                    draft("r", "readItem", "web_rule_2", "request_2")],
            "CONSTRUCTION_DEPENDENCY_CYCLE")

    # Two read instances with no trusted mapping: stop instead of guessing an order.
    ambiguous = [clause("request_1", "readItem", "读取一次。"),
                 clause("request_2", "readItem", "再读取一次。",
                        before=[order("readItem", "readItem", "request_1", "request_2")])]
    unbound = [dict(id="r1", operation_id="readItem", role=None, policy_id="web_rule_2",
                    arguments=[], requirement_source_id="request_1",
                    requirement_action_index=0)]
    refused(ambiguous, unbound, "CONSTRUCTION_ORDER_INSTANCE_UNBOUND")

    # An identity that this operation was never approved for is refused at construction.
    refused(requirements_, [dict(draft("c", "createItem", "web_rule_1", "request_1",
                                       path=False, name="first"), role="owner")],
            "CONSTRUCTION_NODE_NOT_APPROVED")

    # An unapproved policy id cannot become an execution either.
    refused(requirements_, [draft("c", "createItem", "web_rule_9", "request_1", path=False,
                                  name="first")], "CONSTRUCTION_NODE_NOT_APPROVED")
    assert target.calls == []
