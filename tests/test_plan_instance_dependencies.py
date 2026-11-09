"""Instance-level dependencies: one API type can appear several times in one user order.

Every case names a concrete clause instance on both sides of each required order and asserts the
constructed edges, not merely a success status. Array position never decides order.
"""

import sys

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.plan_construction import construct_plan
from agenttest.request_requirements import RequestRequirement

READ_METHODS = {"read": "GET", "state": "GET"}


def policy(operation):
    return dict(id=operation, operation_id=operation, roles=[None, "owner", "other"])


def context(*operations):
    operations = operations or ("create", "read", "update", "replace", "delete", "login")
    return dict(policies=[policy(operation) for operation in operations],
                operations=[dict(id=operation, method=READ_METHODS.get(operation, "POST"))
                            for operation in operations],
                read_operations=[operation for operation, method in READ_METHODS.items()
                                 if operation in operations])


def clause(source_id, *operations, quote=None, before=(), forbidden=(), blocked=()):
    roles = ("owner",) * len(operations)
    return dict(source_id=source_id, source_quote=quote or source_id,
                disposition="required",
                actions=[dict(operation_id=operation, role=role)
                         for operation, role in zip(operations, roles)],
                before=list(before), same_resource=[], response_checks=[],
                forbidden_operation_ids=list(forbidden),
                blocked_operation_ids=list(blocked))


def order(earlier, later, earlier_source, later_source, earlier_index=0, later_index=0):
    return dict(earlier=earlier, later=later,
                earlier_source_id=earlier_source, earlier_index=earlier_index,
                later_source_id=later_source, later_index=later_index)


def step(key, operation, *, clause_id=None, index=0, role="owner", source=None,
         arguments=(), policy_id=None):
    arguments = list(arguments) or ([] if source is None else [
        dict(location="path_parameters", name="id", value=None, from_step=source,
             select="$.id")])
    node = dict(id=key, operation_id=operation, role=role,
                policy_id=policy_id or operation, arguments=arguments)
    if clause_id is not None:
        node["requirement_source_id"] = clause_id
        node["requirement_action_index"] = index
    return node


def literal(name, value):
    return [dict(location="body", name=name, value=value, from_step=None, select=None)]


def build(requirements, steps, *operations):
    ctx = context(*operations)
    ctx["request_requirements"] = requirements
    return construct_plan(dict(name="instance-order", steps=steps), ctx)


def edges(plan, stats):
    return {key: [source for source in value if not source.startswith("managed_")]
            for key, value in stats["node_dependencies"].items()
            if not key.startswith("managed_")}


def order_of(plan):
    return [step["id"] for step in plan["steps"] if not step["id"].startswith("managed_")]


def test_minimal_crud_reads_once_before_and_once_after_delete():
    """POST -> GET(200) -> DELETE(204) -> GET(404): the two GETs are different instances.

    The readback must take its id from the creation, because the deletion answers 204 with no
    body; its position after the deletion is a written-order edge, not a data reference.
    """
    requirements = [
        clause("request_1", "create"),
        clause("request_2", "read", before=[order("create", "read", "request_1", "request_2")]),
        clause("request_3", "delete", before=[order("read", "delete", "request_2", "request_3")]),
        clause("request_4", "read", before=[order("delete", "read", "request_3", "request_4")]),
    ]
    steps = [step("c", "create", clause_id="request_1"),
             step("read_before", "read", clause_id="request_2", source="c"),
             step("d", "delete", clause_id="request_3", source="c"),
             step("read_after", "read", clause_id="request_4", source="c")]
    plan, stats = build(requirements, steps, "create", "read", "delete")
    assert order_of(plan) == ["c", "read_before", "d", "read_after"]
    assert set(edges(plan, stats)["read_after"]) == {"c", "d"}
    assert stats["edge_reasons"]["read_after<=c"] == "MODEL_REFERENCE"
    assert stats["edge_reasons"]["read_after<=d"] == "TEXT_ORDER"
    assert "read_before" in edges(plan, stats)["d"]


def test_the_readback_order_edge_comes_from_the_requirement_not_from_the_binding():
    """Dropping only the written order removes only the TEXT_ORDER edge from the deletion."""
    requirements = [
        clause("request_1", "create"),
        clause("request_2", "read", before=[order("create", "read", "request_1", "request_2")]),
        clause("request_3", "delete", before=[order("read", "delete", "request_2", "request_3")]),
    ]
    steps = [step("c", "create", clause_id="request_1"),
             step("read_before", "read", clause_id="request_2", source="c"),
             step("d", "delete", clause_id="request_3", source="c"),
             step("read_after", "read", source="c")]
    plan, stats = build(requirements, steps, "create", "read", "delete")
    assert set(edges(plan, stats)["read_after"]) == {"c"}
    assert stats["edge_reasons"]["read_after<=c"] == "MODEL_REFERENCE"


def test_full_crud_keeps_every_step_and_the_final_readback():
    """POST -> GET -> PATCH -> PUT -> DELETE -> GET(404)."""
    requirements = [
        clause("request_1", "create"),
        clause("request_2", "read", before=[order("create", "read", "request_1", "request_2")]),
        clause("request_3", "update", before=[order("read", "update", "request_2", "request_3")]),
        clause("request_4", "replace",
               before=[order("update", "replace", "request_3", "request_4")]),
        clause("request_5", "delete",
               before=[order("replace", "delete", "request_4", "request_5")]),
        clause("request_6", "read", before=[order("delete", "read", "request_5", "request_6")]),
    ]
    steps = [step("c", "create", clause_id="request_1"),
             step("r1", "read", clause_id="request_2", source="c"),
             step("p", "update", clause_id="request_3", source="c",
                  arguments=literal("name", "n")),
             step("u", "replace", clause_id="request_4", source="c",
                  arguments=literal("name", "m")),
             step("d", "delete", clause_id="request_5", source="c"),
             step("r2", "read", clause_id="request_6", source="c")]
    plan, stats = build(requirements, steps, "create", "read", "update", "replace", "delete")
    assert order_of(plan) == ["c", "r1", "p", "u", "d", "r2"]
    assert edges(plan, stats)["r2"] == ["d"] or set(edges(plan, stats)["r2"]) >= {"d"}


def test_non_adjacent_repeated_create_keeps_three_instances():
    """POST(A) -> POST(B) -> POST(A): A1 -> B2 -> A3, never grouped by request signature."""
    requirements = [clause("request_1", "create", "create", "create",
                           quote="先建A，再建B，最后再建A",
                           before=[order("create", "create", "request_1", "request_1", 0, 1),
                                   order("create", "create", "request_1", "request_1", 1, 2)])]
    steps = [step("a1", "create", clause_id="request_1", index=0, arguments=literal("name", "A")),
             step("b2", "create", clause_id="request_1", index=1, arguments=literal("name", "B")),
             step("a3", "create", clause_id="request_1", index=2, arguments=literal("name", "A"))]
    for proposal in (steps, [steps[2], steps[0], steps[1]], [steps[1], steps[2], steps[0]]):
        plan, stats = build(requirements, proposal, "create")
        assert order_of(plan) == ["a1", "b2", "a3"]
        assert edges(plan, stats)["b2"] == ["a1"] and edges(plan, stats)["a3"] == ["b2"]


def test_idempotent_replay_and_conflict_keep_their_own_expectations():
    """POST(201) -> POST(200) -> POST(409): three sends of one operation, three instances."""
    requirements = [clause("request_1", "create", "create", "create",
                           quote="先创建，再重复提交一次，最后提交冲突内容",
                           before=[order("create", "create", "request_1", "request_1", 0, 1),
                                   order("create", "create", "request_1", "request_1", 1, 2)])]
    steps = [step("first", "create", clause_id="request_1", index=0,
                  arguments=literal("name", "a")),
             step("replay", "create", clause_id="request_1", index=1,
                  arguments=literal("name", "a")),
             step("conflict", "create", clause_id="request_1", index=2,
                  arguments=literal("name", "b"))]
    plan, stats = build(requirements, [steps[2], steps[1], steps[0]], "create")
    assert order_of(plan) == ["first", "replay", "conflict"]
    assert edges(plan, stats)["replay"] == ["first"]
    assert edges(plan, stats)["conflict"] == ["replay"]


def test_multi_identity_order_keeps_owner_and_other_reads_apart():
    """create -> owner read -> other read -> delete: three identities, four instances."""
    requirements = [
        clause("request_1", "create"),
        clause("request_2", "read", before=[order("create", "read", "request_1", "request_2")]),
        clause("request_3", "read", before=[order("read", "read", "request_2", "request_3")]),
        clause("request_4", "delete", before=[order("read", "delete", "request_3", "request_4")]),
    ]
    steps = [step("c", "create", clause_id="request_1"),
             step("owner_read", "read", clause_id="request_2", source="c"),
             step("other_read", "read", clause_id="request_3", source="c", role="other"),
             step("d", "delete", clause_id="request_4", source="c")]
    plan, stats = build(requirements, [steps[3], steps[2], steps[0], steps[1]],
                        "create", "read", "delete")
    assert order_of(plan) == ["c", "owner_read", "other_read", "d"]
    assert set(edges(plan, stats)["other_read"]) == {"c", "owner_read"}
    assert set(edges(plan, stats)["d"]) == {"c", "other_read"}


def test_consecutive_reads_are_three_ordered_instances():
    """GET1 -> GET2 -> GET3 without collapsing them into one array-position claim."""
    requirements = [clause("request_1", "read", "read", "read", quote="连续读取三次",
                           before=[order("read", "read", "request_1", "request_1", 0, 1),
                                   order("read", "read", "request_1", "request_1", 1, 2)])]
    steps = [step("g1", "read", clause_id="request_1", index=0),
             step("g2", "read", clause_id="request_1", index=1),
             step("g3", "read", clause_id="request_1", index=2)]
    plan, stats = build(requirements, [steps[1], steps[2], steps[0]], "read")
    assert order_of(plan) == ["g1", "g2", "g3"]
    assert edges(plan, stats)["g2"] == ["g1"] and edges(plan, stats)["g3"] == ["g2"]


def test_true_contradiction_reports_the_cycle_path_and_no_http():
    """A written order against a data reference is a real cycle, with each edge explained."""
    requirements = [
        clause("request_1", "create"),
        clause("request_2", "read", before=[order("read", "create", "request_2", "request_1")]),
    ]
    steps = [step("read", "read", clause_id="request_2", source="c"),
             step("c", "create", clause_id="request_1")]
    with pytest.raises(ValidationFailure) as error:
        build(requirements, steps, "create", "read")
    assert error.value.diagnostic.code == "CONSTRUCTION_DEPENDENCY_CYCLE"
    assert sorted(error.value.construction["cycle"]) == ["c", "read"]
    assert {edge["reason"] for edge in error.value.construction["edges"]} == {
        "MODEL_REFERENCE", "TEXT_ORDER"}


def test_unresolvable_instance_mapping_needs_review_instead_of_guessing():
    """Two distinct reads with no instance mapping cannot be ordered: stop before HTTP."""
    requirements = [
        clause("request_1", "read"),
        clause("request_2", "read", before=[order("read", "read", "request_1", "request_2")]),
    ]
    steps = [step("r1", "read"), step("r2", "read", role="other")]
    with pytest.raises(ValidationFailure) as error:
        build(requirements, steps, "read")
    assert error.value.diagnostic.code == "CONSTRUCTION_ORDER_INSTANCE_UNBOUND"
    assert error.value.diagnostic.fields == ["requirement_map.request_1.requirement_action_index"]


def test_schema_offers_nullable_instance_slots_and_a_bounded_action_index():
    from agenttest.llm_planner import GenericPlannerDraft
    from agenttest.product_schema import constrain_product_steps
    from agenttest.request_requirements import request_atoms, requirement_schema

    atoms = request_atoms("创建条目，然后读取条目。")
    ctx = context("create", "read")
    ctx["approved_ids"] = ["create", "read"]
    ctx["user_task"] = "创建条目，然后读取条目。"
    ctx["request_atoms"] = atoms
    ctx["request_requirements"] = [clause("request_1", "create")]
    schema = requirement_schema(ctx)
    order_schema = schema["$defs"]["RequestOrder"]
    assert order_schema["properties"]["earlier_source_id"]["enum"] == [
        atom["source_id"] for atom in atoms] + [None]
    design = GenericPlannerDraft.model_json_schema()
    constrain_product_steps(design, ctx)
    branch = design["$defs"]["GenericDraftStep"]["anyOf"][0]
    # The wire carries a compact nullable slot: repeating the clause vocabulary in every
    # per-policy branch pushed the frozen design request over its input budget. Unknown
    # clause ids are refused by the trusted local check instead.
    assert branch["properties"]["requirement_source_id"] == {"type": ["string", "null"]}
    assert branch["properties"]["requirement_action_index"]["maximum"] == 19


def test_context_split_keeps_cross_case_order_and_cross_step_bindings():
    """Splitting into execution contexts must not break a cross-clause instance dependency."""
    from agenttest.product_design import TestDesign
    from agenttest.request_requirements import validate_plan_requirements

    requirements = [
        clause("request_1", "create"),
        clause("request_2", "read", before=[order("create", "read", "request_1", "request_2")]),
    ]
    design = TestDesign(interpretation="先建后读", cases=[
        dict(title="先创建", kind="flow", plan=dict(name="create", steps=[
            step("c", "create", clause_id="request_1")])),
        dict(title="再读取", kind="flow", plan=dict(name="read", steps=[
            step("r", "read", clause_id="request_2", source="c")]))],
        questions=[], not_covered=[])
    validate_plan_requirements(design, [RequestRequirement.model_validate(item)
                                        for item in requirements], ["create", "read"])
    reversed_design = TestDesign(interpretation="先读后建", cases=list(reversed(design.cases)),
                                 questions=[], not_covered=[])
    with pytest.raises(ValidationFailure, match="DESIGN_REQUIRED_ORDER_MISSING"):
        validate_plan_requirements(reversed_design, [RequestRequirement.model_validate(item)
                                                     for item in requirements],
                                   ["create", "read"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
