"""Expectation preconditions: an expectation about a resource needs its lifecycle phase.

P0 measured a real false positive. A case read a parcel expecting ``404`` **before** it had
been deleted (or after a deletion that never succeeded), and the run reported
``ASSERTION_FAILED`` -- i.e. it accused the service of a defect that the plan itself had
caused. P1's first real Deep Agents run reproduced the mirror image: a plan deleted a parcel
and then read it expecting ``200``, so the target's correct ``404`` was again reported as a
defect. The P1 brief requires that the three situations stay distinct:

1. the plan is missing a precondition *before* execution  -> refuse or mark it pending;
2. the precondition was not established *during* execution -> never report it as an
   independent business defect;
3. the precondition was established and the response still violated the approved
   expectation -> this, and only this, is valid ``ASSERTION_FAILED`` evidence.

Nothing here calls a model or speaks HTTP. The vocabulary is the one Planning IR already
has (``ResourcePhase``/``ConstraintKind``): a phase requirement on a resource, not merely a
status code, so this module pairs each approved expectation with the step that establishes
that phase.

Both directions are audited, because a plan can contradict itself either way: asserting a
resource is absent after creating it, or asserting it exists after deleting it. The
establishing request must be an approved non-negative deletion bound to the same producer
step through the same selector; both facts already exist in the plan, so this is a check,
not a new capability.
"""

from agenttest.planning_ir import ResourcePhase

ABSENCE_STATUSES = frozenset({404, 410})
ABSENCE_PRECONDITION_MISSING = "DESIGN_ABSENCE_PRECONDITION_MISSING"
CLEANUP_READ_BACK_MISSING = "DESIGN_CLEANUP_READ_BACK_MISSING"
PRECONDITION_NOT_ESTABLISHED = "PRECONDITION_NOT_ESTABLISHED"
PRECONDITION_UNPROVEN = "PRECONDITION_UNPROVEN"
ESTABLISHED = "ESTABLISHED"
MISSING = "MISSING"
UNPROVEN = "UNPROVEN"
# Cleanup read-back states, one per created resource of a plan.
PROVEN = "PROVEN"                      # an approved absence read runs after the deletion
READ_BACK_MISSING = "READ_BACK_MISSING"    # the proof exists but the plan never plans it
NO_APPROVED_READ = "NO_APPROVED_READ"  # no approved proof exists: nothing can be required
NO_DELETE = "NO_DELETE"                # out of scope: cleanup selection is another rule
ABSENCE_READ_BACK = "ABSENCE_READ_BACK"
AFTER_DELETE = "AFTER_DELETE"


def absence_expectation(policy) -> bool:
    """True when the approved expectation is about a resource that must not exist."""
    if policy is None or policy.negative is not None:
        return False
    status = policy.assertions.status
    if status is None:
        return False
    codes = set(status.codes)
    return bool(codes) and codes <= ABSENCE_STATUSES


def existence_expectation(policy) -> bool:
    """True when the approved expectation is a success status on an existing resource."""
    if policy is None or policy.negative is not None:
        return False
    status = policy.assertions.status
    if status is None:
        return False
    codes = set(status.codes)
    return bool(codes) and all(200 <= code < 300 for code in codes)


def phase_of(policy, operation) -> str:
    """The lifecycle phase a step needs, derived from the approval and the contract."""
    if operation is not None and operation.method == "DELETE":
        return ResourcePhase.DELETED.value
    if absence_expectation(policy):
        return ResourcePhase.VERIFY_ABSENT.value
    return ResourcePhase.EXISTS.value


def _consumer_identity(step, policy) -> list[tuple[str, str]]:
    """The concrete (producer step, selector) pairs this read actually consumes.

    The approved binding rule says *which policy* may produce the identity; the instantiated
    step says *which step* did. Only the concrete pair can establish a precondition: two
    creates of the same policy are different resources.
    """
    identities = []
    for rule in policy.binding_rules:
        if rule.location != "path_parameters":
            continue
        bound = step.path_parameters.get(rule.name)
        if bound is not None and bound.from_step:
            identities.append((bound.from_step, bound.select))
    return identities


def add_batch_cleanup(project, scenario):
    """Close approved batch setup with the unique owned deletion and its absence read."""
    from agenttest.dsl import ArgumentBinding, Scenario, Step
    from agenttest.policies import NeedsReview

    operations = {op.operation_id: op for op in project.contract.operations}
    policies = {p.id: p for p in project.registry.definitions()}
    lifecycle = {rule.delete_operation: rule for rule in project.target.resource_lifecycles}
    if not lifecycle:
        return scenario
    steps = list(scenario.steps)
    existing = {step.id: step for step in steps}
    lifecycle_by_create = {rule.create_operation: rule for rule in lifecycle.values()}
    for index, owner in enumerate(scenario.steps):
        rule = lifecycle_by_create.get(owner.operation_id)
        if rule is None or policies[owner.policy_id].negative is not None:
            continue
        identity = ArgumentBinding(from_step=owner.id, select=rule.id_selector)
        dependents = [offset for offset, check in enumerate(scenario.steps)
                      if offset > index and policies[check.policy_id].negative is not None
                      and check.role == owner.role
                      and check.path_parameters.get(rule.path_parameter) == identity]
        if not dependents:
            continue
        deletes = [offset for offset, step in enumerate(steps)
                   if step.operation_id == rule.delete_operation and step.role == owner.role
                   and step.path_parameters.get(rule.path_parameter) == identity
                   and policies[step.policy_id].negative is None]
        if deletes:
            if len(deletes) != 1 or deletes[0] <= dependents[-1]:
                raise NeedsReview("DESIGN_SELECTED_CLEANUP_MISSING")
            continue
        approved = [p for p in policies.values() if p.operation_id == rule.delete_operation
                    and owner.role in p.roles and existence_expectation(p)
                    and len(p.binding_rules) == 1
                    and (rule_binding := p.binding_rules[0]).location == "path_parameters"
                    and rule_binding.name == rule.path_parameter
                    and rule_binding.source_policy_id == owner.policy_id
                    and rule_binding.source_selector == rule.id_selector]
        if len(approved) != 1:
            raise NeedsReview("DESIGN_SELECTED_CLEANUP_MISSING")
        identifier = f"batch_cleanup_delete_{index}"
        if identifier in existing:
            raise ValueError("BATCH_CLEANUP_STEP_ID_COLLISION")
        steps.append(Step(id=identifier, operation_id=rule.delete_operation, role=owner.role,
                          policy_id=approved[0].id,
                          path_parameters={rule.path_parameter: identity}))
    for index, delete in enumerate(tuple(steps)):
        rule = lifecycle.get(delete.operation_id)
        if rule is None or policies[delete.policy_id].negative is not None:
            continue
        bound = delete.path_parameters.get(rule.path_parameter)
        owner = existing.get(bound.from_step) if bound else None
        if (owner is None or owner.operation_id != rule.create_operation
                or owner.role != delete.role or bound.select != rule.id_selector):
            continue
        approved = [p for p in policies.values() if absence_expectation(p)
                    and delete.role in p.roles
                    and operations[p.operation_id].method == "GET"
                    and operations[p.operation_id].path == operations[delete.operation_id].path
                    and len(p.binding_rules) == 1
                    and (binding := p.binding_rules[0]).location == "path_parameters"
                    and binding.name == rule.path_parameter
                    and binding.source_policy_id == owner.policy_id
                    and binding.source_selector == bound.select]
        if len(approved) != 1:
            continue
        policy = approved[0]
        if any(step.policy_id == policy.id and step.path_parameters == delete.path_parameters
               for step in steps[index + 1:]):
            continue
        identifier = f"batch_cleanup_read_{index}"
        if identifier in existing:
            raise ValueError("BATCH_CLEANUP_STEP_ID_COLLISION")
        steps.append(Step(id=identifier, operation_id=policy.operation_id, role=delete.role,
                          policy_id=policy.id, path_parameters={rule.path_parameter: bound}))
    return Scenario(name=scenario.name, steps=steps)


def _identity(step, name):
    """The concrete (producer step, selector) address this step consumes, if any."""
    binding = step.path_parameters.get(name)
    if binding is None or not binding.from_step:
        return None
    return (binding.from_step, binding.select)


def approved_absence_read(project, create_policy_id, role, rule, operations, policies):
    """The one approved absence (404/410) read that can prove this resource is gone.

    The approval must be reachable from the creation: same path as the approved deletion, a
    ``GET``, one path binding fed by *this* create policy and selector. Anything else is not a
    proof for this resource, and more than one candidate is ambiguous rather than a licence.
    """
    deletion = operations.get(rule.delete_operation)
    if deletion is None:
        return None
    candidates = []
    for candidate in policies.values():
        operation = operations.get(candidate.operation_id)
        if (not absence_expectation(candidate) or operation is None or operation.method != "GET"
                or operation.path != deletion.path or role not in candidate.roles
                or len(candidate.binding_rules) != 1):
            continue
        binding = candidate.binding_rules[0]
        if (binding.location == "path_parameters" and binding.name == rule.path_parameter
                and binding.source_policy_id == create_policy_id
                and binding.source_selector == rule.id_selector):
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def cleanup_read_back_report(project, scenario) -> dict:
    """Which created resources the plan deletes with (and without) an approved proof of absence.

    One row per lifecycle-managed creation the plan actually performs. The row answers the only
    question the runtime asks after execution -- "can this resource be shown to be gone?" -- and
    keeps the two failure modes apart:

    * ``READ_BACK_MISSING``: an approved absence read exists for this identity, the plan deletes
      it, and the plan never runs that read after the deletion. This is the model's own gap, so
      it is refused before execution;
    * ``NO_APPROVED_READ``: no approved absence read exists at all. Nothing legal can be
      required here -- asking for a 404 read that was never approved would send an unapproved
      request -- so the row is reported and the runtime keeps ``CLEANUP_UNPROVEN``.

    A read before the deletion, or a read bound to a different resource, is not a proof: the
    first observes the resource still exists, and the second proves another resource is gone.
    """
    operations = {op.operation_id: op for op in project.contract.operations}
    policies = {p.id: p for p in project.registry.definitions()}
    logins = {role.login_operation for role in project.target.roles.values()
              if role.login_operation}
    rules = {rule.create_operation: rule for rule in project.target.resource_lifecycles}
    rows = []
    for index, step in enumerate(scenario.steps):
        rule = rules.get(step.operation_id)
        policy = policies.get(step.policy_id)
        operation = operations.get(step.operation_id)
        if (rule is None or step.operation_id in logins or policy is None
                or operation is None or policy.negative is not None
                or operation.method != "POST"):
            continue
        # The identity a create owns is always "this step, this selector" -- a creation carries no
        # path binding of its own, so its address is the one every consumer must copy verbatim.
        identity = (step.id, rule.id_selector)
        deletions = [offset for offset, later in enumerate(scenario.steps)
                     if later.operation_id == rule.delete_operation
                     and (later_policy := policies.get(later.policy_id)) is not None
                     and later_policy.negative is None
                     and _identity(later, rule.path_parameter) == identity]
        approved = approved_absence_read(project, policy.id, step.role, rule, operations, policies)
        row = dict(create_step=step.id, create_index=index, create_operation=step.operation_id,
                   create_policy=policy.id, delete_operation=rule.delete_operation,
                   path_parameter=rule.path_parameter, proof_kind=ABSENCE_READ_BACK,
                   read_policy=approved.id if approved is not None else None,
                   delete_step=None, delete_index=None, read_step=None)
        if not deletions:
            row["status"] = NO_DELETE
        elif approved is None:
            row.update(status=NO_APPROVED_READ, delete_step=scenario.steps[max(deletions)].id,
                       delete_index=max(deletions))
        else:
            last = max(deletions)
            reads = [offset for offset, later in enumerate(scenario.steps)
                     if offset > last and later.policy_id == approved.id
                     and _identity(later, rule.path_parameter) == identity]
            row.update(status=PROVEN if reads else READ_BACK_MISSING,
                       delete_step=scenario.steps[last].id, delete_index=last,
                       read_step=scenario.steps[reads[-1]].id if reads else None)
        rows.append(row)
    return dict(rows=rows,
                missing=[row for row in rows if row["status"] == READ_BACK_MISSING],
                proven=[row for row in rows if row["status"] == PROVEN],
                unprovable=[row for row in rows if row["status"] == NO_APPROVED_READ],
                undeleted=[row for row in rows if row["status"] == NO_DELETE])


def validate_cleanup_read_back(project, scenario) -> dict:
    """Refuse a cleanup that deletes an owned resource without its approved absence read.

    ``DELETE 204`` is not proof, an empty final inventory is not proof for a single resource,
    and a read that ran before the deletion or read another resource is not proof either. Only
    the approved 404/410 read, after the deletion, bound to the identity this plan created,
    closes the resource. When no such approval exists the plan is *not* refused: the runtime
    keeps the resource ``CLEANUP_UNPROVEN``, which is honest about a missing capability.
    """
    report = cleanup_read_back_report(project, scenario)
    if not report["missing"]:
        return report
    row = report["missing"][0]
    from agenttest.policies import NeedsReview

    failure = NeedsReview(CLEANUP_READ_BACK_MISSING)
    failure.diagnostic.fields = [
        f"plan.steps.{row['delete_index']}.path_parameters.{row['path_parameter']}"]
    failure.diagnostic.detail = dict(
        create_step=row["create_step"], delete_step=row["delete_step"],
        obligation_id=row["read_policy"], read_policy=row["read_policy"],
        lifecycle_create=row["create_operation"], lifecycle_delete=row["delete_operation"],
        lifecycle_parameter=row["path_parameter"], proof_kind=ABSENCE_READ_BACK,
        required_position=AFTER_DELETE, missing=[row["read_policy"]],
        hint=(f"删除步骤 {row['delete_step']} 之后缺少已批准的缺失回读：在同一身份"
              f"（{row['path_parameter']} 绑定 {row['create_step']}）上按已批准策略 "
              f"{row['read_policy']} 补一个位于删除之后的回读，用 404/410 证明该资源已不存在。"
              "删除返回 204 不能单独算清理完成，最终库存为 0 也不能替代逐资源证明。"))
    raise failure


def precondition_report(project, scenario) -> dict:
    """Deterministically audit both directions of the resource-lifecycle contract.

    An expectation about a resource is trustworthy only when the plan has not already done the
    opposite to *that same* resource. Two contradictions exist, and they are mirror images:

    * a step asserts a resource is **absent** (approved ``404``/``410``) while the plan creates
      it and never deletes it -- "the parcel must not exist", but we just made it;
    * a step asserts a resource **exists** (approved ``2xx``) while an approved deletion of that
      same resource precedes it -- "the parcel must be readable", but we just deleted it.

    Each row is one of:

    * ``ESTABLISHED`` -- the plan's own steps establish the phase the expectation needs, so a
      mismatch is real evidence;
    * ``MISSING`` -- the plan contradicts itself, so the run would accuse the target of a defect
      the plan invented;
    * ``UNPROVEN`` -- a literal id: nothing in the plan proves the phase either way. A literal
      ``GET /parcels/never-existed`` is a legitimate probe, but its absence is assumed, not
      demonstrated, so a mismatch cannot be reported as a business defect.

    A ``MISSING`` row is refused before execution; an ``UNPROVEN`` row runs and is judged as
    review evidence. This function raises nothing: the caller decides.
    """
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    policies = {policy.id: policy for policy in project.registry.definitions()}
    rows = []
    for index, step in enumerate(scenario.steps):
        policy = policies.get(step.policy_id)
        if policy is None:
            continue
        operation = operations.get(step.operation_id)
        absent = absence_expectation(policy)
        # A delete's own success code is not a claim that the resource exists, so only an
        # operation that must *find* the resource can carry an existence expectation.
        present = (not absent and existence_expectation(policy)
                   and phase_of(policy, operation) == ResourcePhase.EXISTS.value)
        if not absent and not present:
            continue
        identities = _consumer_identity(step, policy)
        deletions = []
        for prior in scenario.steps[:index]:
            deletion = policies.get(prior.policy_id)
            prior_operation = operations.get(prior.operation_id)
            if (deletion is None or prior_operation is None or deletion.negative is not None
                    or prior_operation.method != "DELETE"):
                continue
            if set(_consumer_identity(prior, deletion)) & set(identities):
                deletions.append(prior.id)
        if absent:
            direction = "absence"
            if deletions:
                status = ESTABLISHED
            elif identities:
                status = MISSING  # the plan creates this resource and then claims it is gone
            else:
                status = UNPROVEN  # a literal id: nothing in the plan proves absence
        else:
            direction = "existence"
            if not identities:
                continue  # a literal id: the plan never owns this resource, so nothing to audit
            status = MISSING if deletions else ESTABLISHED
        rows.append(dict(step_id=step.id, policy_id=policy.id,
                         operation_id=step.operation_id, direction=direction,
                         phase=phase_of(policy, operations.get(step.operation_id)),
                         identities=[dict(producer=producer, select=select)
                                     for producer, select in identities],
                         deletions=deletions,
                         status=status,
                         satisfied=status == ESTABLISHED))
    return dict(expectations=len(rows), rows=rows,
                satisfied=all(row["satisfied"] for row in rows),
                contradictions=[row["step_id"] for row in rows if row["status"] == MISSING])


def validate_expectation_preconditions(project, scenario) -> dict:
    """Preflight (situation 1): refuse a plan that contradicts itself.

    Only a *contradiction* is refused, in either direction: the plan creates the resource and
    then asserts it is absent, or it deletes the resource and then asserts it is readable.
    Executing that would spend writes to produce a failure that accuses the target of a defect
    the plan invented -- exactly P0's false positive. A literal-id absence probe is not
    refused; it is reported as ``UNPROVEN`` and stays review evidence.
    """
    report = precondition_report(project, scenario)
    if report["contradictions"]:
        from agenttest.policies import NeedsReview

        raise NeedsReview(ABSENCE_PRECONDITION_MISSING)
    return report


def _observed_statuses(result) -> set[int]:
    """The status codes the executor actually saw, read from the recorded checks."""
    codes = set()
    for check in getattr(result, "checks", None) or ():
        kind = getattr(check, "kind", None)
        name = getattr(kind, "value", kind)
        if name == "http_status":
            actual = getattr(check, "actual", None)
            if isinstance(actual, int):
                codes.add(actual)
    return codes


def reclassify_unestablished(report, scenario, project) -> list[dict]:
    """Situation 2: a failure whose precondition never held is not a business defect.

    Two mirror cases, and nothing else, are downgraded:

    * an **absence** check failed, but no approved deletion of the same resource is present and
      passing in the executed report -- the "absent" state was never established;
    * an **existence** check failed with an absence status, but the plan itself deleted that
      same resource and the deletion passed -- the plan created the mismatch.

    Situation 3 is left untouched: when the establishing precondition held at runtime, the
    failure stays ``ASSERTION_FAILED``. Nothing is upgraded to ``PASS`` and no assertion is
    rewritten: only the *accusation* is removed.
    """
    from agenttest.report import Outcome

    audit = precondition_report(project, scenario)
    if not audit["rows"]:
        return []
    by_step = {row["step_id"]: row for row in audit["rows"]}
    executed = {item.step_id: item for item in report.steps}
    changes = []
    for result in report.steps:
        row = by_step.get(result.step_id)
        if row is None or result.outcome != Outcome.ASSERTION_FAILED:
            continue
        states = [executed.get(item) for item in row["deletions"]]
        deletion_passed = bool(states) and all(
            item is not None and item.outcome == Outcome.PASS for item in states)
        reason = PRECONDITION_NOT_ESTABLISHED
        if row["direction"] == "absence":
            downgrade = not (row["status"] == ESTABLISHED and deletion_passed)
        else:
            # An "exists" expectation is judgeable only when the plan has not removed that
            # resource. A passing deletion makes the mismatch plan-induced; an unproven one
            # (failed, aborted, or never executed) leaves the phase unknown, which is review
            # material, never an accusation of a business defect.
            downgrade = bool(states)
            if downgrade and not deletion_passed:
                reason = PRECONDITION_UNPROVEN
        if not downgrade:
            continue  # situation 3: the precondition held, so the failure is real evidence
        result.outcome = Outcome.NEEDS_REVIEW
        result.reason = reason
        changes.append(dict(step_id=result.step_id, operation_id=row["operation_id"],
                            direction=row["direction"],
                            expected_precondition=(
                                "approved DELETE of the same resource"
                                if row["direction"] == "absence"
                                else "resource still present (no DELETE of it)"),
                            planned_deletions=list(row["deletions"]),
                            precondition_status=row["status"],
                            established=False,
                            deletion_passed=deletion_passed,
                            observed_statuses=sorted(_observed_statuses(result)),
                            previous_outcome="ASSERTION_FAILED",
                            reason=reason))
    return changes
