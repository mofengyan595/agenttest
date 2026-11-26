"""Frozen second-contract evidence for the isolated RequestedCheck projection."""

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from agenttest.contracts import load_openapi
from agenttest.focus_compiler import (
    ConfirmedCheckScope,
    FocusAvailability,
    compile_requested_checks,
)
from agenttest.policies import PolicyRegistry, RegisteredPolicy
from agenttest.project_goals import GoalCatalog, GoalProject, RegisteredProject
from agenttest.safety import TargetConfig

FIXTURE = Path(__file__).with_name("fixtures") / "focus-compiler-holdout.json"
FROZEN = "e2f8a361a9afb1a00b03cbe19711ea2c296a42217ef370c77d9053718fa9c37d"
HOLDOUT = json.loads(FIXTURE.read_bytes())


@pytest.fixture
def project():
    document = json.dumps(HOLDOUT["contract"]).encode()
    contract = load_openapi(document)
    target = TargetConfig(base_url="http://127.0.0.1:18766", environment="isolated",
        allow_mutations=True, allowed_operations=[o.operation_id for o in contract.operations],
        roles={role: {"bearer_env": f"OBSERVATORY_{role.upper()}_TOKEN"}
               for role in HOLDOUT["roles"]}, business_scenario="observatory")
    bases = {key: dict(id=key, operation_id=operation, roles=[role],
             business_scenario=target.business_scenario,
             assertions=dict(reviewed=True, source="operator fixture", status=dict(codes=[200])),
             binding_rules=[dict(location=location, name=name, literal_schema={})
                            for name in names])
             for key, operation, role, location, names in HOLDOUT["bases"]}
    locations = {key: location for key, _, _, location, _ in HOLDOUT["bases"]}
    negatives = [dict(bases[base], id=key,
        assertions=dict(reviewed=True, source="operator fixture", status=dict(codes=[422])),
        negative=dict(base_policy_id=base, location=locations[base], name=field, kind=kind,
            value=value, violation_codes=[code], allow_value_variants=variants))
        for key, base, field, kind, value, code, variants in HOLDOUT["negatives"]]
    registry = PolicyRegistry([RegisteredPolicy.model_validate(row)
                               for row in [*bases.values(), *negatives]])
    return GoalProject(RegisteredProject(schema_version="agenttest.registered-project.v1",
        id="observatory", contract="openapi.json", target="target.json", policies="policies.json",
        goals="goals.json"), document, contract, target, registry,
        GoalCatalog(resource_aliases=["观测站"], goals=[dict(id="pulse", aliases=["检查心跳"],
                    policy_id="pulse_public", role=None)]))


def availability(project, case):
    return FocusAvailability(project.identity(), frozenset(project.target.allowed_operations),
        frozenset(project.target.roles.keys() - set(case.get("not_ready_roles", []))),
        frozenset(tuple(pair) for pair in case.get("pending", [])))


def selection_for(project, case):
    if "selected" not in case:
        return None
    actions = [dict(operation_id=op, role=role, instance_id=rest[0] if rest else None)
               for op, role, *rest in case["selected"]]
    return ConfirmedCheckScope(selection_id="observatory-current-selection",
        confirmation_id=None if case.get("unconfirmed") else "operator-confirmation-r1",
        project_identity="stale" if case.get("stale") else project.identity(),
        operations=tuple(actions), include_negatives=case.get("include_negatives", True),
        variants=case.get("values", {}))


@pytest.mark.parametrize("case", HOLDOUT["cases"], ids=lambda case: case["id"])
def test_frozen_holdout(project, case):
    assert sha256(FIXTURE.read_text(encoding="utf-8").encode()).hexdigest() == FROZEN
    selection = selection_for(project, case)
    identity = project.identity()
    result = compile_requested_checks(project, availability(project, case),
                                      text=case.get("text", ""), selection=selection)
    assert result.status == case["status"]
    assert {row.policy_id for row in result.mappings} == set(case["mapped"])
    assert len(result.executable) == case["executable"]
    assert project.identity() == identity
    if case.get("reason"):
        assert case["reason"] in {result.reason, *(row.reason for row in result.pending)}
    for row in result.mappings:
        assert row.source.project_identity == identity
        if selection is None:
            assert row.source.source_ref == "source_3"
            assert row.source.source_quote == "正常路径和非法入参都按已批准的规则执行"
        else:
            assert row.source.source_quote is None and row.source.source_ref is None
            assert row.source.selection_id == selection.selection_id
            assert row.source.confirmation_id == selection.confirmation_id
            assert (row.operation_id, row.roles[0]) in {(item.operation_id, item.role)
                                                        for item in selection.operations}
    if case["id"] == "typed_overrides":
        captures = {row["policy_id"]: (row["variant"], row["mutation_kind"])
                    for row in result.model_dump()["mappings"]}
        for policy_id, variant in case["values"].items():
            assert captures[policy_id][0] == variant
        assert captures["reading_missing"][1] == "required_missing"
        assert captures["reading_null"][1] == "type_mismatch"


@pytest.mark.parametrize("change", HOLDOUT["metamorphic_policy_sets"])
def test_directory_changes_are_reflected_in_semantic_mappings(project, change):
    policies = project.registry.definitions()
    contract = project.contract
    target = project.target
    if change == "reverse":
        policies.reverse()
    elif change == "renamed":
        contract = contract.model_copy(update={"operations": [
            op.model_copy(update={"operation_id": "readGauge"})
            if op.operation_id == "queryReadings" else op for op in contract.operations]})
        target = target.model_copy(update={"allowed_operations": [
            "readGauge" if op == "queryReadings" else op for op in target.allowed_operations]})
        policies = [p.model_copy(update={"operation_id": "readGauge",
                    "id": "revised_limit" if p.id == "reading_limit" else p.id})
                    if p.operation_id == "queryReadings" else p for p in policies]
    elif change == "add":
        source = next(p for p in policies if p.id == "ledger_limit")
        policy = source.model_dump()
        policy.update(id="ledger_missing", negative=dict(policy["negative"],
            kind="required_missing", value=None, violation_codes=["OPENAPI_PARAMETER_REQUIRED"],
            allow_value_variants=False))
        policies.append(RegisteredPolicy.model_validate(policy))
    else:
        policies = [p for p in policies if p.id != "ledger_limit"]
    transformed = replace(project, registry=PolicyRegistry(policies),
                          contract=contract, target=target)
    result = compile_requested_checks(transformed, availability(transformed, {}),
                                      text=HOLDOUT["cases"][0]["text"])
    assert result.status == "COMPILED" and not result.pending
    expected = set(HOLDOUT["metamorphic_policy_sets"][change])
    assert {p.policy_id for p in result.executable} == expected
    refs = {(row.operation_id, row.check_ref) for row in result.mappings}
    assert len(refs) == len(result.mappings)
    if change == "renamed":
        assert any(row.operation_id == "readGauge" and row.policy_id == "revised_limit"
                   for row in result.mappings)
    if change == "reverse":
        original = compile_requested_checks(project, availability(project, {}),
                                            text=HOLDOUT["cases"][0]["text"])
        assert result == original


def test_closed_release_keeps_unavailable_identity_out_of_executable_partition(project):
    missing = availability(project, {"not_ready_roles": ["curator"]})
    result = compile_requested_checks(project, missing, text=HOLDOUT["cases"][0]["text"])
    assert result.status == "NEEDS_REVIEW"
    assert {item.policy_id for item in result.executable} == set(HOLDOUT["cases"][0]["mapped"]) - {
        "curator_limit"}
    assert [(row.policy_id, row.reason) for row in result.pending] == [
        ("curator_limit", "IDENTITY_NOT_READY")]


def test_free_text_history_and_snapshot_drift_never_backfill(project):
    current = availability(project, {})
    text = HOLDOUT["cases"][0]["text"]
    assert compile_requested_checks(project, current, text=text, prior_scope=True).reason == (
        "PRIOR_SCOPE_REQUIRES_RESOLUTION")
    assert compile_requested_checks(project, replace(current, project_identity="older"),
                                    text=text).reason == "INVENTORY_SNAPSHOT_CHANGED"
    assert compile_requested_checks(project, current,
        text=text + " label 明确改成长 10 的值。 ").reason == "SCOPE_NOT_PROVEN"


def test_confirmed_selection_cannot_borrow_other_role_or_variant(project):
    current = availability(project, {})
    wrong_role = selection_for(project, {"selected": [["queryReadings", "auditor"]]})
    assert compile_requested_checks(project, current, selection=wrong_role).reason == (
        "IDENTITY_NOT_ALLOWED")
    wrong_variant = selection_for(project, {"selected": [["queryReadings", "observer"]],
        "values": {"ledger_limit": {"exact_value": {"value": 0}}}})
    result = compile_requested_checks(project, current, selection=wrong_variant)
    assert result.reason == "VARIANT_OUTSIDE_SCOPE" and not result.mappings
