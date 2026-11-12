"""Blocking vs. resolvable Planning-IR ambiguity: a safe stop is not a false stop."""

from test_product_supervisor import lifecycle_project

from agenttest.product_tasks import resolve_planning_ambiguities


def ambiguities(*items):
    class Stub:
        ambiguities = list(items)
    return Stub()


def unclassified(source_id="request_2"):
    return dict(kind="UNCLASSIFIED_CLAUSE", source_id=source_id,
                fields=[f"requirement_map.{source_id}"],
                message="该子句既不像新的请求，也不像状态/数据约束。", detail={})


def unknown_source(policy_id):
    return dict(kind="PARAMETER_SOURCE_UNKNOWN", source_id="request_1", fields=[],
                message="参数来源策略没有唯一的产生者实例。", detail=dict(policy_id=policy_id))


def missing_identity(earlier, later="deleteParcel"):
    return dict(kind="MISSING_INSTANCE_IDENTITY", source_id="request_2",
                fields=["requirement_map.request_2.before"],
                message="顺序约束指向的子句没有对应的请求实例。",
                detail=dict(order=dict(earlier=earlier, later=later)))


def test_a_clause_that_produced_no_instance_is_an_advisory_not_a_stop():
    project, _ = lifecycle_project()
    blocking, advisories = resolve_planning_ambiguities(project, ambiguities(unclassified()))
    assert blocking == []
    assert [item["kind"] for item in advisories] == ["UNCLASSIFIED_CLAUSE"]


def test_an_approved_producer_makes_the_unknown_parameter_source_resolvable():
    project, _ = lifecycle_project()
    blocking, advisories = resolve_planning_ambiguities(
        project, ambiguities(unknown_source("web_rule_1")))
    assert blocking == []
    assert advisories[0]["resolved_by"]


def test_a_parameter_whose_policy_cannot_produce_it_still_stops():
    project, _ = lifecycle_project()
    policies = {policy.id: policy for policy in project.registry.definitions()}
    read_id = next(pid for pid, policy in policies.items() if policy.operation_id == "readParcel")
    blocking, advisories = resolve_planning_ambiguities(
        project, ambiguities(unknown_source(read_id)))
    assert advisories == []
    assert blocking[0]["kind"] == "PARAMETER_SOURCE_UNKNOWN"


def test_a_sequence_whose_earlier_step_is_an_approved_write_is_resolvable():
    project, _ = lifecycle_project()
    blocking, advisories = resolve_planning_ambiguities(
        project, ambiguities(missing_identity("createParcel")))
    assert blocking == []
    assert "createParcel" in advisories[0]["resolved_by"]


def test_a_sequence_whose_earlier_step_is_not_a_write_still_stops():
    project, _ = lifecycle_project()
    blocking, _ = resolve_planning_ambiguities(
        project, ambiguities(missing_identity("readParcel")))
    assert [item["kind"] for item in blocking] == ["MISSING_INSTANCE_IDENTITY"]


def test_an_unknown_ambiguity_kind_is_never_downgraded():
    project, _ = lifecycle_project()
    blocking, advisories = resolve_planning_ambiguities(
        project, ambiguities(dict(kind="SOMETHING_NEW", source_id="request_1", fields=["a"],
                                  message="?", detail={})))
    assert advisories == []
    assert blocking and blocking[0]["kind"] == "SOMETHING_NEW"


def test_a_real_ambiguity_still_reaches_the_operator_as_a_stop():
    project, _ = lifecycle_project()
    blocking, _ = resolve_planning_ambiguities(
        project, ambiguities(unclassified(), missing_identity("readParcel")))
    assert [item["kind"] for item in blocking] == ["MISSING_INSTANCE_IDENTITY"]
