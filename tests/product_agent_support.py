"""Deterministic product-agent test fixtures shared by base and agent profiles."""

import json
from types import SimpleNamespace

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.goal_suite import SuiteLedger
from agenttest.product_agent import AgentSession
from agenttest.report import ReportStore

FUNCTIONAL_GOAL = "帮我测一下包裹管理功能。"


def obligation_summary(*operations):
    return dict(groups=[dict(id="group_1", primary=list(operations), operations=list(operations),
                            supported=True)],
                inventory=dict(total=len(operations), operations=[
                    dict(operation_id=name, state="READY", reason="", next_step="")
                    for name in operations]), cases=[])


def lifecycle_project():
    """Creation, read and deletion of one resource, linked like the real parcels contract."""
    parcel_id = [dict(name="parcelId", **{"in": "path"}, required=True,
                      schema=dict(type="string"))]
    create = dict(operationId="createParcel", summary="创建包裹",
        requestBody=dict(required=True, content={"application/json": dict(schema=dict(
            type="object", required=["name", "quantity"], properties=dict(
                name=dict(type="string", minLength=1, maxLength=20),
                quantity=dict(type="integer", minimum=1, maximum=3))))}),
        responses={
            "201": dict(description="created", links=dict(
                read=dict(operationId="readParcel",
                          parameters=dict(parcelId="$response.body#/id")),
                remove=dict(operationId="deleteParcel",
                            parameters=dict(parcelId="$response.body#/id")))),
            "422": dict(description="invalid parameters")})
    read = dict(operationId="readParcel", summary="查询包裹", parameters=parcel_id,
                responses={"200": dict(description="ok"), "404": dict(description="missing")})
    delete = dict(operationId="deleteParcel", summary="删除包裹", parameters=parcel_id,
                  responses={"204": dict(description="deleted"),
                             "404": dict(description="missing")})
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="Parcels API", version="1"),
        paths={"/parcels": {"post": create},
               "/parcels/{parcelId}": {"get": read, "delete": delete}}))
    contract = load_openapi(document)
    suggestions = analyze_contract(contract)
    operations = [dict(operation_id=s["operation_id"], alias=s["alias"], statuses=s["statuses"],
        arguments=[{k: v for k, v in a.items() if k in {"location", "name", "value",
            "source_operation", "selector", "generated", "needs_input"}}
            for a in s["arguments"]]) for s in suggestions]
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998",
        isolated_confirmed=True, allow_mutations=True, assisted=True, operations=operations))
    return build_generic_project("parcels", document, contract, setup), setup


def service(tmp_path):
    return SimpleNamespace(reports=ReportStore(tmp_path), environ={},
                           _shutting_down=False, model_status=lambda: "CONFIGURED_MODEL")


class FakeTasks:
    """The two deterministic actions the runtime may call, with no HTTP at all."""

    DEFAULT_ROUND = "安排一轮测试"

    def __init__(self, effect=None):
        self.effect = effect
        self.calls = []
        self.rounds = 0

    def next_supervision_round(self, scope):
        self.rounds += 1
        return self.rounds

    async def run_group(self, scope, group, *, position=None, future_groups=(),
                        supervision=None):
        self.calls.append(dict(kind="group", group_id=group["id"],
                               supervision=supervision))
        if self.effect is not None:
            self.effect(scope.summary, group, supervision)

    async def supervise_design(self, scope, candidate):
        self.calls.append(dict(kind="design", candidate=candidate["id"]))
        return SimpleNamespace(executed=True, cases=1, requests=2, assertions=1,
                               detail=dict(planned=1), note=None)

    async def supervise_read(self, scope, candidate):
        self.calls.append(dict(kind="probe", candidate=candidate["id"]))
        scope.state.setdefault("probes", []).append(
            dict(policy_id=candidate["policies"][0], operation_id=candidate["operations"][0],
                 run_id="probe-run", outcome="PASS",
                 checks=[dict(kind="status", selector="status", expected=200, actual=200,
                              passed=True)]))
        return SimpleNamespace(executed=True, cases=0, requests=1, assertions=1,
                               detail=dict(planned=1), note=None)


def evidence(summary, group, policies):
    """One executed case per approved policy, exactly the shape the executor writes."""
    summary["design_records"].append(dict(group_id=group["id"], status="DESIGNED",
                                          reason="EXECUTED", planning_engine="deterministic"))
    summary["cases"].append(dict(
        case_id="case_" + group["id"], run_id="run_" + group["id"], status="FINISHED",
        outcome="PASS", executed_operations=list(group["primary"]),
        executed_steps=len(policies), planned_steps=len(policies), http_requests=2,
        executed_policy_ids=list(policies),
        evidence=[dict(operation_id="createParcel", policy_id=policy_id, executed=True,
                       outcome="PASS", checks=[dict(kind="status", selector="status",
                                                    expected=201, actual=201, passed=True)],
                       attempts=[dict(status_code=201)])
                  for policy_id in policies]))
    group["status"] = "DESIGNED"


def session(tmp_path, text=FUNCTIONAL_GOAL, *, effect=None, state=None,
            ledger=None, guard=None):
    project, _ = lifecycle_project()
    policies = [policy.id for policy in project.registry.definitions() if not policy.negative]
    summary = obligation_summary("createParcel", "readParcel", "deleteParcel")
    summary.update(cases=[], design_records=[], questions=[], not_covered=[],
                   focus=dict(mode="all_ready"), budget=dict(max_requests=24))
    scope = SimpleNamespace(svc=service(tmp_path), project=project, summary=summary,
                            text=text, state=state if state is not None else {},
                            ledger=ledger or SuiteLedger(24, 8, 100_000),
                            guard=guard or (lambda: None), max_seconds=60.0,
                            task_id="task_1")
    effect = effect or (lambda summary, group, supervision: evidence(summary, group, policies))
    tasks = FakeTasks(effect)
    return AgentSession(scope=scope, tasks=tasks), tasks, project, policies, summary
