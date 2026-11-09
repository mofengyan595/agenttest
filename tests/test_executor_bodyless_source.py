"""A data reference never reads a field from a step whose response cannot carry one."""

import pytest

from agenttest.diagnostics import ValidationFailure
from agenttest.dsl import ArgumentBinding, Scenario, Step
from agenttest.executor import _refuse_bodyless_sources
from agenttest.policies import AssertionPolicy, RegisteredPolicy


def policy(policy_id, operation_id, codes):
    from agenttest.dsl import ExpectedStatus

    return RegisteredPolicy(id=policy_id, operation_id=operation_id, roles=[None],
                            business_scenario="isolated",
                            assertions=AssertionPolicy(status=ExpectedStatus(codes=codes)))


def scenario(source="d"):
    return Scenario(name="readback", steps=[
        Step(id="d", operation_id="deleteItem", policy_id="web_rule_delete"),
        Step(id="r", operation_id="getItem", policy_id="web_rule_read",
             path_parameters={"itemId": ArgumentBinding(from_step=source, select="$.id")})])


def test_a_delete_source_is_refused_by_the_executor_backstop():
    policies = {"d": policy("web_rule_delete", "deleteItem", [204]),
                "r": policy("web_rule_read", "getItem", [200])}
    with pytest.raises(ValidationFailure) as error:
        _refuse_bodyless_sources(scenario(), policies)
    assert error.value.diagnostic.code == "DEPENDENCY_SOURCE_HAS_NO_BODY"
    assert error.value.diagnostic.fields == ["steps.r.itemId", "steps.d.policy_id"]
    assert error.value.construction == dict(step="r", argument="itemId", source_step="d",
                                            source_statuses=[204])


def test_a_creation_source_with_a_body_is_allowed():
    policies = {"c": policy("web_rule_create", "createItem", [201]),
                "r": policy("web_rule_read", "getItem", [200])}
    built = scenario(source="c")
    _refuse_bodyless_sources(built, policies)  # no raise: 201 can carry the id
    assert built.steps[1].path_parameters["itemId"].select == "$.id"
    # A mixed success set that includes a body-bearing status is still a real body source.
    policies["m"] = policy("web_rule_mixed", "deleteItem", [200, 204])
    _refuse_bodyless_sources(scenario(source="m"), policies)
