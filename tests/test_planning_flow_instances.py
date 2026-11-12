"""P2.1: a valid multi-instance flow compiles; a real missing dependency still stops.

The P2 failure was not the model. Its proposals were create+delete and
create+read+delete+read-404, but the scoped IR pulled every instance that shared the resource
into the round and the compiler treated ordering-only edges ("the delete runs after the reads")
as hard prerequisites. These tests pin the corrected contract:

* an instance is *required* only by a data/policy/lifecycle edge, never by array order;
* an approved absence rule becomes a bindable post-delete read instance;
* an absence read without its deletion, and a read without its creator, still fail.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agenttest.diagnostics import ValidationFailure  # noqa: E402
from agenttest.plan_construction import construct_plan  # noqa: E402
from agenttest.planning_ir import build_planning_ir  # noqa: E402

OPERATIONS = [
    dict(id="createParcel", method="POST", path="/parcels"),
    dict(id="readParcel", method="GET", path="/parcels/{parcelId}"),
    dict(id="changeParcel", method="PATCH", path="/parcels/{parcelId}"),
    dict(id="replaceParcel", method="PUT", path="/parcels/{parcelId}"),
    dict(id="deleteParcel", method="DELETE", path="/parcels/{parcelId}"),
]
LIFECYCLE = [dict(create_operation="createParcel", delete_operation="deleteParcel",
                  path_parameter="parcelId", id_selector="$.id")]
PATH_RULE = [dict(location="path_parameters", name="parcelId",
                  source_policy_id="createParcel", source_selector="$.id")]
READ_OPERATIONS = {"readParcel"}


def policy(policy_id, operation_id, statuses, rules=(), *, extra=None):
    item = dict(id=policy_id, operation_id=operation_id, roles=[None],
                assertions=dict(reviewed=True, source="operator",
                                status=dict(codes=list(statuses))),
                binding_rules=list(rules), negative=None)
    item.update(extra or {})
    return item


def policies(*, absence=True):
    items = [
        policy("createParcel", "createParcel", [201],
               [dict(location="body", name="name", literal_schema=dict(enum=["demo"]))]),
        policy("readParcel", "readParcel", [200], PATH_RULE),
        policy("changeParcel", "changeParcel", [200], PATH_RULE),
        policy("replaceParcel", "replaceParcel", [200], PATH_RULE),
        policy("deleteParcel", "deleteParcel", [204], PATH_RULE),
    ]
    if absence:
        items.append(policy("readParcel_expectation_404", "readParcel", [404], PATH_RULE))
    return items


def requirement(source_id="request_1"):
    return dict(source_id=source_id, source_quote="创建、读取、修改、替换并删除包裹",
                disposition="required",
                actions=[dict(operation_id=operation, role=None) for operation in
                         ("createParcel", "readParcel", "changeParcel", "replaceParcel",
                          "deleteParcel")],
                before=[], same_resource=[], response_checks=[],
                forbidden_operation_ids=[], blocked_operation_ids=[])


def planner_ir(*, absence=True):
    text = "创建、读取、修改、替换并删除包裹"
    atoms = [dict(source_id="request_1", text=text, start=0)]
    return build_planning_ir([requirement()], atoms, operations=OPERATIONS,
                             policies=policies(absence=absence),
                             target_lifecycles=LIFECYCLE,
                             read_operations=READ_OPERATIONS)


def context(ir, *, absence=True):
    return dict(policies=policies(absence=absence), operations=OPERATIONS,
                authentication=[], planning_ir=ir)


def step(identifier, operation, *, clause=None, index=None, source=None, policy_id=None,
         body=False, path=True):
    arguments = []
    if body:
        arguments.append(dict(location="body", name="name", value="demo", from_step=None,
                              select=None))
    if source is not None and path:
        arguments.append(dict(location="path_parameters", name="parcelId", value=None,
                              from_step=source, select="$.id"))
    node = dict(id=identifier, operation_id=operation, role=None,
                policy_id=policy_id or operation, arguments=arguments)
    if clause is not None:
        node["requirement_source_id"] = clause
        node["requirement_action_index"] = index or 0
    return node


def compile_case(ir, steps, **kwargs):
    plan = dict(name="case", steps=steps)
    return construct_plan(plan, context(ir, **kwargs))


def codes(exc_info):
    return exc_info.value.diagnostic.code


def test_the_ir_derives_the_approved_absence_read_even_without_a_verify_clause():
    ir = planner_ir()
    absent = [item for item in ir.instances if item.phase == "VERIFY_ABSENT"]
    assert len(absent) == 1
    assert absent[0].policy_id == "readParcel_expectation_404"
    assert absent[0].origin == "APPROVED_ABSENCE_POLICY"
    assert any(argument.name == "parcelId" and argument.from_instance
               for argument in absent[0].arguments)
    # The delete->readback dependency is hard, so an absence read can never float alone.
    pairs = ir.edge_pairs()
    delete = next(item.instance_id for item in ir.instances
                  if item.operation_id == "deleteParcel")
    assert {"RESOURCE_ABSENT"} <= {reason.reason
                                   for reason in pairs[(delete, absent[0].instance_id)]}


def test_an_absence_read_is_not_derived_without_an_approved_rule():
    ir = planner_ir(absence=False)
    assert [item for item in ir.instances if item.phase == "VERIFY_ABSENT"] == []


def test_a_create_delete_case_is_accepted_even_if_the_round_also_names_reads():
    ir = planner_ir()
    scoped = ir.scope_to(["createParcel", "deleteParcel"])
    steps = [step("create", "createParcel", clause="request_1", index=0, body=True),
             step("delete", "deleteParcel", clause="request_1", index=4, source="create")]
    plan, stats = compile_case(scoped, steps)
    assert [item["id"] for item in plan["steps"]] == ["create", "delete"]
    assert [item["operation_id"] for item in plan["steps"]] == ["createParcel", "deleteParcel"]
    assert stats["dependency_edges"] >= 1


def test_the_full_lifecycle_with_the_post_delete_404_compiles():
    ir = planner_ir()
    steps = [
        step("create", "createParcel", clause="request_1", index=0, body=True),
        step("read", "readParcel", clause="request_1", index=1, source="create"),
        step("change", "changeParcel", clause="request_1", index=2, source="create"),
        step("replace", "replaceParcel", clause="request_1", index=3, source="create"),
        step("delete", "deleteParcel", clause="request_1", index=4, source="create"),
        step("after", "readParcel", source="create", policy_id="readParcel_expectation_404"),
    ]
    plan, stats = compile_case(ir, steps)
    order = [item["id"] for item in plan["steps"]]
    assert order[-2:] == ["delete", "after"]
    readback = plan["steps"][-1]
    assert readback["policy_id"] == "readParcel_expectation_404"
    assert next(argument for argument in readback["arguments"]
                if argument["name"] == "parcelId")["from_step"] == "create"
    assert stats["instances"][-1].startswith("ai_")


def test_an_absence_read_without_its_delete_is_still_rejected():
    ir = planner_ir()
    steps = [step("create", "createParcel", clause="request_1", index=0, body=True),
             step("after", "readParcel", source="create",
                  policy_id="readParcel_expectation_404")]
    with pytest.raises(ValidationFailure) as caught:
        compile_case(ir, steps)
    assert codes(caught) == "PLANNING_INSTANCE_COVERAGE"
    detail = caught.value.diagnostic.detail
    assert detail["operations"], "错误必须指出缺失实例的操作"
    assert "deleteParcel" in detail["operations"].values()


def test_a_read_without_its_creator_is_still_rejected():
    ir = planner_ir()
    steps = [step("read", "readParcel", clause="request_1", index=1, source="ghost")]
    with pytest.raises(ValidationFailure) as caught:
        compile_case(ir, steps)
    assert codes(caught) == "PLANNING_INSTANCE_COVERAGE"
    assert "createParcel" in caught.value.diagnostic.detail["operations"].values()


def test_the_compiler_error_names_the_missing_instances_and_operations():
    ir = planner_ir()
    steps = [step("create", "createParcel", clause="request_1", index=0, body=True),
             step("after", "readParcel", source="create",
                  policy_id="readParcel_expectation_404")]
    with pytest.raises(ValidationFailure) as caught:
        compile_case(ir, steps)
    diagnostic = caught.value.diagnostic
    assert diagnostic.fields and all(field.startswith("instance.") for field in diagnostic.fields)
    assert diagnostic.detail["hint"]
    assert diagnostic.detail["consumers"]
