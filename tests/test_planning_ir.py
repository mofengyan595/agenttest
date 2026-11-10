"""The normalized planning IR and the deterministic compiler, proven on archived M2 evidence."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.plan_construction import construct_plan
from agenttest.planning_ir import build_planning_ir
from agenttest.product_tasks import ProductTasks, aggregate_usage, task_max_seconds
from agenttest.request_requirements import request_atoms

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "rw-acceptance-m2-8845c31" / "evidence.json"


def archived_m2():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    decoded = evidence["focus"][0]["decoded"]
    return evidence["text"], decoded["requirements"], decoded["input_requirements"]


def policy(policy_id, operation_id, statuses, rules=(), *, roles=("author",), extra=None):
    item = dict(id=policy_id, operation_id=operation_id, roles=list(roles),
                assertions=dict(reviewed=True, source="operator",
                                status=dict(codes=list(statuses))),
                binding_rules=list(rules), negative=None)
    item.update(extra or {})
    return item


def m2_policies():
    path_rule = [dict(location="path_parameters", name="itemId",
                      source_policy_id="createItem", source_selector="$.id")]
    return [
        policy("createItem", "createItem", [201], [
            dict(location="body", name="name",
                 literal_schema=dict(enum=["first", "second"])),
            dict(location="headers", name="Idempotency-Key",
                 literal_schema=dict(enum=["rw-lifecycle-key-0001"]))]),
        policy("getItem", "getItem", [200], path_rule),
        policy("getItem_expectation_1", "getItem", [404], path_rule),
        policy("deleteItem", "deleteItem", [204], path_rule),
    ]


M2_OPERATIONS = [
    dict(id="createItem", method="POST", path="/items"),
    dict(id="getItem", method="GET", path="/items/{itemId}"),
    dict(id="deleteItem", method="DELETE", path="/items/{itemId}"),
]
M2_LIFECYCLE = [dict(create_operation="createItem", delete_operation="deleteItem",
                     path_parameter="itemId", id_selector="$.id")]


def m2_ir():
    text, requirements, inputs = archived_m2()
    return build_planning_ir(requirements, request_atoms(text),
                             operations=M2_OPERATIONS, policies=m2_policies(),
                             target_lifecycles=M2_LIFECYCLE,
                             read_operations={"getItem"}, input_requirements=inputs)


def step(identifier, operation, *, clause=None, body=False, source=None, policy_id=None):
    arguments = []
    if body:
        arguments.append(dict(location="body", name="name", value="first",
                              from_step=None, select=None))
    if source is not None:
        arguments.append(dict(location="path_parameters", name="itemId", value=None,
                              from_step=source, select="$.id"))
    node = dict(id=identifier, operation_id=operation, role="author",
                policy_id=policy_id or operation, arguments=arguments)
    if clause is not None:
        node.update(requirement_source_id=clause, requirement_action_index=0)
    return node


def test_archived_m2_normalizes_eight_clauses_into_four_instances():
    ir = m2_ir()
    assert ir.normalization_applied is True
    assert ir.coverage["required_clauses_before"] == 8
    assert ir.coverage["required_clauses_after"] == 4
    assert ir.coverage["assertion_clauses"] == 3
    assert ir.coverage["data_capture_clauses"] == 1
    assert [(item.instance_id, item.operation_id) for item in ir.instances] == [
        ("ai_1", "createItem"), ("ai_2", "getItem"),
        ("ai_3", "deleteItem"), ("ai_4", "getItem")]
    assert ir.order == ["ai_1", "ai_2", "ai_3", "ai_4"]
    assert ir.ambiguities == []
    statuses = [[status for assertion in item.assertions for status in assertion.statuses]
                for item in ir.instances]
    assert statuses == [[], [200], [204], [404]]
    assert [item.policy_id for item in ir.instances] == [
        "createItem", "getItem", "deleteItem", "getItem_expectation_1"]
    assert [item.phase for item in ir.instances] == [
        None, "EXISTS", "DELETED", "VERIFY_ABSENT"]
    assert {item["source_id"] for item in ir.normalized_requirements} == {
        "request_1", "request_3", "request_5", "request_7"}


def test_every_m2_edge_keeps_all_its_reasons():
    ir = m2_ir()
    pairs = ir.edge_pairs()
    reasons = {pair: {reason.reason for reason in items} for pair, items in pairs.items()}
    assert {"DATA_DEPENDENCY", "TEXT_ORDER", "SEQUENCE_MARKER"} <= reasons[
        ("ai_1", "ai_2")]
    assert "LIFECYCLE_CLEANUP" in reasons[("ai_2", "ai_3")]
    assert {"RESOURCE_ABSENT", "TEXT_ORDER"} <= reasons[("ai_3", "ai_4")]
    # The post-delete readback references the create id but must never delay the delete.
    assert ("ai_4", "ai_3") not in pairs
    assert any(argument.from_instance == "ai_1"
               for argument in ir.instance("ai_4").arguments)


def test_archived_m2_legacy_path_still_reports_a_cycle():
    _, requirements, _ = archived_m2()
    context = dict(
        policies=[dict(id=op, operation_id=op, roles=["author"])
                  for op in ("createItem", "getItem", "deleteItem")],
        operations=[dict(id="createItem", method="POST"), dict(id="getItem", method="GET"),
                    dict(id="deleteItem", method="DELETE")],
        request_requirements=requirements,
        resource_lifecycles=M2_LIFECYCLE)
    steps = [step("get_3", "getItem", source="create_1"),
             step("delete_5", "deleteItem", source="create_1")]
    # A real create step keeps the reference valid; the unbound order obligations still
    # widen each GET/DELETE obligation to every call of that operation.
    steps.insert(0, step("create_1", "createItem", body=True))
    steps.append(step("get_7", "getItem", source="create_1"))
    with pytest.raises(ValidationFailure) as caught:
        construct_plan(dict(name="legacy", steps=steps), context)
    assert caught.value.diagnostic.code == "CONSTRUCTION_DEPENDENCY_CYCLE"
    assert caught.value.diagnostic.fields


def test_archived_m2_ir_path_compiles_a_correct_dag():
    ir = m2_ir()
    policies = m2_policies()
    steps = [
        step("read_item", "getItem", clause="request_3", source="create_item"),
        step("read_after_delete", "getItem", clause="request_7", source="create_item",
             policy_id="getItem_expectation_1"),
        step("create_item", "createItem", clause="request_1", body=True),
        step("remove_item", "deleteItem", clause="request_5", source="create_item"),
    ]
    plan, stats = construct_plan(dict(name="m2", steps=steps), dict(
        policies=policies, operations=M2_OPERATIONS, authentication=[],
        planning_ir=ir))
    assert [item["id"] for item in plan["steps"]] == [
        "create_item", "read_item", "remove_item", "read_after_delete"]
    assert [item["policy_id"] for item in plan["steps"]] == [
        "createItem", "getItem", "deleteItem", "getItem_expectation_1"]
    for item in plan["steps"][1:]:
        binding = next(argument for argument in item["arguments"]
                       if argument["name"] == "itemId")
        assert binding["from_step"] == "create_item"
        assert binding["select"] == "$.id"
    assert stats["edge_reasons"]["read_after_delete<=remove_item"].startswith(
        "RESOURCE_ABSENT")
    assert stats["edge_reasons"]["remove_item<=read_item"].startswith("LIFECYCLE_CLEANUP")


def test_unbound_repeated_gets_match_on_approved_status():
    """Two GET steps without source ids are distinguished by their approved 200/404 policies."""
    ir = m2_ir()
    steps = [
        step("create_item", "createItem", body=True),
        step("read_item", "getItem", source="create_item"),
        step("remove_item", "deleteItem", source="create_item"),
        step("read_after_delete", "getItem", source="create_item",
             policy_id="getItem_expectation_1"),
    ]
    plan, stats = construct_plan(dict(name="m2-unbound", steps=steps), dict(
        policies=m2_policies(), operations=M2_OPERATIONS, authentication=[], planning_ir=ir))
    assert stats["instance_steps"] == {
        "ai_1": "create_item", "ai_2": "read_item",
        "ai_3": "remove_item", "ai_4": "read_after_delete"}
    assert [item["id"] for item in plan["steps"]] == [
        "create_item", "read_item", "remove_item", "read_after_delete"]
    assert [item["policy_id"] for item in plan["steps"]] == [
        "createItem", "getItem", "deleteItem", "getItem_expectation_1"]


def test_unbound_identical_gets_remain_review():
    """Without a policy/status/resource distinction the compiler must not guess."""
    quote_one, quote_two = "读取条目甲", "读取条目乙"
    requirements = [
        dict(source_id="request_1", source_quote=quote_one, disposition="required",
             actions=[dict(operation_id="getItem", role="author")], before=[],
             same_resource=[]),
        dict(source_id="request_2", source_quote=quote_two, disposition="required",
             actions=[dict(operation_id="getItem", role="author")], before=[],
             same_resource=[])]
    atoms = [dict(source_id="request_1", text=quote_one, start=0),
             dict(source_id="request_2", text=quote_two, start=8)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="getItem", method="GET", path="/items/{id}")],
                           policies=[policy("getItem", "getItem", [200])],
                           read_operations={"getItem"})
    steps = [step("read_a", "getItem"), step("read_b", "getItem")]
    with pytest.raises(ValidationFailure) as caught:
        construct_plan(dict(name="ambiguous", steps=steps), dict(
            policies=[policy("getItem", "getItem", [200])],
            operations=[dict(id="getItem", method="GET", path="/items/{id}")],
            authentication=[], planning_ir=ir))
    assert caught.value.diagnostic.code == "PLANNING_INSTANCE_AMBIGUOUS"
    assert caught.value.diagnostic.fields == ["plan.steps.0"]


def test_a_distinct_operation_set_in_one_clause_is_an_executable_request():
    text = "帮我测一下条目增删改查"
    requirements = [dict(source_id="request_1", source_quote=text, disposition="required",
                         actions=[dict(operation_id=operation, role=None)
                                  for operation in ("createItem", "getItem", "deleteItem")],
                         before=[], same_resource=[])]
    ir = build_planning_ir(
        requirements, request_atoms(text), operations=M2_OPERATIONS,
        policies=[policy("createItem", "createItem", [201], roles=(None,)),
                  policy("getItem", "getItem", [200], roles=(None,)),
                  policy("deleteItem", "deleteItem", [204], roles=(None,))],
        target_lifecycles=M2_LIFECYCLE, read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == [
        "createItem", "getItem", "deleteItem"]
    assert [item.role.value for item in ir.clauses] == ["REQUEST"]
    assert ir.ambiguities == []
    assert all(item.resource_id == ir.resources[0].resource_id for item in ir.instances)
    assert len(ir.edges) == 3


def test_a_change_verb_creates_a_request_instance():
    requirements = [
        dict(source_id="request_1", source_quote="创建一条条目", disposition="required",
             actions=[dict(operation_id="createItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_2", source_quote="把名称改成新的", disposition="required",
             actions=[dict(operation_id="changeItem", role=None)], before=[], same_resource=[]),
    ]
    atoms = [dict(source_id="request_1", text="创建一条条目", start=0),
             dict(source_id="request_2", text="把名称改成新的", start=7)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="createItem", method="POST", path="/items"),
                                       dict(id="changeItem", method="PATCH",
                                            path="/items/{itemId}")],
                           policies=[policy("createItem", "createItem", [201], roles=(None,)),
                                     policy("changeItem", "changeItem", [200], roles=(None,)),
                                     policy("getItem", "getItem", [200], roles=(None,)),
                                     policy("deleteItem", "deleteItem", [204], roles=(None,))],
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == ["createItem", "changeItem"]
    assert [item.role.value for item in ir.clauses] == ["REQUEST", "REQUEST"]
    assert ir.ambiguities == []


def test_a_bare_delete_confirmation_is_not_a_second_read_instance():
    requirements = [
        dict(source_id="request_1", source_quote="创建一条条目", disposition="required",
             actions=[dict(operation_id="createItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_2", source_quote="读取该条目", disposition="required",
             actions=[dict(operation_id="getItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_3", source_quote="删除该条目", disposition="required",
             actions=[dict(operation_id="deleteItem", role=None)],
             before=[dict(earlier="getItem", later="deleteItem",
                          earlier_source_id="request_2", earlier_index=0,
                          later_source_id="request_3", later_index=0)],
             same_resource=[]),
        dict(source_id="request_4", source_quote="确认删除成功", disposition="required",
             actions=[dict(operation_id="getItem", role=None)], before=[], same_resource=[]),
    ]
    atoms = [dict(source_id=f"request_{index}", text=text, start=index)
             for index, text in enumerate(
                 ["创建一条条目", "读取该条目", "删除该条目", "确认删除成功"], 1)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="createItem", method="POST", path="/items"),
                                       dict(id="getItem", method="GET", path="/items/{itemId}"),
                                       dict(id="deleteItem", method="DELETE",
                                            path="/items/{itemId}")],
                           policies=[policy("createItem", "createItem", [201], roles=(None,)),
                                     policy("getItem", "getItem", [200], roles=(None,)),
                                     policy("deleteItem", "deleteItem", [204], roles=(None,))],
                           target_lifecycles=[dict(create_operation="createItem",
                                                   delete_operation="deleteItem",
                                                   path_parameter="itemId", id_selector="$.id")],
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == [
        "createItem", "getItem", "deleteItem"]
    assert [item.role.value for item in ir.clauses] == [
        "REQUEST", "REQUEST", "REQUEST", "CONSTRAINT"]
    assert ir.ambiguities == []


def test_a_confirmed_delete_uses_the_approved_absence_readback():
    requirements = [
        dict(source_id="request_1", source_quote="创建一条条目", disposition="required",
             actions=[dict(operation_id="createItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_2", source_quote="读取该条目", disposition="required",
             actions=[dict(operation_id="getItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_3", source_quote="删除该条目", disposition="required",
             actions=[dict(operation_id="deleteItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_4", source_quote="确认删除成功", disposition="required",
             actions=[dict(operation_id="deleteItem", role=None)], before=[], same_resource=[]),
    ]
    atoms = [dict(source_id=f"request_{index}", text=text, start=index)
             for index, text in enumerate(
                 ["创建一条条目", "读取该条目", "删除该条目", "确认删除成功"], 1)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="createItem", method="POST", path="/items"),
                                       dict(id="getItem", method="GET", path="/items/{itemId}"),
                                       dict(id="deleteItem", method="DELETE",
                                            path="/items/{itemId}")],
                           policies=[policy("createItem", "createItem", [201], roles=(None,)),
                                     policy("getItem", "getItem", [200], roles=(None,)),
                                     policy("getItem_expectation_1", "getItem", [404],
                                            roles=(None,)),
                                     policy("deleteItem", "deleteItem", [204], roles=(None,))],
                           target_lifecycles=[dict(create_operation="createItem",
                                                   delete_operation="deleteItem",
                                                   path_parameter="itemId", id_selector="$.id")],
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == [
        "createItem", "getItem", "deleteItem", "getItem"]
    assert ir.instances[-1].phase == "VERIFY_ABSENT"
    assert ir.instances[-1].policy_id == "getItem_expectation_1"
    assert [status for assertion in ir.instances[-1].assertions
            for status in assertion.statuses] == [404]
    assert ir.ambiguities == []


def test_a_read_confirmation_after_update_is_a_new_read_instance():
    requirements = [
        dict(source_id="request_1", source_quote="创建一条条目", disposition="required",
             actions=[dict(operation_id="createItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_2", source_quote="读取该条目", disposition="required",
             actions=[dict(operation_id="getItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_3", source_quote="把名称改成新的", disposition="required",
             actions=[dict(operation_id="changeItem", role=None)], before=[], same_resource=[]),
        dict(source_id="request_4", source_quote="再确认", disposition="required",
             actions=[dict(operation_id="getItem", role=None)], before=[], same_resource=[]),
    ]
    atoms = [dict(source_id=f"request_{index}", text=text, start=index)
             for index, text in enumerate(
                 ["创建一条条目", "读取该条目", "把名称改成新的", "再确认"], 1)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="createItem", method="POST", path="/items"),
                                       dict(id="getItem", method="GET", path="/items/{itemId}"),
                                       dict(id="changeItem", method="PATCH",
                                            path="/items/{itemId}")],
                           policies=[policy("createItem", "createItem", [201], roles=(None,)),
                                     policy("getItem", "getItem", [200], roles=(None,)),
                                     policy("changeItem", "changeItem", [200], roles=(None,))],
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == [
        "createItem", "getItem", "changeItem", "getItem"]
    assert [item.role.value for item in ir.clauses] == [
        "REQUEST", "REQUEST", "REQUEST", "REQUEST"]
    assert ir.ambiguities == []


def test_a_read_back_phrase_creates_a_read_request():
    requirements = [dict(source_id="request_1", source_quote="按 ID 读回",
                          disposition="required",
                          actions=[dict(operation_id="getItem", role=None)],
                          before=[], same_resource=[])]
    atoms = [dict(source_id="request_1", text="按 ID 读回", start=0)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="getItem", method="GET", path="/items/{id}")],
                           policies=[policy("getItem", "getItem", [200], roles=(None,))],
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == ["getItem"]
    assert ir.ambiguities == []


def test_a_meta_clause_does_not_repeat_an_already_requested_operation_set():
    first = "帮我测一下条目增删改查"
    second = "看看增删改查有没有问题"
    requirements = [
        dict(source_id="request_1", source_quote=first, disposition="required",
             actions=[dict(operation_id=operation, role=None)
                      for operation in ("createItem", "getItem", "deleteItem")],
             before=[], same_resource=[]),
        dict(source_id="request_2", source_quote=second, disposition="required",
             actions=[dict(operation_id=operation, role=None)
                      for operation in ("getItem", "deleteItem")],
             before=[], same_resource=[]),
    ]
    ir = build_planning_ir(
        requirements, request_atoms(first + "，" + second), operations=M2_OPERATIONS,
        policies=[policy("createItem", "createItem", [201], roles=(None,)),
                  policy("getItem", "getItem", [200], roles=(None,)),
                  policy("deleteItem", "deleteItem", [204], roles=(None,))],
        target_lifecycles=M2_LIFECYCLE, read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == [
        "createItem", "getItem", "deleteItem"]
    assert [item.role.value for item in ir.clauses] == ["REQUEST", "CONSTRAINT"]
    assert ir.ambiguities == []


def test_status_only_clause_does_not_become_a_second_request():
    requirements = [
        dict(source_id="request_1", source_quote="读取该条目", disposition="required",
             actions=[dict(operation_id="getItem", role="author")], before=[],
             same_resource=[]),
        dict(source_id="request_2", source_quote="确认返回 200", disposition="required",
             actions=[dict(operation_id="getItem", role="author")], before=[],
             same_resource=[]),
    ]
    atoms = [dict(source_id="request_1", text="读取该条目", start=0),
             dict(source_id="request_2", text="确认返回 200", start=6)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="getItem", method="GET", path="/items/{id}")],
                           policies=[policy("getItem", "getItem", [200])],
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == ["getItem"]
    assert ir.normalized_requirements[0]["source_id"] == "request_1"
    assert ir.coverage["assertion_clauses"] == 1


def test_explicit_three_reads_become_three_instances():
    quote = "连续读取同一资源 3 次"
    requirements = [dict(source_id="request_1", source_quote=quote,
                         disposition="required",
                         actions=[dict(operation_id="getItem", role="author")],
                         before=[], same_resource=[])]
    atoms = [dict(source_id="request_1", text=quote, start=0)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="getItem", method="GET", path="/items/{id}")],
                           policies=[policy("getItem", "getItem", [200])],
                           read_operations={"getItem"})
    assert len(ir.instances) == 3
    assert ir.order == ["ai_1", "ai_2", "ai_3"]
    assert {reason.reason for edge in ir.edges for reason in edge.reasons} == {
        "REPLAY_GROUP"}


def test_two_descriptions_of_one_request_do_not_expand():
    requirements = [
        dict(source_id="request_1", source_quote="创建名称是 first 的条目",
             disposition="required",
             actions=[dict(operation_id="createItem", role="author")], before=[],
             same_resource=[]),
        dict(source_id="request_2", source_quote="该条目名称必须是 first",
             disposition="required",
             actions=[dict(operation_id="createItem", role="author")], before=[],
             same_resource=[]),
    ]
    atoms = [dict(source_id="request_1", text="创建名称是 first 的条目", start=0),
             dict(source_id="request_2", text="该条目名称必须是 first", start=15)]
    ir = build_planning_ir(requirements, atoms,
                           operations=[dict(id="createItem", method="POST", path="/items")],
                           policies=[policy("createItem", "createItem", [201])],
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == ["createItem"]
    assert [item["source_id"] for item in ir.normalized_requirements] == ["request_1"]
    assert ir.normalized_requirements[0]["actions"] == [
        dict(operation_id="createItem", role="author")]


def test_two_creates_cannot_share_one_unbound_resource():
    requirements = [
        dict(source_id="request_1", source_quote="创建第一个条目", disposition="required",
             actions=[dict(operation_id="createItem", role="author")], before=[],
             same_resource=[]),
        dict(source_id="request_2", source_quote="创建第二个条目", disposition="required",
             actions=[dict(operation_id="createItem", role="author")], before=[],
             same_resource=[]),
        dict(source_id="request_3", source_quote="读取第一个条目", disposition="required",
             actions=[dict(operation_id="getItem", role="author")],
             before=[dict(earlier="createItem", later="getItem")], same_resource=[]),
    ]
    atoms = [dict(source_id="request_1", text="创建第一个条目", start=0),
             dict(source_id="request_2", text="创建第二个条目", start=8),
             dict(source_id="request_3", text="读取第一个条目", start=16)]
    ir = build_planning_ir(requirements, atoms, operations=M2_OPERATIONS,
                           policies=m2_policies(), target_lifecycles=M2_LIFECYCLE,
                           read_operations={"getItem"})
    assert [item.operation_id for item in ir.instances] == [
        "createItem", "createItem", "getItem"]
    kinds = {item.kind.value for item in ir.ambiguities}
    assert "RESOURCE_REFERENCE_AMBIGUOUS" in kinds
    # No consumer may claim a resource whose producer is not unique.
    assert all(item.resource_id is None for item in ir.instances)


def test_true_cycle_is_reported_not_repaired():
    requirements = [
        dict(source_id="request_1", source_quote="先读取再删除", disposition="required",
             actions=[dict(operation_id="getItem", role="author")],
             before=[dict(earlier="deleteItem", later="getItem",
                          earlier_source_id="request_2", earlier_index=0,
                          later_source_id="request_1", later_index=0)],
             same_resource=[]),
        dict(source_id="request_2", source_quote="删除后再读取", disposition="required",
             actions=[dict(operation_id="deleteItem", role="author")],
             before=[dict(earlier="getItem", later="deleteItem",
                          earlier_source_id="request_1", earlier_index=0,
                          later_source_id="request_2", later_index=0)],
             same_resource=[]),
    ]
    atoms = [dict(source_id="request_1", text="先读取再删除", start=0),
             dict(source_id="request_2", text="删除后再读取", start=7)]
    ir = build_planning_ir(requirements, atoms, operations=M2_OPERATIONS,
                           policies=m2_policies(), read_operations={"getItem"})
    kinds = {item.kind.value for item in ir.ambiguities}
    assert "DEPENDENCY_CYCLE" in kinds
    assert ir.order == []


def test_unapproved_operation_is_an_ambiguity():
    requirements = [
        dict(source_id="request_1", source_quote="调用未授权接口", disposition="required",
             actions=[dict(operation_id="wipeAll", role="author")], before=[],
             same_resource=[])]
    atoms = [dict(source_id="request_1", text="调用未授权接口", start=0)]
    ir = build_planning_ir(requirements, atoms, operations=M2_OPERATIONS,
                           policies=m2_policies(), read_operations={"getItem"})
    assert [item.kind.value for item in ir.ambiguities] == ["OPERATION_NOT_APPROVED"]
    assert ir.instances == []


def test_usage_survives_a_later_design_failure():
    records = [
        dict(status="SELECTED", model_call=dict(calls=1, model_name="ecnu-max",
                                                total_tokens=12168, input_tokens=11276,
                                                output_tokens=892, elapsed_ms=27843)),
        dict(status="REJECTED", model_call=dict(calls=1, model_name="ecnu-max",
                                                total_tokens=10406, input_tokens=8924,
                                                output_tokens=1482, elapsed_ms=8344)),
    ]
    calls, usage = aggregate_usage(records)
    assert calls == 2
    assert usage.total_tokens == 22574
    assert usage.input_tokens == 20200
    assert usage.output_tokens == 2374
    unknown_calls, unknown = aggregate_usage([
        records[0], dict(status="REJECTED",
                         model_call=dict(calls=1, total_tokens=None))])
    assert unknown_calls == 2
    assert unknown.total_tokens is None
    assert unknown.input_tokens is None


def test_development_runner_can_lower_the_task_deadline_only_explicitly():
    assert task_max_seconds(SimpleNamespace()) == 600.0
    assert task_max_seconds(SimpleNamespace(dev_max_seconds=150)) == 150.0


def test_failure_report_keeps_the_known_usage():
    saved = []

    class Store:
        def save(self, report):
            saved.append(report)

    registry = SimpleNamespace(digest=lambda: "0" * 64)
    target = SimpleNamespace(environment="isolated", base_url="http://127.0.0.1:9",
                             model_dump=lambda mode="json": dict(environment="isolated",
                                                                base_url="http://127.0.0.1:9"))
    project = SimpleNamespace(document={}, registry=registry, target=target)
    tasks = ProductTasks(SimpleNamespace(reports=Store()))
    report = tasks.failure_report(
        project, "创建后读取", "CONSTRUCTION_DEPENDENCY_CYCLE", None,
        records=[dict(status="SELECTED", model_call=dict(calls=1, total_tokens=12168,
                                                         input_tokens=11276,
                                                         output_tokens=892)),
                 dict(status="REJECTED", model_call=dict(calls=1, total_tokens=10406,
                                                         input_tokens=8924,
                                                         output_tokens=1482))])
    assert report.planner_calls == 2
    assert report.model_call.total_tokens == 22574


@pytest.mark.asyncio
async def test_archived_m2_semantics_reach_the_executor_as_four_requests(tmp_path):
    """The production construction/DSL/Executor chain, with the archived eight clauses."""
    from test_instance_delivery_e2e import CrudTarget, deliver, project

    from agenttest.llm_planner import GenericPlannerDraft
    from agenttest.report import Outcome

    text, archived, _ = archived_m2()
    requirements = []
    for item in archived:
        item = json.loads(json.dumps(item))
        for action in item["actions"]:
            action["role"] = None
            if action["operation_id"] == "getItem":
                action["operation_id"] = "readItem"
        requirements.append(item)
    proj = project()
    policies = [item.model_dump(mode="json") for item in proj.registry.definitions()]
    operations = [dict(id=item.operation_id, method=item.method, path=item.path)
                  for item in proj.contract.operations]
    atoms = request_atoms(text)
    ir = build_planning_ir(requirements, atoms, operations=operations, policies=policies,
                           target_lifecycles=[dict(create_operation="createItem",
                               delete_operation="deleteItem", path_parameter="itemId",
                               id_selector="$.id")], read_operations={"readItem"})
    assert [item.policy_id for item in ir.instances] == [
        "web_rule_1", "web_rule_2", "web_rule_5", "web_rule_2_expectation_1"]

    def step(key, operation, policy, clause, *, source=None, name=None):
        arguments = []
        if name is not None:
            arguments.append(dict(location="body", name="name", value=name,
                                  from_step=None, select=None))
        if source is not None:
            arguments.append(dict(location="path_parameters", name="itemId", value=None,
                                  from_step=source, select="$.id"))
        return dict(id=key, operation_id=operation, role=None, policy_id=policy,
                    arguments=arguments, requirement_source_id=clause,
                    requirement_action_index=0)

    draft = dict(name="m2", steps=[
        step("c", "createItem", "web_rule_1", "request_1", name="first"),
        step("r1", "readItem", "web_rule_2", "request_3", source="c"),
        step("d", "deleteItem", "web_rule_5", "request_5", source="c"),
        step("r2", "readItem", "web_rule_2_expectation_1", "request_7", source="c"),
    ])
    plan, _ = construct_plan(draft, dict(
        policies=policies, operations=operations, planning_ir=ir,
        request_requirements=ir.normalized_requirements, request_atoms=atoms))
    scenario = GenericPlannerDraft.model_validate(plan).scenario()
    target = CrudTarget()
    report = await deliver(tmp_path, scenario, target)
    assert target.calls == [("POST", "/items"), ("GET", "/items/item_1"),
                            ("DELETE", "/items/item_1"), ("GET", "/items/item_1")]
    assert report.completed and report.outcome == Outcome.PASS
    assert target.resources == {}


@pytest.mark.asyncio
async def test_archived_m2_semantics_run_through_the_product_entry(tmp_path, monkeypatch):
    """Focus -> Design -> IR compiler -> DSL -> Executor, with the archived eight clauses."""
    from uuid import uuid4

    import httpx
    from test_instance_delivery_e2e import DOCUMENT, CrudTarget, setup
    from test_product_focus import focus_response
    from test_product_tasks import finished, response, service

    from agenttest.product_focus import TestFocusModel
    from agenttest.project_intake import IntakeInput

    text, archived, _ = archived_m2()
    requirement_map = {}
    for item in archived:
        item = json.loads(json.dumps(item))
        for action in item["actions"]:
            action["role"] = None
            if action["operation_id"] == "getItem":
                action["operation_id"] = "readItem"
        for pair in item.get("same_resource") or []:
            for key in ("producer", "consumer"):
                if pair.get(key) == "getItem":
                    pair[key] = "readItem"
        for order in item.get("before") or []:
            for key in ("earlier", "later"):
                if order.get(key) == "getItem":
                    order[key] = "readItem"
        requirement_map[item.pop("source_id")] = item
        item.pop("source_quote", None)

    def focus(request):
        return focus_response(dict(interpretation="按原文执行最小读写闭环", mode="selected",
            operation_ids=["createItem", "readItem", "deleteItem"],
            normal_operation_ids=["createItem", "readItem", "deleteItem"],
            question=None, requirement_map=requirement_map), request)

    def designer(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert context["fixed_resource_setups"][0]["slot"] == "fresh_1"
        flow = dict(title="最小读写闭环", kind="flow", plan=dict(name="m2", steps=[
            dict(id="c", operation_id="createItem", role=None, policy_id="web_rule_1",
                 arguments=[dict(location="body", name="name", value="first",
                                 from_step=None, select=None)]),
            dict(id="r1", operation_id="readItem", role=None, policy_id="web_rule_2",
                 arguments=[dict(location="path_parameters", name="itemId", value=None,
                                 from_step="c", select="$.id")]),
            dict(id="d", operation_id="deleteItem", role=None, policy_id="web_rule_5",
                 arguments=[dict(location="path_parameters", name="itemId", value=None,
                                 from_step="c", select="$.id")]),
            dict(id="r2", operation_id="readItem", role=None,
                 policy_id="web_rule_2_expectation_1",
                 arguments=[dict(location="path_parameters", name="itemId", value=None,
                                 from_step="c", select="$.id")]),
        ]))
        return response(dict(interpretation="创建、读取、删除、回读", cases=[],
                             resource_flows={"fresh_1": flow}, questions=[],
                             not_covered=[]))

    from test_product_design import settings

    target = CrudTarget()
    service_dir = tmp_path / "service"
    service_dir.mkdir()
    svc = service(service_dir, designer)
    svc.focus_factory = lambda _: TestFocusModel(
        settings(), transport=httpx.MockTransport(focus), profile="generic")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    try:
        saved = svc.intake.save(IntakeInput(
            name="m2-ir-product", document=json.dumps(DOCUMENT), generic=setup(),
            confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], text, uuid4().hex)
        done = await finished(svc, row)
        summary = done["summary"]
        assert done["status"] == "FINISHED" and summary["complete"], summary.get("reason")
        assert summary["planning_engine"] == "NORMALIZED_IR_COMPILER_V1"
        assert summary["planning_engine_reason"] == "IR_SELECTED"
        assert summary["planning_instance_count"] == 4
        assert summary["planning_instance_mapping_complete"] is True
        assert summary["planning_dependency_edges"] == 5
        assert len(summary["planning_requirements"]) == 8
        conditions = summary["planning_engine_groups"][-1]["conditions"]
        assert conditions["planning_ir"] is True
        assert conditions["normalization_applied"] is True
        assert conditions["no_pending_permissions"] is True
        assert conditions["no_pending_questions"] is True
        assert conditions["all_instances_in_group"] is True
        assert conditions["no_unresolved_request_clauses"] is True
        construction = summary["design_records"][-1]["plan_construction"]
        assert [item["encoding"] for item in construction] == [
            "NORMALIZED_IR_COMPILER_V1"]
        assert construction[0]["instance_steps"] == {
            "ai_1": "c", "ai_2": "r1", "ai_3": "d", "ai_4": "r2"}
        assert summary["planning_ir"]["coverage"]["instances"] == 4
        assert summary["planning_ir"]["coverage"]["required_clauses_after"] == 4
        assert target.calls == [("POST", "/items"), ("GET", "/items/item_1"),
                                ("DELETE", "/items/item_1"), ("GET", "/items/item_1")]
        assert summary["cases"][0]["outcome"] == "PASS"
        assert summary["cases"][0]["http_requests"] == 4
        assert target.resources == {}
    finally:
        await svc.close()

