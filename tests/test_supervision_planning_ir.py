"""A follow-up round must plan from the same IR, never silently fall back to Legacy.

P0 dropped the Planning IR whenever supervision was enabled (``planning_ir = None if
supervision is not None``), so every follow-up round designed from text through the legacy
path and lost the resource/precondition model -- the very defect the round was meant to fix.
These tests pin the replacement: the round receives a *scoped* copy of the IR (with its
resources, edges and constraints) and, when no scope exists, the fallback is a recorded reason
rather than a silent engine change.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agenttest.planning_ir import (  # noqa: E402
    ActionInstance,
    Ambiguity,
    AmbiguityKind,
    ClauseRecord,
    ClauseRole,
    Constraint,
    ConstraintKind,
    DependencyEdge,
    EdgeReason,
    InstanceArgument,
    PlanningIR,
    ResourceHandle,
)
from agenttest.product_tasks import (  # noqa: E402
    SUPERVISION_IR_EMPTY,
    SUPERVISION_IR_SCOPED,
    SUPERVISION_IR_UNAVAILABLE,
    planning_engine_choice,
    planning_engine_conditions,
    scoped_supervision_ir,
)


def instance(identifier, operation, source="s1", index=0, arguments=()):
    return ActionInstance(instance_id=identifier, operation_id=operation, role=None,
                          source_id=source, source_quote="已验证的请求", source_start=index,
                          arguments=list(arguments))


def parcels_ir():
    """create -> delete -> read on one resource, plus an unrelated read from another clause."""
    return PlanningIR(
        instances=[instance("i-create", "createParcel", index=0),
                   instance("i-delete", "deleteParcel", index=1,
                            arguments=[InstanceArgument(location="path_parameters",
                                                        name="parcelId",
                                                        from_instance="i-create",
                                                        select="$.id")]),
                   instance("i-read", "readParcel", index=2,
                            arguments=[InstanceArgument(location="path_parameters",
                                                        name="parcelId",
                                                        from_instance="i-create",
                                                        select="$.id")]),
                   instance("i-job", "readJob", source="s2", index=3)],
        resources=[ResourceHandle(resource_id="parcel-1", producer_instance="i-create",
                                  operation_id="createParcel", path_parameter="parcelId",
                                  selector="$.id")],
        clauses=[ClauseRecord(source_id="s1", text="创建、删除后查询包裹", start=0,
                              role=ClauseRole.REQUEST),
                 ClauseRecord(source_id="s2", text="再看看任务", start=8,
                              role=ClauseRole.REQUEST)],
        constraints=[Constraint(kind=ConstraintKind.RESOURCE_ABSENT, source_id="s1",
                                instances=["i-read"]),
                     Constraint(kind=ConstraintKind.RESOURCE_EXISTS, source_id="s2",
                                instances=["i-job"])],
        edges=[DependencyEdge(source_instance="i-create", target_instance="i-delete",
                              reasons=[EdgeReason(reason="RESOURCE_DEPENDENCY",
                                                  evidence="同一资源", source_id="s1")]),
               DependencyEdge(source_instance="i-create", target_instance="i-read",
                              reasons=[EdgeReason(reason="RESOURCE_DEPENDENCY",
                                                  evidence="同一资源", source_id="s1")])],
        ambiguities=[Ambiguity(kind=AmbiguityKind.INSTANCE_COVERAGE, message="s2 未覆盖",
                               source_id="s2")],
        normalized_requirements=[dict(source_id="s1", text="包裹"),
                                 dict(source_id="s2", text="任务")],
        coverage=dict(s1="REQUIRED", s2="REQUIRED"), normalization_applied=True,
        order=["createParcel", "deleteParcel", "readParcel", "readJob"])


def test_scoping_keeps_the_obligations_instances_and_drops_the_rest():
    scoped = parcels_ir().scope_to(["createParcel", "deleteParcel", "readParcel"])
    assert [item.instance_id for item in scoped.instances] == ["i-create", "i-delete", "i-read"]
    assert [item.source_id for item in scoped.clauses] == ["s1"]
    assert [item.get("text") for item in scoped.normalized_requirements] == ["包裹"]
    assert [item.message for item in scoped.ambiguities] == []  # belonged to the other clause
    assert scoped.normalization_applied is True


def test_the_dependency_closure_keeps_the_producer_of_a_scoped_read():
    """A 404 read needs the create that established the phase, even outside the obligation."""
    scoped = parcels_ir().scope_to(["readParcel"])
    assert [item.instance_id for item in scoped.instances] == ["i-create", "i-read"]
    assert [item.resource_id for item in scoped.resources] == ["parcel-1"]


def test_scoping_keeps_the_precondition_evidence():
    scoped = parcels_ir().scope_to(["createParcel", "deleteParcel", "readParcel"])
    # This is what the delete-then-404 rule needs: the resource handle, the lifecycle edges and
    # the absence constraint must survive narrowing, or the follow-up round cannot see them.
    assert [item.resource_id for item in scoped.resources] == ["parcel-1"]
    assert [(edge.source_instance, edge.target_instance) for edge in scoped.edges] == [
        ("i-create", "i-delete"), ("i-create", "i-read")]
    assert [(item.kind.value, item.instances) for item in scoped.constraints] == [
        ("RESOURCE_ABSENT", ["i-read"])]


def test_a_round_with_a_scoped_ir_records_the_scoped_engine_decision():
    scoped, note = scoped_supervision_ir(parcels_ir(), {"primary": ["readParcel", "deleteParcel"]})
    assert note["code"] == SUPERVISION_IR_SCOPED
    # The obligation's own operations plus the closure that can establish its precondition.
    assert note["operations"] == ["createParcel", "deleteParcel", "readParcel"]
    assert note["requested"] == ["deleteParcel", "readParcel"]
    assert note["closure_added"] == ["createParcel"]
    assert note["resources"] == ["parcel-1"]
    # Production evaluates the compiler choice against the round's *realizable* coverage (the
    # closure), not just the obligation's own operations: the create is part of what the round
    # must do to make the 404 expectation reachable.
    closure = sorted({item.operation_id for item in scoped.instances})
    conditions = planning_engine_conditions(scoped, group={"primary": closure},
                                            questions=None, requested_checks=None,
                                            permission_requirements=None, unresolved=[])
    assert conditions["planning_ir"] is True
    assert conditions["all_instances_in_group"] is True
    # Without the closure (the obligation alone) the compiler would look partial -- the reason
    # P0's silent `planning_ir = None` was the wrong fix for this condition.
    narrow = planning_engine_conditions(scoped, group={"primary": ["readParcel", "deleteParcel"]},
                                        questions=None, requested_checks=None,
                                        permission_requirements=None, unresolved=[])
    assert narrow["all_instances_in_group"] is False
    assert planning_engine_choice(conditions)["reason"] in {"IR_SELECTED",
                                                            "NO_NORMALIZATION"}


def test_a_missing_ir_is_a_recorded_downgrade_not_a_silent_one():
    scoped, note = scoped_supervision_ir(None, {"primary": ["readParcel"]})
    assert scoped is None
    assert note["code"] == SUPERVISION_IR_UNAVAILABLE
    assert "Legacy" in note["reason"]


def test_an_obligation_without_instances_is_reported_with_its_operations():
    scoped, note = scoped_supervision_ir(parcels_ir(), {"primary": ["deleteJob"]})
    assert scoped is None
    assert note["code"] == SUPERVISION_IR_EMPTY
    assert note["operations"] == ["deleteJob"]