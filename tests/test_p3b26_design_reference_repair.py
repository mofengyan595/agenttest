"""The trusted repair reaches the actual legacy Design request once per group."""

import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from test_p3b24_references import consumer, context, reject, step
from test_product_design import settings
from test_product_supervisor import lifecycle_project
from test_product_tasks import response

from agenttest.contracts import load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.goal_suite import SuiteLedger
from agenttest.plan_construction import construct_design
from agenttest.product_agent import AgentSession
from agenttest.product_checks import reference_repair_view
from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_tasks import ProductTasks, RunScope
from agenttest.report import ReportStore
from agenttest.task_store import TaskStore


def replace_project():
    base, setup = lifecycle_project()
    document = json.loads(base.document)
    create = document["paths"]["/parcels"]["post"]
    resource = document["paths"]["/parcels/{parcelId}"]
    resource["put"] = dict(operationId="replaceParcel", summary="替换包裹",
        parameters=resource["get"]["parameters"], requestBody=create["requestBody"],
        responses={"200": dict(description="updated"), "422": dict(description="invalid")})
    setup = setup.model_dump()
    create_config = next(item for item in setup["operations"]
                         if item["operation_id"] == "createParcel")
    setup["operations"].append(dict(operation_id="replaceParcel", alias="替换包裹", statuses=[200],
        arguments=[dict(location="path_parameters", name="parcelId", selector="$.id",
                        source_operation="createParcel"), *create_config["arguments"]]))
    setup = GenericSetup.model_validate(dict(setup, approve_negative_tests=True))
    encoded = json.dumps(document)
    return build_generic_project("parcels", encoded, load_openapi(encoded), setup)


def new_session(tmp_path, project, transport, obligations, target_transport):
    db = TaskStore(tmp_path / "tasks.sqlite3")
    svc = SimpleNamespace(db=db, reports=ReportStore(tmp_path / "reports"), environ={},
        designer_factory=lambda _: DesignModel(settings(), profile="generic", transport=transport),
        intake=SimpleNamespace(record=lambda _: {"generic": {"roles": []}}))
    group = dict(id="negative_batch_5", primary=["replaceParcel"],
                 operations=["createParcel", "replaceParcel"], policy_ids=obligations,
                 supported=True)
    text = "发版前请完整验收整个项目，正常路径和非法入参都按已批准的规则执行。"
    summary = dict(groups=[group], cases=[], design_records=[], questions=[], not_covered=[],
                   inventory=dict(total=len(project.contract.operations)),
                   focus=dict(mode="selected"))
    scope = RunScope(svc=svc, task_id="task_1", owner="operator", row={"project_id": "parcels"},
        project=project, project_record={}, summary=summary, context={"current_request": text},
        text=text, ledger=SuiteLedger(120, 64, 650_000), transport=target_transport,
        guard=lambda: None, checkpoint=lambda: None, executable_checks=[], planning_ir=None,
        requirements=[])
    return AgentSession(scope, ProductTasks(svc)), group


@pytest.mark.asyncio
@pytest.mark.parametrize("first_shape_error", [False, True])
async def test_real_legacy_design_encoding_ignores_conflicting_agent_note(tmp_path,
                                                                  first_shape_error):
    project = replace_project()
    policies = project.registry.definitions()
    producer = next(p.id for p in policies if p.operation_id == "createParcel" and not p.negative)
    negative = next(p.id for p in policies if p.operation_id == "replaceParcel" and p.negative)
    bodies = []
    marker = "unapproved-free-text Authorization: Bearer sk-test-repair-secret-only"

    def model(request):
        bodies.append(json.loads(request.content))
        if first_shape_error and len(bodies) == 1:
            return response(dict(not_covered=False))
        check = step("negative_replace", "replaceParcel", negative, "missing_seed")
        return response(dict(interpretation="验证批准的替换负向义务", relevant=True, cases=[],
                             questions=[], not_covered=[], required_cases={negative: dict(
                                 title="替换负向", before=[], check=check, after=[])}))

    def no_target(_):
        pytest.fail("The Design rejection must never reach business HTTP")

    session, group = new_session(tmp_path, project, httpx.MockTransport(model), [negative],
                                 httpx.MockTransport(no_target))
    group_id = group["id"]
    if first_shape_error:
        shape = await session.run_group(group_id)
        assert shape["error"]["code"] == "DESIGN_OMISSION_SHAPE_INVALID"
    initial = await session.run_group(group_id, "按批准的绑定与清理规则验证")
    assert initial["error"]["code"] == "CONSTRUCTION_REFERENCE_INVALID"
    assert initial["error"]["detail"]["allowed_from_steps"] == []
    repaired = await session.run_group(group_id, "单步执行，禁止 from_step，不用前置。" + marker)
    assert repaired["error"]["code"] == "CONSTRUCTION_REFERENCE_INVALID"
    blocked = await session.run_group(group_id, "再换一个模型备注")
    assert blocked["error"]["code"] == ("GROUP_RUN_LIMIT" if first_shape_error
                                       else "REPAIR_NOTE_REQUIRED")
    assert len(bodies) == (3 if first_shape_error else 2)
    assert session.ledger.model_calls == len(bodies) and session.ledger.requests == 0
    assert session.repair_runs == {group_id: 1}
    starts = [row for row in session.tool_calls if row["phase"] == "start"]
    assert [row["runtime_repair"] for row in starts] == [False] * (len(bodies) - 1) + [True]
    public = [json.loads(body["messages"][1]["content"]) for body in bodies]
    assert all("reference_repair" not in body for body in public[:-1])
    repair = public[-1]["reference_repair"]
    assert repair["code"] == "CONSTRUCTION_REFERENCE_INVALID"
    assert repair["fields"] == ["cases.0.plan.steps.0.arguments.0.from_step"]
    assert repair["detail"] == dict(reference="SOURCE_UNKNOWN", case_index=0,
        step_id="negative_replace", from_step="missing_seed", select="$.id",
        obligation_id=negative, allowed_from_steps=[], direct_from_steps=[],
        reordered_from_steps=[], binding=dict(location="path_parameters", name="parcelId"))
    assert repair["approved_producers"] == [dict(operation_id="createParcel", policy_id=producer,
                                                role=None, selector="$.id")]
    assert "before" in repair["hint"] and "同一用例" in repair["hint"]
    assert all(token not in json.dumps(bodies[-1], ensure_ascii=False) for token in (
        "禁止 from_step", "不用前置", "unapproved-free-text", "Authorization: Bearer",
        "sk-test-repair-secret-only", settings().api_key.get_secret_value()))


def test_projected_feedback_keeps_declared_order_separate_from_approval_and_drops_raw_values():
    ctx = context()
    ctx.update(requested_checks=[dict(policy_id="negative_variant")], operations=[
        dict(id="createParcel", method="POST"), dict(id="replaceParcel", method="PUT")])
    diagnostic = reject([consumer("typo"), step("later_seed")], ctx).model_dump(mode="json")
    diagnostic["detail"]["rejected_binding"]["from_step"] = "sk-synthetic-not-for-model"
    diagnostic["detail"].update(body={"draft": "raw-value"}, reason="unreviewed note")
    view = reference_repair_view(ctx, diagnostic, ["createParcel", "replaceParcel"], {None})
    assert view["detail"]["allowed_from_steps"] == ["later_seed"]
    assert view["detail"]["direct_from_steps"] == []
    assert view["detail"]["reordered_from_steps"] == ["later_seed"]
    assert view["approved_producers"] == [dict(
        operation_id="createParcel", policy_id="createParcel", role=None, selector="$.id")]
    assert all(token not in json.dumps(view) for token in (
        "sk-synthetic-not-for-model", "raw-value", "unreviewed note"))
    no_binding = deepcopy(ctx)
    no_binding["policies"][0].pop("binding_rules")
    no_reference = reject([consumer("missing")], no_binding).model_dump(mode="json")
    missing = reference_repair_view(no_binding, no_reference, ["replaceParcel"], {None})
    assert missing["detail"]["allowed_from_steps"] == []
    assert missing["approved_producers"] == []


@pytest.mark.parametrize("deleted", [False, True])
def test_other_cases_cannot_supply_a_producer_even_if_the_id_matches(deleted):
    ctx = context()
    seed = step("seed")
    steps = [seed]
    if deleted:
        ctx["policies"].append(dict(id="deleteParcel", operation_id="deleteParcel", roles=[None]))
        steps.append(step("delete", "deleteParcel", "deleteParcel", source="seed"))
    wire = dict(cases=[dict(title="前一个独立用例", plan=dict(name="seed", steps=steps)),
                       dict(title="替换", plan=dict(name="replace", steps=[consumer("seed")]))])
    with pytest.raises(ValidationFailure, match="CONSTRUCTION_REFERENCE_INVALID") as caught:
        construct_design(wire, ctx, construction_chains=[(0, [s["id"] for s in steps]),
                                                        (1, [consumer("seed")["id"]])])
    d = caught.value.diagnostic
    assert d.fields == ["cases.1.plan.steps.0.arguments.0.from_step"]
    assert d.detail["reference"] == "SOURCE_UNKNOWN" and d.detail["allowed_from_steps"] == []
