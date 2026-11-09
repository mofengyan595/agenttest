"""The instance contract reaches the model only when one operation really repeats."""

import json

from agenttest.product_design import (
    DESIGN_PROMPT,
    INSTANCE_BINDING_PROMPT,
    TestDesign,
    _needs_instance_bindings,
    design_prompt,
    strip_instance_bindings,
)
from agenttest.product_schema import constrain_product_steps

POLICY = {"id": "web_rule_2", "operation_id": "getItem", "roles": ["author"],
          "binding_rules": [], "closed_arguments": False}


def requirement(source_id, operation_id="getItem"):
    return {"source_id": source_id, "source_quote": "读一次。", "disposition": "required",
            "actions": [{"operation_id": operation_id, "role": "author"}], "before": [],
            "same_resource": [], "response_checks": []}


def step_properties(requirements_):
    schema = TestDesign.model_json_schema()
    constrain_product_steps(schema, {"policies": [POLICY], "request_requirements": requirements_,
                                     "roles": ["author"],
                                     "design_operations": ["getItem"]})
    generic = schema["$defs"]["GenericDraftStep"]
    return (generic.get("anyOf") or [generic])[0]["properties"]


def test_the_instance_paragraph_is_added_only_for_a_repeating_operation():
    once = [requirement("request_1")]
    twice = [requirement("request_1"), requirement("request_2")]
    assert _needs_instance_bindings({"request_requirements": once}) is False
    assert _needs_instance_bindings({"request_requirements": twice}) is True
    assert design_prompt({"request_requirements": once}) == DESIGN_PROMPT
    assert design_prompt({"request_requirements": twice}) == (
        DESIGN_PROMPT + INSTANCE_BINDING_PROMPT)
    # Distinct operations never trigger the extra contract.
    mixed = [requirement("request_1", "createItem"), requirement("request_2", "getItem")]
    assert design_prompt({"request_requirements": mixed}) == DESIGN_PROMPT


def test_the_focus_prompt_names_instance_addressed_ordering():
    """A repeated operation across clauses must be ordered by instance, not as one set."""
    from agenttest.product_focus import FOCUS_PROMPT

    for phrase in ("earlier_source_id", "later_source_id", "earlier_index", "later_index",
                   "different instance", "false dependency cycle", "Array position"):
        assert phrase in FOCUS_PROMPT, phrase
    # The base requirement schema stays unchanged unless an order edge is present.
    from agenttest.request_requirements import request_atoms, requirement_schema

    atoms = request_atoms("先读取该条目，然后删除该条目。")
    schema = requirement_schema({"approved_ids": ["getItem", "deleteItem"],
                                 "user_task": "先读取该条目，然后删除该条目。",
                                 "request_atoms": atoms, "request_requirements": []})
    order = schema["$defs"]["RequestOrder"]
    assert order["properties"]["earlier_source_id"]["description"]
    assert order["properties"]["later_index"]["description"]
    assert "earlier_index" not in order["required"]


def test_the_paragraph_teaches_instances_the_204_rule_and_the_two_kinds_of_dependency():
    text = INSTANCE_BINDING_PROMPT
    for phrase in ("requirement_source_id", "requirement_action_index", "one request instance",
                   "204", "no body", "never supply an id", "Written order",
                   "data reference", "array position"):
        assert phrase in text, phrase
    # The historical contract must stay untouched for requests without a repetition.
    assert "requirement_source_id" not in DESIGN_PROMPT


def test_a_repeating_operation_keeps_named_and_nullable_binding_slots():
    properties = step_properties([requirement("request_1"), requirement("request_2")])
    source = properties["requirement_source_id"]
    index = properties["requirement_action_index"]
    assert source["type"] == ["string", "null"]
    assert index["type"] == ["integer", "null"]
    assert index["minimum"] == 0 and index["maximum"] == 19
    assert "source_id" in source["description"]
    assert "不同实例" in source["description"]
    assert "0" in index["description"]
    # Without a repetition the slots are removed from the wire entirely.
    schema = TestDesign.model_json_schema()
    assert "requirement_source_id" in schema["$defs"]["GenericDraftStep"]["properties"]
    strip_instance_bindings(schema)
    stripped = schema["$defs"]["GenericDraftStep"]["properties"]
    assert "requirement_source_id" not in stripped
    assert "requirement_action_index" not in stripped
    assert json.dumps(properties, ensure_ascii=False) != json.dumps(stripped, ensure_ascii=False)
