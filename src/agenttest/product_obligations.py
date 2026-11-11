"""Deterministic supervision facts: what is approved, what has evidence, what is still open.

Nothing here calls a model, speaks HTTP or decides a verdict. These helpers project the
round's existing state (inventory, approved policies, executed case rows and their
investigations) into the narrow payload the LangGraph supervisor is allowed to see: the
original goal, the approved scope, real execution evidence, open obligations, confirmed
anomalies with unconfirmed causes, resource state and the remaining budget.

An **obligation** is one approved policy that the round was scoped to try. A **candidate**
is an obligation that currently has no passed evidence, so

* three obligations on one interface stay three distinct candidates, and
* an interface whose obligations all passed opens no candidate at all.

"An endpoint nobody has called yet" is never a candidate by itself.
"""

from agenttest.dsl import Scenario, Step

MAX_ITEMS = 12
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def round_operations(project, summary):
    """Operations this round actually scoped, in contract order."""
    allowed = set(project.target.allowed_operations)
    chosen = {operation for group in summary.get("groups") or ()
              for operation in group.get("primary") or ()}
    return [operation.operation_id for operation in project.contract.operations
            if operation.operation_id in chosen and operation.operation_id in allowed]


def login_operations(project):
    """Authentication steps: prerequisites, never test obligations of their own."""
    return {role.login_operation for role in project.target.roles.values()}


def policy_expectation(policy):
    """Only operator-registered expectations; a caller can never invent a business rule."""
    status = policy.assertions.status
    if status is not None:
        return dict(kind="status", status=list(status.codes))
    if policy.assertions.fields:
        return dict(kind="fields", fields=[dict(select=item.select, operator=item.operator,
                                                expected=item.expected)
                                           for item in policy.assertions.fields])
    return dict(kind="none")


def obligation_kind(policy):
    """The obligation class is derived from the approval itself, not from a model."""
    if policy.negative is not None:
        return "APPROVED_NEGATIVE"
    if policy.assertions.status is not None:
        return "STATUS_EXPECTATION"
    if policy.assertions.fields:
        return "FIELD_EXPECTATION"
    return "ASSERTION_GAP"


def approved_scope(project, summary):
    """Approved policies for the round's own operations, with their approved expectations."""
    operations = set(round_operations(project, summary))
    logins = login_operations(project)
    scope = []
    for policy in project.registry.definitions():
        if policy.operation_id not in operations or policy.operation_id in logins:
            continue
        scope.append(dict(policy_id=policy.id, operation_id=policy.operation_id,
                          roles=list(policy.roles), kind=obligation_kind(policy),
                          expected=policy_expectation(policy)))
    return scope


def executed_evidence(summary):
    """policy_id -> checks measured on real attempts; a planned step is not evidence.

    Three real sources count: an executed step, a lifecycle cleanup that the executor ran
    under the same approved policy (the delete of a created resource), and a supervision
    read probe. Without them an obligation would look open after it was truly measured and
    the supervisor would repeat an already executed write.
    """
    evidence = {}
    rows = [(case.get("case_id"), step) for case in summary.get("cases") or ()
            for step in (*(case.get("evidence") or ()), *(case.get("cleanup_evidence") or ()))]
    probes = list((summary.get("supervision") or {}).get("probes") or ())
    for case_id, step in rows:
        policy_id = step.get("policy_id")
        if not policy_id or not step.get("executed"):
            continue
        entry = evidence.setdefault(policy_id, dict(policy_id=policy_id, cases=[],
                                                   checks=[], outcomes=[]))
        if case_id and case_id not in entry["cases"]:
            entry["cases"].append(case_id)
        entry["outcomes"].append(step.get("outcome"))
        entry["checks"].extend(_normalized(step.get("checks") or ()))
    for item in probes:
        policy_id = item.get("policy_id")
        if not policy_id:
            continue
        entry = evidence.setdefault(policy_id, dict(policy_id=policy_id, cases=[],
                                                   checks=[], outcomes=[]))
        if item.get("run_id") and item["run_id"] not in entry["cases"]:
            entry["cases"].append(item["run_id"])
        entry["outcomes"].append(item.get("outcome"))
        entry["checks"].extend(_normalized(item.get("checks") or ()))
    for entry in evidence.values():
        entry["passed"] = (bool(entry["outcomes"]) and set(entry["outcomes"]) == {"PASS"}
                           and all(check["passed"] for check in entry["checks"]))
        entry["observed"] = entry["outcomes"][-1] if entry["outcomes"] else None
    return evidence


def _normalized(checks):
    return [dict(kind=check.get("kind"), selector=check.get("selector"),
                 expected=check.get("expected"), actual=check.get("actual"),
                 passed=check.get("passed")) for check in checks]


def satisfied_obligations(scope, evidence):
    """Obligations already closed by measured, passing evidence."""
    return [dict(policy_id=item["policy_id"], operation_id=item["operation_id"],
                 kind=item["kind"], checks=len(evidence[item["policy_id"]]["checks"]))
            for item in scope
            if (entry := evidence.get(item["policy_id"])) and entry["passed"]]


def open_obligations(scope, evidence):
    """Obligations without passed evidence, including the ones that already ran and failed."""
    items = []
    for item in scope:
        entry = evidence.get(item["policy_id"])
        if entry and entry["passed"]:
            continue
        if entry is None:
            state, detail = "NOT_EXECUTED", "本轮没有取得该策略的执行证据。"
        elif entry["observed"] == "ASSERTION_FAILED":
            state, detail = "FAILED", "已执行，响应不符合已确认的预期。"
        else:
            state, detail = "UNDECIDED", f"已执行，结果为 {entry['observed']}，不能据此判断通过。"
        items.append(dict(policy_id=item["policy_id"], operation_id=item["operation_id"],
                          kind=item["kind"], state=state, detail=detail,
                          expected=item["expected"]))
    return items


def probe_ready(policy, operation):
    """A standalone read probe needs an approved read that depends on nothing else."""
    return bool(not policy.negative and not policy.binding_rules
                and not policy.prerequisite_policy_ids and not policy.poll
                and not policy.csv_fixture and policy.assertions.reviewed
                and operation.method in READ_METHODS)


def _why_read(item, entry):
    if entry is None:
        return ("该只读策略还没有执行证据；重新观测可确认真实状态。"
                if item["kind"] != "APPROVED_NEGATIVE" else "该只读策略还没有执行证据。")
    return f"上一次观测结果为 {entry['observed']}，重新读取可确认是否稳定复现。"


def _why_design(item, entry):
    if entry is None:
        return {"APPROVED_NEGATIVE": "已批准的异常输入检查还没有执行。",
                "STATUS_EXPECTATION": "已批准的状态预期还没有执行证据。",
                "FIELD_EXPECTATION": "已批准的字段预期还没有执行证据。"}[item["kind"]]
    return "该策略已执行但未通过，需要一条新的、仍受批准范围约束的检查。"


def cleanup_operations(project):
    """Approved deletions that clean up created data; any creation test has to plan them.

    A scenario that creates a resource must end with its approved deletion, otherwise the
    round leaves data behind and cannot be reused as a write regression.
    """
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    creations = {policy.id for policy in project.registry.definitions()
                 if not policy.negative and policy.operation_id in operations
                 and operations[policy.operation_id].method == "POST"}
    return [dict(operation_id=deletion.operation_id, policy_id=deletion.id)
            for deletion in project.registry.definitions()
            if not deletion.negative
            and deletion.operation_id in operations
            and operations[deletion.operation_id].method == "DELETE"
            and any(rule.source_policy_id in creations for rule in deletion.binding_rules)]


def candidates(project, summary, scope, evidence, *, allow_negatives, attempted=()):
    """Closed candidate set: obligations still open, limited to ready approved operations."""
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    inventory = {item["operation_id"]: item for item in summary["inventory"]["operations"]}
    policies = {policy.id: policy for policy in project.registry.definitions()}
    done = set(attempted)
    items = []
    for item in scope:
        policy = policies.get(item["policy_id"])
        operation = operations.get(item["operation_id"])
        if policy is None or operation is None:
            continue
        if (inventory.get(policy.operation_id) or {}).get("state") != "READY":
            continue  # needs operator input or authorisation first, not another test
        if item["kind"] == "APPROVED_NEGATIVE" and not allow_negatives:
            continue
        entry = evidence.get(policy.id)
        if entry is not None and entry["passed"]:
            continue
        if probe_ready(policy, operation):
            key = f"probe:{policy.id}"
            if key in done:
                continue
            items.append(dict(id=key, action="READ_PROBE", kind=item["kind"],
                              operations=[policy.operation_id], policies=[policy.id],
                              method=operation.method, why=_why_read(item, entry),
                              observed=entry["observed"] if entry else None))
            continue
        key = f"design:{policy.id}"
        if key in done:
            continue
        cleanup = cleanup_operations(project)
        items.append(dict(id=key, action="DESIGN_MORE", kind=item["kind"],
                          operations=[policy.operation_id,
                                      *(entry["operation_id"] for entry in cleanup)],
                          policies=[policy.id], method=operation.method,
                          why=_why_design(item, entry) + (
                              "同一场景若创建了资源，还需按已批准的删除策略清理。"
                              if cleanup else ""),
                          cleanup_policies=[entry["policy_id"] for entry in cleanup],
                          expected=item["expected"],
                          observed=entry["observed"] if entry else None))
    return items[:MAX_ITEMS]


def executed_cases(summary):
    """One row per case: what ran, how many requests, and which checks held."""
    rows = []
    for case in summary.get("cases") or ():
        checks = [check for step in case.get("evidence") or ()
                  for check in step.get("checks") or () if step.get("executed")]
        rows.append(dict(case_id=case.get("case_id"), status=case.get("status"),
                         outcome=case.get("outcome"),
                         operations=list(case.get("executed_operations") or ()),
                         requests=case.get("http_requests") or 0,
                         checks_passed=sum(check.get("passed") is True for check in checks),
                         checks_failed=sum(check.get("passed") is False for check in checks),
                         reason=(case.get("reason") or "")[:160] or None))
    return rows[:MAX_ITEMS]


def anomalies(summary):
    """Confirmed anomalies carry expected-vs-actual; everything else keeps its cause open."""
    confirmed, unconfirmed = [], []
    for case in summary.get("cases") or ():
        failed = [dict(operation_id=step.get("operation_id"), kind=check.get("kind"),
                       selector=check.get("selector"), expected=check.get("expected"),
                       actual=check.get("actual"))
                  for step in case.get("evidence") or () if step.get("executed")
                  for check in step.get("checks") or () if check.get("passed") is False]
        if case.get("outcome") == "ASSERTION_FAILED" and failed:
            confirmed.append(dict(case_id=case.get("case_id"),
                                  operations=list(case.get("executed_operations") or ()),
                                  observed="ASSERTION_FAILED", failed=failed[:6]))
        elif case.get("outcome") != "PASS" or case.get("status") != "FINISHED":
            unconfirmed.append(dict(case_id=case.get("case_id"),
                                    observed=case.get("outcome") or case.get("status"),
                                    operations=list(case.get("executed_operations") or ()),
                                    reason=(case.get("reason") or "")[:160] or None,
                                    cause_confirmed=False))
    return confirmed[:MAX_ITEMS], unconfirmed[:MAX_ITEMS]


def investigations(summary):
    """Read-only investigations that already ran inside a case, with their measured checks."""
    rows = [dict(case_id=case.get("case_id"), operation_id=item.get("operation_id"),
                 role=item.get("role"), method=item.get("method"), path=item.get("path"),
                 outcome=item.get("outcome"), requests=item.get("requests"),
                 reason=(item.get("reason") or "")[:160] or None,
                 cause_confirmed=bool(item.get("cause_confirmed")))
            for case in summary.get("cases") or ()
            for item in case.get("investigations") or ()]
    return rows[:MAX_ITEMS]


def resources(summary):
    """Created resources and their cleanup state; no raw identifier leaves this function."""
    rows = [dict(create_step=item.get("create_step"), status=item.get("status"),
                 cleanup=item.get("cleanup_result"),
                 resource=((item.get("id_sha256") or "")[:12] or None))
            for case in summary.get("cases") or ()
            for item in case.get("resource_lifecycle") or ()]
    pending = [row for row in rows if row["status"] in {"OWNED", "CLEANUP_OUTCOME_UNKNOWN",
                                                       "CLEANUP_PENDING"}]
    return dict(total=len(rows), pending=pending[:MAX_ITEMS],
                created=[row for row in rows if row["status"] != "NOT_CREATED"][:MAX_ITEMS])


def progress(summary):
    """Measured totals only; the supervisor never sees bodies, headers or identifiers."""
    cases = summary.get("cases") or ()
    return dict(cases=len(cases),
                requests=sum(case.get("http_requests") or 0 for case in cases),
                assertions=sum(len(step.get("checks") or ())
                               for case in cases for step in case.get("evidence") or ()
                               if step.get("executed")),
                passed=sum(check.get("passed") is True
                           for case in cases for step in case.get("evidence") or ()
                           for check in step.get("checks") or ()),
                failed=sum(check.get("passed") is False
                           for case in cases for step in case.get("evidence") or ()
                           for check in step.get("checks") or ()))


def supervision_facts(*, project, summary, text, remaining, allow_negatives,
                      attempts=(), attempted=()):
    """The complete, narrowed decision payload; no OpenAPI, bodies or historic logs."""
    scope = approved_scope(project, summary)
    evidence = executed_evidence(summary)
    confirmed, unconfirmed = anomalies(summary)
    return dict(user_task=text,
                iteration=len(attempts),
                approved_scope=[dict(policy_id=item["policy_id"],
                                     operation_id=item["operation_id"], kind=item["kind"],
                                     expected=item["expected"]) for item in scope[:MAX_ITEMS]],
                executed_cases=executed_cases(summary),
                satisfied_obligations=satisfied_obligations(scope, evidence),
                open_obligations=open_obligations(scope, evidence),
                confirmed_anomalies=confirmed,
                unconfirmed_causes=unconfirmed,
                investigations=investigations(summary),
                attempts=list(attempts)[:MAX_ITEMS],
                resources=resources(summary),
                progress=progress(summary),
                remaining=dict(remaining),
                candidates=candidates(project, summary, scope, evidence,
                                      allow_negatives=allow_negatives, attempted=attempted))


def probe_scenario(project, candidate, round_number):
    """One approved read step; operation, role and policy come from the candidate, not a model."""
    policies = {policy.id: policy for policy in project.registry.definitions()}
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    policy_ids = list(candidate.get("policies") or ())
    if len(policy_ids) != 1:
        raise ValueError("SUPERVISION_PROBE_SCOPE_INVALID")
    policy = policies.get(policy_ids[0])
    operation = operations.get(policy.operation_id) if policy is not None else None
    if (operation is None or operation.operation_id not in project.target.allowed_operations
            or not probe_ready(policy, operation)):
        raise ValueError("SUPERVISION_PROBE_NOT_APPROVED")
    return Scenario(name=f"supervision probe {round_number}",
                    steps=[Step(id=f"probe_{round_number}", operation_id=operation.operation_id,
                                role=policy.roles[0] if policy.roles else None,
                                policy_id=policy.id)])
