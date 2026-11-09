"""Preparation checks: the write fixture plugs into the product's real lifecycle gate."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import authority  # noqa: E402
import lifecycle as scorer  # noqa: E402

from agenttest.contracts import load_openapi  # noqa: E402
from agenttest.dsl import Scenario  # noqa: E402
from agenttest.generic_intake import GenericSetup, build_generic_project  # noqa: E402
from agenttest.lifecycle import lifecycle_plan, validate_lifecycle_authority  # noqa: E402
from agenttest.safety import TargetConfig  # noqa: E402

TOKENS = {f"AGENTTEST_TARGET_RW_{alias.upper()}_TOKEN": f"preparation-{alias}"
          for alias in ("author", "editor", "auditor", "outsider")}


def project():
    document = authority.document()
    return build_generic_project(
        "rw_preparation",
        document,
        load_openapi(document),
        GenericSetup.model_validate(authority.setup()),
        environ=TOKENS,
    )


def policies(item):
    found = {}
    for policy in item.registry.definitions():
        found.setdefault(policy.operation_id, policy.id)
    return found


def steps(item, overrides=None):
    ids = policies(item)
    rows = [
        dict(
            id="create",
            operation_id="createItem",
            role="author",
            policy_id=ids["createItem"],
            body={"name": dict(value="lifecycle-probe")},
        ),
        dict(
            id="read",
            operation_id="getItem",
            role="author",
            policy_id=ids["getItem"],
            path_parameters={"itemId": dict(from_step="create", select="$.id")},
        ),
        dict(
            id="update",
            operation_id="updateItem",
            role="author",
            policy_id=ids["updateItem"],
            path_parameters={"itemId": dict(from_step="create", select="$.id")},
            body={"name": dict(value="renamed")},
        ),
        dict(
            id="replace",
            operation_id="replaceItem",
            role="author",
            policy_id=ids["replaceItem"],
            path_parameters={"itemId": dict(from_step="create", select="$.id")},
            body={"name": dict(value="replaced")},
        ),
        dict(
            id="delete",
            operation_id="deleteItem",
            role="author",
            policy_id=ids["deleteItem"],
            path_parameters={"itemId": dict(from_step="create", select="$.id")},
        ),
    ]
    for identifier, change in (overrides or {}).items():
        row = next(row for row in rows if row["id"] == identifier)
        for key, value in change.items():
            row[key] = value
    return rows


def scenario(item):
    return Scenario.model_validate(dict(name="rw_preparation", steps=steps(item)))


def test_declaration_is_not_startable_before_approval():
    with pytest.raises(PermissionError, match="WRITE_AUTHORITY_NOT_GRANTED"):
        authority.startable()


def test_every_guard_refuses_an_unsafe_declaration():
    approved = dict(authority.WRITE_AUTHORITY, operator_approval="GRANTED")
    assert authority.startable(approved)
    for broken in (
        dict(approved, environment="local"),
        dict(approved, isolated_confirmed=False),
        dict(approved, allow_mutations=False),
        dict(approved, base_url="http://10.0.0.9:80"),
    ):
        with pytest.raises(PermissionError):
            authority.startable(broken)


def test_generic_intake_accepts_the_isolated_write_project():
    item = project()
    target = item.target
    assert target.allow_mutations is True and target.environment == "isolated"
    assert target.base_url.startswith("http://127.0.0.1:")
    assert set(target.resource_creations) == {"createItem"}
    assert [rule.create_operation for rule in target.resource_lifecycles] == ["createItem"]
    validate_lifecycle_authority(item.contract, target)


def test_real_lifecycle_planner_accepts_the_prepared_plan():
    item = project()
    planned = lifecycle_plan(item.contract, item.target, scenario(item), item.registry)
    assert list(planned) == ["create"]
    source, rule, cleanup = planned["create"]
    assert rule.delete_operation == "deleteItem" and cleanup.operation_id == "deleteItem"


@pytest.mark.parametrize(
    "override,code",
    [
        (
            {"read": {"path_parameters": {"itemId": dict(from_step="ghost", select="$.id")}}},
            "LIFECYCLE_READ_PREPARATION_REQUIRED",
        ),
        (
            {
                "update": {
                    "path_parameters": {
                        "itemId": dict(from_step="create", select="$.entries[0].id")
                    }
                }
            },
            "LIFECYCLE_RESOURCE_SCOPE_MISMATCH",
        ),
        ({"update": {"role": "editor"}}, "LIFECYCLE_RESOURCE_SCOPE_MISMATCH"),
        (
            {"delete": {"body": {"reason": dict(value="cleanup")}}},
            "LIFECYCLE_CLEANUP_POLICY_REQUIRED",
        ),
        (
            {"update": {"body": {"name": dict(from_step="read", select="$.name")}}},
            "LIFECYCLE_MUTATION_DATA_SCOPE_REQUIRED",
        ),
        ({"delete": {"operation_id": "getItem", "policy_id": None}}, None),
    ],
)
def test_planner_rejects_unsafe_write_plans(override, code):
    item = project()
    rows = steps(item, override)
    if rows[-1]["policy_id"] is None:
        rows[-1]["policy_id"] = policies(item)["getItem"]
    prepared = Scenario.model_validate(dict(name="rw_bad", steps=rows))
    with pytest.raises(ValueError) as error:
        lifecycle_plan(item.contract, item.target, prepared, item.registry)
    if code:
        assert code in str(error.value)


def test_disabled_mutation_flag_rejects_creation_authority():
    declared = authority.WRITE_AUTHORITY
    operations = [item["operation_id"] for item in declared["operations"]]
    broken = TargetConfig.model_validate(
        dict(
            base_url=declared["base_url"],
            environment="isolated",
            allow_mutations=False,
            allowed_operations=operations,
            roles={},
            resource_lifecycles=declared["resource_lifecycles"],
            resource_creations=declared["resource_creations"],
        )
    )
    with pytest.raises(ValueError, match="AUTHORITY_INVALID"):
        validate_lifecycle_authority(project().contract, broken)


def test_lifecycle_scorer_reports_each_defect_class():
    case = dict(
        initial=None,
        created=1,
        allowed_operations=["createItem", "getItem", "updateItem", "replaceItem", "deleteItem"],
        identity="author",
        cleanup=True,
        denied=0,
        idempotent=False,
        compensation=False,
        namespace="e2e-unit-0001",
    )

    def row(
        sequence,
        operation,
        status,
        *,
        item="itm-0001",
        role="author",
        replay=False,
        fault=None,
        state="s1",
    ):
        return dict(
            sequence=sequence,
            operation_id=operation,
            status=status,
            item_id=item,
            role=role,
            namespace="e2e-unit-0001",
            idempotent_replay=replay,
            fault=fault,
            state_before=state,
            state_after=state,
            method="GET",
            path="/items",
            request_sha256="0" * 64,
        )

    clean = [
        row(1, "createItem", 201),
        row(2, "getItem", 200),
        row(3, "updateItem", 200),
        row(4, "replaceItem", 200),
        row(5, "deleteItem", 204),
        row(6, "getItem", 404),
    ]
    assert scorer.score(case, clean)["verdict"] == "PASS"

    duplicate = [row(1, "createItem", 201), row(2, "createItem", 201)]
    findings = [item["reason"] for item in scorer.score(case, duplicate)["findings"]]
    assert "create_count_mismatch" in findings

    leftover = [row(1, "createItem", 201)]
    findings = [item["reason"] for item in scorer.score(case, leftover)["findings"]]
    assert "cleanup_unverified" in findings

    denial = [
        row(1, "createItem", 201),
        row(2, "updateItem", 403, role="editor"),
        row(3, "deleteItem", 204),
        row(4, "getItem", 404),
    ]
    findings = [item["reason"] for item in scorer.score(case, denial)["findings"]]
    assert "unexpected_denial" in findings

    foreign = [
        row(1, "createItem", 201),
        row(2, "getItem", 200),
        row(3, "deleteItem", 204),
        row(4, "getItem", 404),
    ]
    foreign[1]["namespace"] = "e2e-other-0001"
    findings = [item["reason"] for item in scorer.score(case, foreign)["findings"]]
    assert "namespace_violation" in findings

    uncompensated = [row(1, "createItem", 201), row(2, "updateItem", 500, fault="after_commit")]
    result = scorer.score(dict(case, compensation=True), uncompensated)
    assert "compensation_missing" in [item["reason"] for item in result["findings"]]

    compensated = uncompensated + [row(3, "deleteItem", 204), row(4, "getItem", 404)]
    result = scorer.score(dict(case, compensation=True), compensated)
    assert result["verdict"] == "PASS", result
