"""P3-B2.4 A3: reference candidates come from approved identity, not from array position.

The archived sample is the P3-B2.2 batch-7 proposal: its negative variant replaces a parcel that
an approved ``createParcel`` step has to create, and the model wrote an id that does not exist.
The old diagnosis advertised only nodes declared *before* the consumer, so it answered "none"
even when the approved producer was right there in the array.
"""

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.plan_construction import construct_plan

BINDING = dict(location="path_parameters", name="parcelId", source_policy_id="createParcel",
               source_selector="$.id", role_relation="anonymous")


def context(*, lifecycles=(), prerequisites=None, binding=True):
    consuming = dict(id="negative_variant", operation_id="replaceParcel", roles=[None])
    if binding:
        consuming["binding_rules"] = [BINDING]
    producers = [dict(id="createParcel", operation_id="createParcel", roles=[None]),
                 dict(id="replaceParcel", operation_id="replaceParcel", roles=[None])]
    if prerequisites:
        producers[0]["prerequisite_policy_ids"] = list(prerequisites)
        consuming["prerequisite_policy_ids"] = ["needs_negative_variant"]
    return dict(policies=[consuming, *producers], authentication=[],
                resource_lifecycles=list(lifecycles))


def step(key, operation="createParcel", policy="createParcel", source=None,
         name="parcelId", select="$.id"):
    arguments = ([] if source is None else [dict(location="path_parameters", name=name,
                                                 value=None, from_step=source, select=select)])
    return dict(id=key, operation_id=operation, role=None, policy_id=policy, arguments=arguments)


def consumer(source, **kwargs):
    return step("replaceQuantityOutOfBound", "replaceParcel", "negative_variant", source, **kwargs)


def reject(steps, ctx):
    with pytest.raises(ValidationFailure) as caught:
        construct_plan(dict(name="archived-negative-batch", steps=steps), ctx)
    assert caught.value.diagnostic.code == "CONSTRUCTION_REFERENCE_INVALID"
    return caught.value.diagnostic


def test_a_producer_declared_later_is_advertised_with_its_order_constraint():
    diagnostic = reject([consumer("createParcelTypo"), step("createBaseParcel")], context())
    detail = diagnostic.detail
    assert detail["reference"] == "SOURCE_UNKNOWN"
    assert detail["allowed_from_steps"] == ["createBaseParcel"]
    assert detail["direct_from_steps"] == []
    assert detail["reordered_from_steps"] == ["createBaseParcel"]
    assert "声明在本步骤之后" in detail["order_constraint"]
    assert "from_step" in detail["order_constraint"]
    assert diagnostic.fields == ["plan.steps.0.arguments.0.from_step"]


def test_an_earlier_producer_is_direct_and_needs_no_order_note():
    diagnostic = reject([step("createBaseParcel"), consumer("createParcelTypo")], context())
    detail = diagnostic.detail
    assert detail["allowed_from_steps"] == ["createBaseParcel"]
    assert detail["direct_from_steps"] == ["createBaseParcel"]
    assert detail["reordered_from_steps"] == []
    assert "order_constraint" not in detail


def test_a_negative_variant_without_binding_rules_uses_the_approved_lifecycle_identity():
    lifecycles = [dict(create_operation="createParcel", path_parameter="parcelId",
                       id_selector="$.id")]
    diagnostic = reject([consumer("createParcelTypo"), step("createBaseParcel")],
                        context(lifecycles=lifecycles, binding=False))
    assert diagnostic.detail["allowed_from_steps"] == ["createBaseParcel"]
    assert diagnostic.detail["reordered_from_steps"] == ["createBaseParcel"]


def test_self_reference_is_refused_and_a_legal_producer_is_still_named():
    diagnostic = reject([step("createBaseParcel"),
                         consumer("replaceQuantityOutOfBound")], context())
    detail = diagnostic.detail
    assert detail["reference"] == "SELF_REFERENCE"
    assert "replaceQuantityOutOfBound" not in detail["allowed_from_steps"]
    assert detail["allowed_from_steps"] == ["createBaseParcel"]


def test_an_unknown_id_without_any_approved_producer_keeps_the_set_empty():
    diagnostic = reject([consumer("createParcelForNegative8")], context())
    detail = diagnostic.detail
    assert detail["allowed_from_steps"] == []
    assert detail["direct_from_steps"] == [] and detail["reordered_from_steps"] == []
    assert "order_constraint" not in detail


def test_another_approved_operation_is_not_a_candidate_for_this_slot():
    lifecycles = [dict(create_operation="createParcel", path_parameter="parcelId",
                       id_selector="$.id")]
    diagnostic = reject([consumer("missing"), step("replaceOnly", "replaceParcel",
                                                   "replaceParcel")],
                        context(lifecycles=lifecycles, binding=False))
    assert diagnostic.detail["allowed_from_steps"] == []


def test_a_producer_that_depends_on_the_consumer_is_blocked_not_offered():
    ctx = context(prerequisites=["needs_negative_variant"])
    ctx["policies"][0]["prerequisite_policy_ids"] = ["createParcel"]
    ctx["policies"][1]["prerequisite_policy_ids"] = ["negative_variant"]
    diagnostic = reject([consumer("missing"), step("createBaseParcel")], ctx)
    detail = diagnostic.detail
    assert detail["allowed_from_steps"] == []
    assert detail["blocked_from_steps"] == [dict(step_id="createBaseParcel", reason="CYCLE")]
    assert "循环" in detail["order_constraint"]


def test_a_producer_with_a_missing_approved_prerequisite_is_blocked():
    ctx = context(prerequisites=["needs_negative_variant"])
    ctx["policies"][0]["prerequisite_policy_ids"] = []
    ctx["policies"][1]["prerequisite_policy_ids"] = ["approved_prerequisite_absent"]
    diagnostic = reject([consumer("missing"), step("createBaseParcel")], ctx)
    detail = diagnostic.detail
    assert detail["allowed_from_steps"] == []
    assert detail["blocked_from_steps"] == [
        dict(step_id="createBaseParcel", reason="PREREQUISITE_MISSING")]


def test_a_forward_reference_is_still_constructed_first_and_accepted():
    plan, _ = construct_plan(dict(name="forward", steps=[
        consumer("createBaseParcel"), step("createBaseParcel")]), context())
    assert [row["id"] for row in plan["steps"]] == ["createBaseParcel",
                                                    "replaceQuantityOutOfBound"]


def test_a_cycle_through_a_policy_prerequisite_is_still_refused():
    ctx = context()
    ctx["policies"][1]["prerequisite_policy_ids"] = ["negative_variant"]
    with pytest.raises(ValidationFailure) as caught:
        construct_plan(dict(name="cycle", steps=[consumer("createBaseParcel"),
                                                 step("createBaseParcel")]), ctx)
    assert caught.value.diagnostic.code == "CONSTRUCTION_DEPENDENCY_CYCLE"
    assert sorted(caught.value.construction["cycle"]) == ["createBaseParcel",
                                                          "replaceQuantityOutOfBound"]


def test_a_step_from_another_case_is_never_a_candidate_here():
    diagnostic = reject([consumer("group_2_seed")], context())
    assert diagnostic.detail["allowed_from_steps"] == []
