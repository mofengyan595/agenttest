"""Archived third-round planning failures become deterministic regression cases."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import plan_v2  # noqa: E402
import plan_v3  # noqa: E402

from agenttest.diagnostics import ValidationFailure  # noqa: E402
from agenttest.product_focus import FocusSelection  # noqa: E402
from agenttest.request_requirements import (  # noqa: E402
    RequestRequirement,
    RequiredAction,
    evidence_fragment,
    repeated_read_order,
    request_atoms,
    textual_order_edge,
    validate_requirement_links,
)

CREATE = "createItem"
READ = "getItem"


def atom_map(text):
    return {atom["source_id"]: atom for atom in request_atoms(text)}


def requirement(atom, actions):
    return RequestRequirement(source_id=atom["source_id"], source_quote=atom["text"],
                              disposition="required", actions=actions, before=[],
                              same_resource=[])


def context(atoms):
    return dict(user_task="", request_atoms=list(atoms.values()),
                approved_ids=[CREATE, READ], managed_authentication=[dict(identity="owner")],
                resource_dependencies=[dict(from_operation=CREATE, operation_id=READ)],
                grouped_index=[dict(operations=[dict(id=CREATE, method="POST"),
                                                dict(id=READ, method="GET")])],
                response_expectations={})


def validate(text, actions_by_clause):
    atoms = atom_map(text)
    facts = context(atoms)
    chosen = [requirement(atoms[key], [RequiredAction(operation_id=operation, role=role)
                                       for operation, role in actions])
              for key, actions in actions_by_clause.items()]
    selection = FocusSelection(interpretation="检查原文对应的全部行为", mode="selected",
                               operation_ids=[CREATE, READ], question=None,
                               pending_questions=[], not_covered=[], requirements=chosen)
    validate_requirement_links(selection.requirements, facts, selection)
    return {item.source_id: item for item in chosen}


def test_third_round_repeated_ordered_read_needs_no_self_edge():
    """T3 ``request_4`` failed because a repeated read can never carry a ``before`` edge."""
    atom = atom_map(plan_v2.FINAL_CASES[2]["text"])["request_4"]
    item = requirement(atom, [RequiredAction(operation_id=READ, role=None)])
    assert atom["ordered"] and "读取" in atom["text"]
    assert textual_order_edge(atom, item, [CREATE, READ]) is None
    assert repeated_read_order(atom, item, [CREATE, READ])


def test_third_round_verbless_ordering_fragment_is_evidence_not_an_action():
    """T2-v3 ``request_2`` was ``在同一请求成功之后``: a split fragment with no verb."""
    fragment = atom_map(plan_v3.FINAL_CASES[1]["text"])["request_3"]
    assert evidence_fragment(fragment)
    assert not evidence_fragment({"text": "在同一请求成功之后核对成功条数是一"})
    assert not evidence_fragment({"text": "在同一请求成功之后确认返回 200"})
    assert not evidence_fragment({"text": "在创建成功之后把名称改为 second"})


def test_repeated_read_and_evidence_fragments_need_no_model_witness():
    items = validate("创建条目；在同一请求成功之后，创建第二个条目，然后读取这个条目，"
                     "最后再次读取这个条目。",
                     {"request_1": [(CREATE, "owner")],
                      "request_2": [],
                      "request_3": [(CREATE, "owner")],
                      "request_4": [(READ, None)],
                      "request_5": [(READ, None)]})
    assert items["request_2"].actions == []
    assert items["request_5"].before == []


def test_verb_bearing_clause_still_needs_its_action_witness():
    with pytest.raises(ValidationFailure, match="FOCUS_REQUIREMENT_WITNESS_MISSING") as error:
        validate("创建条目，然后读取这个条目。",
                 {"request_1": [(CREATE, "owner")], "request_2": []})
    assert "requirement_map.request_2.actions" in error.value.diagnostic.fields


def test_assertion_fragment_still_needs_the_reviewed_continuation_rule():
    with pytest.raises(ValidationFailure, match="FOCUS_REQUIREMENT_WITNESS_MISSING") as error:
        validate("创建条目；在同一请求成功之后核对成功条数是一，然后读取这个条目。",
                 {"request_1": [(CREATE, "owner")], "request_2": [], "request_3": [(READ, None)]})
    assert any(field.endswith("actions") for field in error.value.diagnostic.fields)
