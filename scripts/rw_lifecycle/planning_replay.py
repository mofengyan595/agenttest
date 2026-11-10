"""Offline replay of the archived M2 planning failure.

This script reads the preserved Focus evidence for position M2 and reconstructs the
construction stage offline. It never calls a model or the business target, and it never
writes the archived files.

The raw Design reply was not preserved; the archived diagnostic (``plan.steps.1`` and
``plan.steps.2``) proves only *which* nodes took part in the cycle. The unbound four-step
proposal below is therefore an explicit reconstruction of the minimal realization described
by the diagnostic, not a byte-for-byte replay of the model reply. The IR path uses the same
four clause bindings that a correct proposal must declare.

Run from the repository root:

    python scripts/rw_lifecycle/planning_replay.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttest.diagnostics import ValidationFailure  # noqa: E402
from agenttest.plan_construction import construct_plan  # noqa: E402
from agenttest.planning_compiler import compile_design  # noqa: E402
from agenttest.planning_ir import build_planning_ir  # noqa: E402
from agenttest.request_requirements import request_atoms  # noqa: E402

EVIDENCE = ROOT / "docs/evidence/rw-acceptance-m2-8845c31/evidence.json"


def approved_config():
    """The approved loopback fixture configuration used by position M2."""
    def policy(policy_id, operation_id, statuses, rules, **extra):
        return dict(id=policy_id, operation_id=operation_id, roles=["author"],
                    assertions=dict(reviewed=True, source="operator",
                                    status=dict(codes=statuses)),
                    binding_rules=rules, **extra)

    create_rules = [
        dict(location="body", name="name", literal_schema=dict(enum=["first", "second"])),
        dict(location="headers", name="Idempotency-Key",
             literal_schema=dict(enum=["rw-lifecycle-key-0001"])),
    ]
    path_rule = [dict(location="path_parameters", name="itemId",
                      source_policy_id="createItem", source_selector="$.id")]
    policies = [
        policy("createItem", "createItem", [201], create_rules),
        policy("getItem", "getItem", [200], path_rule),
        policy("getItem_expectation_1", "getItem", [404], path_rule),
        policy("deleteItem", "deleteItem", [204], path_rule),
    ]
    operations = [
        dict(id="createItem", method="POST", path="/items"),
        dict(id="getItem", method="GET", path="/items/{itemId}"),
        dict(id="deleteItem", method="DELETE", path="/items/{itemId}"),
    ]
    lifecycles = [dict(create_operation="createItem", delete_operation="deleteItem",
                       path_parameter="itemId", id_selector="$.id")]
    return policies, operations, lifecycles


def step(key, operation, *, source=None, clause=None, body=False):
    arguments = []
    if body:
        arguments.append(dict(location="body", name="name", value="first", from_step=None,
                              select=None))
    if source is not None:
        arguments.append(dict(location="path_parameters", name="itemId", value=None,
                              from_step=source, select="$.id"))
    node = dict(id=key, operation_id=operation, role="author", policy_id=operation,
                arguments=arguments)
    if clause is not None:
        node["requirement_source_id"] = clause
        node["requirement_action_index"] = 0
    return node


def legacy_proposal():
    """Four unbound steps: the minimal realization the archived diagnostic points at."""
    return dict(name="archived-m2-unbound", steps=[
        step("create_1", "createItem", body=True),
        step("get_2", "getItem", source="create_1"),
        step("delete_3", "deleteItem", source="create_1"),
        step("get_4", "getItem", source="create_1"),
    ])


def bound_proposal():
    """The same four requests with each clause bound to its instance."""
    return dict(name="archived-m2-bound", steps=[
        step("create_1", "createItem", clause="request_1", body=True),
        step("get_3", "getItem", clause="request_3", source="create_1"),
        step("delete_5", "deleteItem", clause="request_5", source="create_1"),
        step("get_7", "getItem", clause="request_7", source="create_1"),
    ])


def main() -> int:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    decoded = evidence["focus"][0]["decoded"]
    requirements = decoded["requirements"]
    inputs = decoded.get("input_requirements", [])
    text = evidence["text"]
    atoms = request_atoms(text)
    policies, operations, lifecycles = approved_config()
    context = dict(policies=policies, operations=operations,
                   request_requirements=requirements, request_atoms=atoms,
                   resource_lifecycles=lifecycles)

    print("archived position:", evidence["case"], "verdict:", evidence["verdict"])
    print("focus clauses:", len(requirements), "atoms:", len(atoms))

    legacy_error = None
    try:
        construct_plan(legacy_proposal(), dict(context, planning_ir=None))
    except ValidationFailure as error:
        legacy_error = error
    if legacy_error is None:
        print("LEGACY: unexpectedly compiled; the historical failure was not reproduced")
        return 1
    fields = sorted(legacy_error.diagnostic.fields)
    print("legacy failure:", legacy_error.diagnostic.code, fields)
    if legacy_error.diagnostic.code != "CONSTRUCTION_DEPENDENCY_CYCLE":
        print("LEGACY: wrong failure code")
        return 1
    if fields != ["plan.steps.1", "plan.steps.2"]:
        print("LEGACY: cycle fields differ from the archived diagnostic")
        return 1

    ir = build_planning_ir(requirements, atoms, operations=operations, policies=policies,
                           target_lifecycles=lifecycles, read_operations={"getItem"},
                           input_requirements=inputs)
    print("ir coverage:", json.dumps(ir.coverage, sort_keys=True))
    print("ir instances:", [(item.instance_id, item.operation_id, item.source_id)
                            for item in ir.instances])
    if len(ir.instances) != 4 or ir.ambiguities:
        print("IR: expected four unambiguous request instances")
        print("ambiguities:", [item.model_dump(mode="json") for item in ir.ambiguities])
        return 1
    order = list(ir.order)
    print("ir order:", order)
    if order != [item.instance_id for item in ir.instances]:
        print("IR: dependency order is not the requested create/read/delete/readback chain")
        return 1

    plan, stats = compile_design(ir, bound_proposal(), context)
    print("compiled order:", [item["id"] for item in plan["steps"]])
    print("compiled policies:", [item["policy_id"] for item in plan["steps"]])
    print("edge reasons:", json.dumps(stats["edge_reasons"], sort_keys=True))
    expected = ["create_1", "get_3", "delete_5", "get_7"]
    if [item["id"] for item in plan["steps"]] != expected:
        print("COMPILER: wrong deterministic order")
        return 1
    for item in plan["steps"][1:]:
        binding = next(argument for argument in item["arguments"]
                       if argument["name"] == "itemId")
        if binding["from_step"] != "create_1" or binding["select"] != "$.id":
            print("COMPILER: dynamic id is not bound to the create step:", binding)
            return 1
    readback = stats["edge_reasons"].get("get_7<=delete_5", "")
    if not readback.startswith("RESOURCE_ABSENT"):
        print("COMPILER: readback is not ordered after the delete:", readback)
        return 1
    print("REPLAY OK: legacy cycle reproduced; IR compiles create->read->delete->readback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
