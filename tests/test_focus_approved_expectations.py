"""Approved input alternatives, approved denial expectations and textual order witnesses."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import authority  # noqa: E402
import plan_v2  # noqa: E402

from agenttest.contracts import load_openapi  # noqa: E402
from agenttest.diagnostics import ValidationFailure  # noqa: E402
from agenttest.generic_intake import GenericSetup, build_generic_project  # noqa: E402
from agenttest.product_inputs import _check_quote  # noqa: E402
from agenttest.request_requirements import (  # noqa: E402
    RequestRequirement,
    RequiredAction,
    RequiredOrder,
    textual_order_edge,
)

TOKENS = {f"AGENTTEST_TARGET_RW_{alias.upper()}_TOKEN": f"preparation-{alias}"
          for alias in ("author", "editor", "auditor", "outsider")}


def project(document=None, mutate=None):
    setup = authority.setup()
    if mutate:
        mutate(setup)
    text = document or authority.document()
    return build_generic_project(
        "rw_variants", text, load_openapi(text),
        GenericSetup.model_validate(setup), environ=TOKENS)


def policies(item):
    return [(policy.operation_id, tuple(policy.roles),
             tuple(policy.assertions.status.codes), policy) for policy in
            item.registry.definitions()]


def operation(setup, name):
    return next(item for item in setup["operations"] if item["operation_id"] == name)


def test_declaration_matches_the_next_round_plan():
    rows = [dict(operation=operation_id, roles=list(roles), statuses=list(statuses))
            for operation_id, roles, statuses, _ in policies(project())]
    result = plan_v2.readiness(rows)
    assert result["ready"], result["missing"]


def test_generated_input_alternatives_are_a_closed_approved_set():
    rows = {statuses: policy for operation_id, _, statuses, policy in policies(project())
            if operation_id == "createItem"}
    rule = next(rule for rule in rows[(201,)]["author"].binding_rules
                if rule.name == "name") if False else next(
        rule for rule in rows[(201,)].binding_rules if rule.name == "name")
    assert rule.literal_schema["enum"] == ["first", "second"]
    header = next(rule for rule in rows[(201,)].binding_rules if rule.name == "Idempotency-Key")
    assert header.location == "headers" and header.literal_schema["enum"] == [
        "rw-lifecycle-key-0001"]


def test_conflict_expectation_reuses_an_approved_value_and_its_own_status():
    conflict = next(policy for operation_id, _, statuses, policy in policies(project())
                    if operation_id == "createItem" and statuses == (409,))
    assert not conflict.negative
    rule = next(rule for rule in conflict.binding_rules if rule.name == "name")
    assert rule.literal_schema["enum"] == ["second"]


def test_absent_resource_read_back_is_an_approved_expectation():
    statuses = {statuses for operation_id, roles, statuses, _ in policies(project())
                if operation_id == "getItem" and roles == ("author",)}
    assert {(200,), (404,)} <= statuses


def test_unsafe_variants_are_refused_at_build_time():
    def repoint(setup):
        operation(setup, "createItem")["expectations"][0]["statuses"] = [201]

    with pytest.raises(ValueError, match="EXPECTATION_STATUS_NOT_APPROVABLE"):
        project(mutate=repoint)

    def unapproved_value(setup):
        operation(setup, "createItem")["expectations"][0]["arguments"][0]["value"] = "third"

    with pytest.raises(ValueError, match="EXPECTATION_VALUE_NOT_APPROVED"):
        project(mutate=unapproved_value)

    def implicit_value(setup):
        operation(setup, "createItem")["expectations"][0]["arguments"] = []

    implicit_value.__name__ = "drop"  # expectations may keep the base value
    assert project(mutate=implicit_value)

    def without_negative_approval(setup):
        setup["approve_negative_tests"] = False

    with pytest.raises(ValueError, match="EXPECTATION_REQUIRES_APPROVED_NEGATIVE_TESTS"):
        project(mutate=without_negative_approval)


def test_undocumented_expectation_status_is_refused():
    document = json.loads(authority.document())
    document["paths"]["/items"]["post"]["responses"].pop("409")
    with pytest.raises(ValueError, match="EXPECTATION_STATUS_NOT_DOCUMENTED"):
        project(document=json.dumps(document, ensure_ascii=False))


def test_mutation_denials_need_explicit_negative_approval_and_a_different_identity():
    def drop_approval(setup):
        setup["approve_negative_tests"] = False
        for item in setup["operations"]:
            item["expectations"] = []

    with pytest.raises(ValueError, match="ACCESS_CHECK_REQUIRES_APPROVED_READONLY_ROLE"):
        project(mutate=drop_approval)

    def same_identity(setup):
        operation(setup, "updateItem")["access_checks"][0]["role"] = "author"

    with pytest.raises(ValueError, match="ACCESS_CHECK_REQUIRES_APPROVED_READONLY_ROLE"):
        project(mutate=same_identity)

    def unsafe_status(setup):
        operation(setup, "updateItem")["access_checks"][0]["statuses"] = [200]

    with pytest.raises(ValueError, match="ACCESS_CHECK_REQUIRES_APPROVED_READONLY_ROLE"):
        project(mutate=unsafe_status)


def test_approved_alternative_is_accepted_only_inside_the_enum():
    enum = {"type": "string", "enum": ["first", "second"]}
    # An approved alternative does not need to repeat the user's words.
    _check_quote("second", enum, "创建名为 first 的条目")
    with pytest.raises(ValidationFailure) as error:
        _check_quote("third", enum, "创建名为 third 的条目")
    assert error.value.diagnostic.code == "FOCUS_INPUT_VALUE_UNAPPROVED"
    # Free text without an approved set still has to preserve the source words.
    with pytest.raises(ValidationFailure) as error:
        _check_quote("second", {"type": "string"}, "创建名为 first 的条目")
    assert error.value.diagnostic.code == "FOCUS_INPUT_TEXT_CHANGED"


def requirement(**overrides):
    values = dict(source_id="request_3", source_quote="然后再用匿名身份读取该条目",
                  disposition="required", actions=[RequiredAction(operation_id="getItem",
                                                                  role=None)],
                  before=[], same_resource=[])
    values.update(overrides)
    return RequestRequirement(**values)


def test_ordered_clause_derives_its_adjacency_witness_from_clause_order():
    item = requirement()
    previous = dict(operation_id="createItem", source_id="request_2", index=0)
    edge = textual_order_edge({"ordered": True}, item, [previous])
    assert edge == RequiredOrder(earlier="createItem", later="getItem",
                                 earlier_source_id="request_2", earlier_index=0,
                                 later_source_id="request_3", later_index=0)


def test_derivation_never_invents_or_overrides_a_witness():
    ordered = {"ordered": True}
    create = dict(operation_id="createItem", source_id="request_2", index=0)
    assert textual_order_edge({"ordered": False}, requirement(), [create]) is None
    assert textual_order_edge(ordered, requirement(), []) is None
    kept = requirement(before=[RequiredOrder(earlier="updateItem", later="getItem")])
    assert textual_order_edge(ordered, kept, [create]) is None
    # The same operation in another clause is another instance: the user's clause order still
    # yields a real edge, and the two sides stay distinguishable.
    repeated = requirement()
    same_operation = dict(operation_id="getItem", source_id="request_2", index=0)
    edge = textual_order_edge(ordered, repeated, [same_operation])
    assert edge is not None and edge.earlier_instance != edge.later_instance
    no_actions = requirement(actions=[])
    assert textual_order_edge(ordered, no_actions, [create]) is None

def test_variant_values_are_admitted_by_their_own_approved_set_only():
    """A base value must not be re-validated against a conflicting policy's narrow set."""
    from agenttest.product_inputs import InputRequirement, constrain_input_schema

    def requirement(value):
        return InputRequirement(operation_id="createItem", location="body", name="name",
                                path=[], value=value, source_quote="创建名为 first 的条目")

    rule = dict(location="body", name="name")
    base = {"type": "string", "enum": ["first", "second"]}
    conflict = {"type": "string", "enum": ["second"]}
    alternatives = {("createItem", "body", "name", ()): ["first", "second"]}

    confined = constrain_input_schema(conflict, "createItem", rule, [requirement("first")],
                                      alternatives=alternatives)
    assert confined["enum"] == ["second"]
    admitted = constrain_input_schema(base, "createItem", rule, [requirement("first")],
                                      alternatives=alternatives)
    assert admitted["enum"] == ["first"]
    # A value no approved variant admits is still refused.
    with pytest.raises(ValidationFailure) as error:
        constrain_input_schema(conflict, "createItem", rule, [requirement("third")],
                               alternatives=alternatives)
    assert error.value.diagnostic.code == "OPENAPI_VALUE_ENUM"
    with pytest.raises(ValidationFailure):
        constrain_input_schema(conflict, "createItem", rule, [requirement("third")])
    # An open (non-closed) rule keeps receiving the requested value.
    open_rule = constrain_input_schema({"type": "string"}, "createItem", rule,
                                       [requirement("first")], alternatives=alternatives)
    assert open_rule["enum"] == ["first"]


def test_approved_values_union_covers_every_declared_variant():
    from agenttest.product_inputs import approved_values

    policies = [
        dict(operation_id="createItem", binding_rules=[
            dict(location="body", name="name", literal_schema={"type": "string",
                                                              "enum": ["first", "second"]})]),
        dict(operation_id="createItem", binding_rules=[
            dict(location="body", name="name", literal_schema={"type": "string",
                                                              "enum": ["second"]})]),
    ]
    assert approved_values(policies) == {("createItem", "body", "name", ()):
                                         ["first", "second"]}

def test_execution_still_rejects_a_value_used_with_the_wrong_variant():
    """The design-time union must not weaken the per-policy execution gate."""
    from agenttest.diagnostics import ValidationFailure as Failure
    from agenttest.dsl import ArgumentBinding, Step

    built = project()
    definitions = list(built.registry.definitions())

    def policy(statuses):
        return next(item for item in definitions
                    if item.operation_id == "createItem"
                    and list(item.assertions.status.codes) == statuses)

    def step(item, name):
        return Step(id="s1", operation_id="createItem", role="author", policy_id=item.id,
                    body={"name": ArgumentBinding(value=name)},
                    headers={"Idempotency-Key": ArgumentBinding(
                        value="rw-lifecycle-key-0001")})

    base, conflict = policy([201]), policy([409])
    built.registry.resolve(step(base, "first"), built.target.business_scenario)
    built.registry.resolve(step(base, "second"), built.target.business_scenario)
    built.registry.resolve(step(conflict, "second"), built.target.business_scenario)
    for abused in ("first", "third"):
        with pytest.raises(Failure) as error:
            built.registry.resolve(step(conflict, abused), built.target.business_scenario)
        assert error.value.diagnostic.code == "OPENAPI_VALUE_ENUM"
