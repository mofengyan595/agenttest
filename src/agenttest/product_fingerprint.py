"""Deterministic scenario fingerprints: one failed scenario is not worth testing twice.

P0 measured the second real defect the supervisor had: two follow-up rounds produced designs
with different target ids but the **same** semantics, so the agent re-created and re-deleted a
resource and only reproduced the same wrong conclusion. "A new plan exists" is not evidence of
new information, and the product must be able to say so without asking a model.

The fingerprint below is a canonical hash of what the plan actually does:

* the ordered operations and their method,
* the approved policy behind each step and that policy's approved expectation,
* the identity/role each step runs as,
* resource bindings expressed as *producer position + selector* (so renaming a step id, a
  target id or a case title cannot make the same plan look new),
* the lifecycle phase each step needs and whether its precondition is satisfied,
* the ordered read/write shape.

Two plans with the same fingerprint are the same test. A retest is refused only when the
fingerprint matches **and** the earlier attempt did not pass: a passing obligation is already
closed, and a failing one is only worth repeating if the plan actually changed.
"""

import json

from agenttest.product_preconditions import phase_of
from agenttest.report import fingerprint


def policy_expectation(policy) -> dict:
    """Only operator-approved expectations; a plan cannot add one of its own."""
    status = policy.assertions.status
    fields = policy.assertions.fields
    return dict(negative=bool(policy.negative),
                statuses=sorted(status.codes) if status is not None else None,
                fields=sorted((item.select, item.operator, json.dumps(item.expected,
                                                                     sort_keys=True))
                              for item in fields or ()),
                prerequisite=bool(policy.prerequisite_policy_ids),
                bindings=sorted((rule.location, rule.name, rule.source_policy_id,
                                 rule.source_selector, rule.role_relation)
                                for rule in policy.binding_rules))


def _producer_positions(project, scenario) -> dict[str, int]:
    return {step.id: index for index, step in enumerate(scenario.steps)}


def scenario_shape(project, scenario, precondition=None) -> dict:
    """The canonical, replay-stable shape of one scenario."""
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    policies = {policy.id: policy for policy in project.registry.definitions()}
    positions = _producer_positions(project, scenario)
    satisfied = {row["step_id"]: row["satisfied"]
                 for row in (precondition or {}).get("rows", ())}
    steps = []
    for index, step in enumerate(scenario.steps):
        policy = policies.get(step.policy_id)
        operation = operations.get(step.operation_id)
        bindings = []
        for location in ("path_parameters", "query", "body", "headers"):
            for name, bound in sorted(getattr(step, location).items()):
                if bound.from_step is not None:
                    # Producer position, not producer id: ids are free text.
                    bindings.append([location, name, positions.get(bound.from_step), bound.select])
                else:
                    bindings.append([location, name, None, None])
        steps.append(dict(
            position=index, operation_id=step.operation_id,
            method=operation.method if operation is not None else None,
            role=step.role, policy=step.policy_id,
            expectation=policy_expectation(policy) if policy is not None else None,
            negative=step.negative_value is not None,
            bindings=sorted(bindings),
            phase=(phase_of(policy, operation) if policy is not None and operation is not None
                   else None),
            # An unsatisfied deletion precondition changes the plan's meaning, so it must
            # change the fingerprint: refusing it is not the same test as running it.
            precondition_satisfied=(satisfied[step.id] if step.id in satisfied else None)))
    return dict(steps=steps, order=[step.operation_id for step in scenario.steps])


def scenario_fingerprint(project, scenario, precondition=None) -> str:
    """Stable digest of a scenario's semantics (never its ids, titles or timestamps)."""
    return fingerprint(scenario_shape(project, scenario, precondition))


def failure_signature(report) -> list[str]:
    """What actually failed, in a comparable form: operation, kind, selector, expected, actual."""
    signature = []
    for step in report.steps:
        for check in step.checks:
            if check.passed:
                continue
            signature.append("|".join(str(part) for part in (
                step.operation_id, check.kind, check.selector, check.expected, check.actual)))
    return sorted(set(signature))


def duplicate_attempt(cases, digest) -> dict | None:
    """The earlier attempt this plan repeats, or ``None`` when the plan adds information.

    A passing attempt is closed and is not a reason to refuse anything; only a non-passing
    attempt on the *same* fingerprint makes a repeat worthless.
    """
    for case in cases or ():
        if case.get("scenario_fingerprint") != digest:
            continue
        if case.get("outcome") in {"PASS", None} or case.get("status") == "SKIPPED_DUPLICATE":
            continue
        return dict(case_id=case.get("id"), run_id=case.get("run_id"),
                    outcome=case.get("outcome"),
                    scenario_fingerprint=digest,
                    failure_signature=case.get("failure_signature") or [])
    return None


def annotate_case(case_row, project, scenario, precondition=None, report=None) -> dict:
    """Attach the fingerprint (and the measured failure signature) to one executed case."""
    case_row["scenario_fingerprint"] = scenario_fingerprint(project, scenario, precondition)
    if report is not None:
        case_row["failure_signature"] = failure_signature(report)
    return case_row


def duplicate_failure_reason(match) -> str:
    """One sentence the report can show without a model: what was skipped and why."""
    return ("与已执行的补测 " + str(match.get("case_id")) + " 语义等价（同一指纹 "
            + str(match.get("scenario_fingerprint") or "")[:12] + "，上次结论 "
            + str(match.get("outcome")) + "），未修正前置条件也没有新的信息价值，未重复执行。")
