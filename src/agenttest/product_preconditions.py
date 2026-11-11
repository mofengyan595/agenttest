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
PRECONDITION_NOT_ESTABLISHED = "PRECONDITION_NOT_ESTABLISHED"
PRECONDITION_UNPROVEN = "PRECONDITION_UNPROVEN"
ESTABLISHED = "ESTABLISHED"
MISSING = "MISSING"
UNPROVEN = "UNPROVEN"


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
