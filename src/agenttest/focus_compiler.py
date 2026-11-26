"""Pure shadow projection of approved negative checks. No task or dispatch integration."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_serializer

from agenttest.contracts import Operation
from agenttest.diagnostics import ValidationFailure
from agenttest.discovery import trusted
from agenttest.model_input import IDENTIFIER, SECRET
from agenttest.negative import validate_approval
from agenttest.policies import RegisteredPolicy
from agenttest.product_checks import (
    MAX_REQUESTED_CHECKS,
    ExactNegativeValue,
    RequestedCheck,
    check_index,
    partition_requested_checks,
    release_check_source,
    requested_check_readiness,
    validate_requested_checks,
    validate_requested_variant,
)
from agenttest.product_design import design_groups
from agenttest.product_focus_wire import operation_checks
from agenttest.project_goals import GoalProject
from agenttest.request_requirements import request_atoms
from agenttest.safety import StrictModel
from agenttest.task_completion import project_variant_scope_sources


@dataclass(frozen=True)
class FocusAvailability:
    """Server-owned inventory facts, without credential values or model-proposed aliases."""

    project_identity: str
    ready_operations: frozenset[str]
    ready_roles: frozenset[str]
    pending_checks: frozenset[tuple[str, str | None]]


class SelectedCheckScope(StrictModel):
    operation_id: str
    role: str | None
    instance_id: str | None = None


class CheckVariant(StrictModel):
    # An outer null is a default marker. A wrapped null is the JSON value null.
    exact_value: ExactNegativeValue | None = None
    exact_length: int | None = Field(default=None, ge=0, le=1000)

    @model_serializer(mode="wrap")
    def preserve_supplied_fields(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class ConfirmedCheckScope(StrictModel):
    """Trusted current selection record, provided by the caller after ownership checks."""

    selection_id: str = Field(min_length=1)
    confirmation_id: str | None = Field(default=None, min_length=1)
    project_identity: str
    operations: tuple[SelectedCheckScope, ...] = Field(min_length=1)
    include_negatives: bool
    revoked: bool = False
    variants: dict[str, CheckVariant] = Field(default_factory=dict)


class CheckSourceWitness(StrictModel):
    kind: Literal["CURRENT_CLAUSE", "CONFIRMED_SELECTION"]
    project_identity: str
    source_ref: str | None = None
    source_quote: str | None = None
    clause_id: str | None = None
    selection_id: str | None = None
    confirmation_id: str | None = None


class ShadowCheck(StrictModel):
    operation_id: str
    policy_id: str
    check_ref: str
    roles: tuple[str | None, ...]
    instance_id: str | None = None
    mutation_kind: str
    source: CheckSourceWitness
    variant: CheckVariant


class PendingShadowCheck(StrictModel):
    policy_id: str
    reason: str


class FocusCompilation(StrictModel):
    status: Literal["COMPILED", "NEEDS_MODEL", "NEEDS_REVIEW", "CONFLICT"]
    reason: str | None = None
    mappings: list[ShadowCheck] = Field(default_factory=list)
    executable: list[RequestedCheck] = Field(default_factory=list)
    pending: list[PendingShadowCheck] = Field(default_factory=list)


def compile_requested_checks(project: GoalProject, availability: FocusAvailability, *,
                             text: str = "", prior_scope: bool = False,
                             selection: ConfirmedCheckScope | None = None) -> FocusCompilation:
    """Compile only a standalone closed release or an authoritative structured selection.

    The executable partition is a shadow of the existing RequestedCheck authorization check.
    It grants no authority to run a case. Structured selections have no legacy text projection.
    """
    identity = project.identity()
    if availability.project_identity != identity:
        return FocusCompilation(status="CONFLICT", reason="INVENTORY_SNAPSHOT_CHANGED")
    if prior_scope:
        return FocusCompilation(status="NEEDS_MODEL", reason="PRIOR_SCOPE_REQUIRES_RESOLUTION")

    operations = {op.operation_id: op for op in project.contract.operations}
    business = {key for group in design_groups(project) for key in group["primary"]}
    policies = {p.id: p for p in project.registry.definitions()}
    if selection is None:
        atoms = request_atoms(text)
        scope_clauses = project_variant_scope_sources(project, text, atoms)
        context = dict(user_task=text, request_atoms=atoms, trusted_scope_clauses=scope_clauses,
                       managed_authentication=[], unavailable_operations=[], pending_checks=[])
        source = release_check_source(context)
        if source is None:
            return FocusCompilation(status="NEEDS_MODEL", reason="SCOPE_NOT_PROVEN")
        witness = CheckSourceWitness(kind="CURRENT_CLAUSE", project_identity=identity,
            source_ref=source["source_id"], source_quote=source["text"], clause_id=scope_clauses[0])
        selected_ops = business
        pairs = {}
    else:
        if text.strip():
            return FocusCompilation(status="CONFLICT", reason="STRUCTURED_SCOPE_WITH_OPEN_TEXT")
        if selection.project_identity != identity:
            return FocusCompilation(status="CONFLICT", reason="CONFIRMATION_SNAPSHOT_CHANGED")
        if selection.revoked or not selection.confirmation_id:
            return FocusCompilation(status="NEEDS_REVIEW", reason="SCOPE_CONFIRMATION_REQUIRED")
        selected_ops = {item.operation_id for item in selection.operations}
        if selected_ops - business:
            return FocusCompilation(status="CONFLICT", reason="OPERATION_NOT_ALLOWED")
        pairs = {(item.operation_id, item.role): item for item in selection.operations}
        if len(pairs) != len(selection.operations):
            return FocusCompilation(status="NEEDS_REVIEW", reason="INSTANCE_NOT_UNIQUE")
        approved_pairs = {(p.operation_id, role) for p in policies.values()
            if trusted(p) and p.business_scenario == project.target.business_scenario
            for role in p.roles if role is None or role in project.target.roles}
        if pairs.keys() - approved_pairs:
            return FocusCompilation(status="CONFLICT", reason="IDENTITY_NOT_ALLOWED")
        witness = CheckSourceWitness(kind="CONFIRMED_SELECTION", project_identity=identity,
            selection_id=selection.selection_id, confirmation_id=selection.confirmation_id)
    try:
        for key in selected_ops:
            project.target.permit(key, operations[key].method)
        index = check_index(project)
    except (KeyError, ValueError):
        return FocusCompilation(status="CONFLICT", reason="APPROVED_CATALOG_INVALID")
    if selection is not None and not selection.include_negatives:
        if selection.variants:
            return FocusCompilation(status="CONFLICT", reason="VARIANT_OUTSIDE_SCOPE")
        return FocusCompilation(status="COMPILED", reason="NORMAL_ONLY")
    inputs = selection.variants if selection else {}
    result = FocusCompilation(status="COMPILED")
    projected = []
    for operation_id, entries in operation_checks(index).items():
        if operation_id not in selected_ops:
            continue
        if not IDENTIFIER.fullmatch(operation_id) or SECRET.search(operation_id):
            return FocusCompilation(status="CONFLICT", reason="APPROVED_OPERATION_ID_INVALID")
        for check_ref, entry in entries.items():
            policy = policies[entry["policy_id"]]
            if selection and not any((operation_id, role) in pairs for role in policy.roles):
                continue
            item = pairs.get((operation_id, policy.roles[0])) if len(policy.roles) == 1 else None
            variant = inputs.get(policy.id, CheckVariant())
            result.mappings.append(ShadowCheck(operation_id=operation_id, policy_id=policy.id,
                check_ref=check_ref, roles=tuple(policy.roles),
                instance_id=item.instance_id if item else None, mutation_kind=policy.negative.kind,
                source=witness, variant=variant))
            reason = _pending_reason(project, availability, policy, operations, variant)
            if reason:
                result.pending.append(PendingShadowCheck(policy_id=policy.id, reason=reason))
            elif witness.source_quote is None:
                result.pending.append(PendingShadowCheck(policy_id=policy.id,
                    reason="STRUCTURED_SOURCE_REQUIRES_PROTOCOL"))
            else:
                projected.append(RequestedCheck(policy_id=policy.id,
                                                source_quote=witness.source_quote))

    if inputs.keys() - {row.policy_id for row in result.mappings}:
        return FocusCompilation(status="CONFLICT", reason="VARIANT_OUTSIDE_SCOPE")
    if len(result.mappings) > MAX_REQUESTED_CHECKS:
        return FocusCompilation(status="NEEDS_REVIEW", reason="FOCUS_CHECK_LIMIT_EXCEEDED")
    if selection is None:
        context.update(approved_ids=list(selected_ops), approved_checks=index,
            focus_ready_operations=list(availability.ready_operations),
            managed_authentication=[dict(identity=role, credential_status="READY")
                                     for role in availability.ready_roles],
            pending_checks=[dict(operation_id=key, role=role)
                            for key, role in availability.pending_checks])
        # Validate the complete source-backed projection. In a partial projection the original
        # gate may also flag a withheld check as missing. Preserve those diagnostics without
        # falsely treating the remaining subset as an accepted full Focus proposal.
        try:
            validate_requested_checks(projected, context, selected_ops,
                                      mode=None if result.pending else "selected")
            executable, pending = partition_requested_checks(project,
                [check.model_dump() for check in projected])
        except ValidationFailure as exc:
            return FocusCompilation(status="CONFLICT", reason=exc.diagnostic.code)
        result.executable = [RequestedCheck.model_validate(check) for check in executable]
        result.pending.extend(PendingShadowCheck(policy_id=row["policy_id"], reason=row["reason"])
                              for row in pending)
    if any(row.reason != "STRUCTURED_SOURCE_REQUIRES_PROTOCOL" for row in result.pending):
        result.status = "NEEDS_REVIEW"
        result.reason = "CHECKS_PENDING"
    elif not result.mappings:
        result.reason = "NO_APPROVED_NEGATIVE_CHECKS"
    return result


def _pending_reason(project: GoalProject, availability: FocusAvailability,
                    policy: RegisteredPolicy, operations: dict[str, Operation],
                    variant: CheckVariant) -> str | None:
    if len(policy.roles) != 1:
        return "POLICY_IDENTITY_NOT_UNIQUE"
    role, = policy.roles
    if role is not None and role not in project.target.roles:
        return "IDENTITY_NOT_ALLOWED"
    if (policy.operation_id, role) in availability.pending_checks:
        return "POLICY_PENDING"
    reason = requested_check_readiness(policy.operation_id, policy.roles,
        availability.ready_operations, availability.ready_roles, availability.pending_checks)
    if reason:
        return reason
    if policy.business_scenario != project.target.business_scenario:
        return "POLICY_SCENARIO_CHANGED"
    try:
        validate_approval(policy.negative, operations[policy.operation_id])
        validate_requested_variant(variant, policy, operations)
    except ValidationFailure as exc:
        return exc.diagnostic.code
    except ValueError:
        return "NEGATIVE_VALUE_NOT_AUTHORIZED"
    return None
