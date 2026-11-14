"""Four separately recorded scopes so a pass rate is never a quietly narrower test.

P3-A.1 published one coverage number. When Focus selected a subset of the approved
operations the denominator moved with the selection, so a round that measured 9 of 9
selected obligations and a round that measured 9 of 31 approved ones were reported the
same way. This module keeps the four scopes apart and publishes their differences:

``requested``  the operator's original goal text (deterministic subject closure);
``approved``   every operator-approved obligation the project makes testable at all;
``selected``   the obligations of the round the Focus step actually chose;
``executed``   the obligations that carry real execution evidence.

Everything here is derived from the registry, the contract, the Focus decision and the
measured evidence. No model output is interpreted, and no scope is widened: the ledger
only *reports* a difference and names the basis for it. A difference the approval does
not justify is published as a gap instead of being hidden by a smaller denominator.
"""

from __future__ import annotations

from agenttest.product_design import design_groups
from agenttest.product_obligations import approved_scope, executed_evidence, login_operations
from agenttest.task_completion import (
    INVESTIGATION,
    PRE_RELEASE,
    REGRESSION,
    classify_task,
    subject_scope,
)

SCHEMA = "agenttest.scope-ledger.v1"

# Why an approved obligation is not part of the round. These are the only reasons the
# ledger accepts; anything else is reported as ``UNEXPLAINED`` and blocks completeness.
NOT_IN_REQUESTED_SCOPE = "NOT_IN_REQUESTED_SCOPE"
FOCUS_BLOCKED = "FOCUS_BLOCKED"
OPERATION_NOT_READY = "OPERATION_NOT_READY"
REQUESTED_BUT_NOT_SELECTED = "REQUESTED_BUT_NOT_SELECTED"
FOCUS_ADDED_PREREQUISITE = "FOCUS_ADDED_PREREQUISITE"
# A regression or investigation goal names a behaviour ("the delete function changed"), and the
# deterministic subject closure still lists every approved policy of the resource it matched. A
# sibling operation the goal did not name is not a silent narrowing, and requiring it would
# reject a legitimate natural-language request. The obligation the goal *did* name is still
# required through ``SUBJECT_OBLIGATIONS_MEASURED`` and the approved-producer prerequisite audit.
SIBLING_OPERATION_NOT_SELECTED = "SIBLING_OPERATION_NOT_SELECTED"
# An approved negative variant (422/400/…) is a separate obligation of the same operation.
# A functional or regression goal does not ask for every invalid-input variant; a pre-release
# goal does. The difference is recorded with its goal-kind basis instead of being unexplained.
NEGATIVE_VARIANT_NOT_SELECTED = "NEGATIVE_VARIANT_NOT_SELECTED"
UNEXPLAINED = "UNEXPLAINED"

NEGATIVE_CUES = ("非法", "边界", "异常值", "负向", "无效", "校验", "invalid", "boundary")


def wants_negative_variants(kind: str, text: str) -> bool:
    """Does this goal ask for the approved invalid-input variants?

    A pre-release round does; a functional or regression goal only when its own text names
    boundary or invalid behaviour. One rule, shared by the scope ledger and the budget
    estimate, so the two never disagree about what this round intends to run.
    """
    lowered = (text or "").lower()
    return kind == PRE_RELEASE or any(cue in lowered for cue in NEGATIVE_CUES)


def _policy_rows(project, scope):
    """The scope rows as comparable obligations (one approved policy each)."""
    return {item["policy_id"]: dict(
        policy_id=item["policy_id"], operation_id=item["operation_id"],
        kind=item["kind"], expected=item["expected"], roles=list(item.get("roles") or ()))
        for item in scope}


def _all_approved(project):
    """Every approved obligation the project can test, independent of the request.

    ``design_groups`` over the whole approved operation set is the same grouping the round
    would use for a "test everything" goal, so the number is comparable across goals.
    """
    return _policy_rows(project, approved_scope(project, dict(groups=design_groups(project))))


def _selected(project, summary):
    """The approved obligations of the narrowed round (the scope Focus actually chose)."""
    if not summary.get("groups"):
        return {}
    return _policy_rows(project, approved_scope(project, summary))


def _measured(evidence):
    return {policy_id for policy_id, entry in evidence.items() if entry is not None}


def _requested_rows(project, rows, subject):
    """The requested subject and its approved closure, expressed in obligation ids."""
    chosen = [rows[policy_id] for policy_id in subject.get("policy_ids") or ()
              if policy_id in rows]
    missing_policies = [policy_id for policy_id in subject.get("policy_ids") or ()
                        if policy_id not in rows]
    return dict(
        kind=subject.get("kind"), operations=list(subject.get("operations") or ()),
        resolved=bool(subject.get("resolved")), basis=subject.get("basis"),
        cues=list(subject.get("cues") or ()), policies=sorted(subject.get("policy_ids") or ()),
        rows=chosen,
        # A part of the deterministic closure that has no approved policy at all: it is a
        # known gap in the approval, not a gap in the execution.
        unapproved=missing_policies)


def _inventory(summary):
    return {item.get("operation_id"): item
            for item in (summary.get("inventory") or {}).get("operations") or ()}


def _exclusion_reason(row, *, summary, focus, requested, kind):
    """A deterministic reason for an approved obligation the round did not select."""
    operation = row["operation_id"]
    blocked = set(focus.get("blocked_operation_ids") or ())
    if operation in blocked:
        return FOCUS_BLOCKED
    state = (_inventory(summary).get(operation) or {}).get("state")
    if state and state != "READY":
        return OPERATION_NOT_READY
    if operation in set(requested.get("operations") or ()):
        if kind in {REGRESSION, INVESTIGATION}:
            # The goal named a behaviour, not this sibling operation.
            return SIBLING_OPERATION_NOT_SELECTED
        # The goal itself names this operation, yet the round dropped it: never silent.
        return REQUESTED_BUT_NOT_SELECTED
    return NOT_IN_REQUESTED_SCOPE


def with_producers(project, policy_ids):
    """Close a selected obligation set over the approved producers its identity needs.

    A delete/absence obligation cannot run without the approved create that produces the
    identifier, and P3-A.1's compiler projects that prerequisite. The ledger must use the same
    closure, otherwise a legitimate prerequisite looks like an unexplained narrowing.
    """
    policies = {policy.id: policy for policy in project.registry.definitions()}
    logins = login_operations(project)
    chosen = {policy_id for policy_id in policy_ids if policy_id in policies}
    frontier = list(chosen)
    while frontier:
        policy = policies.get(frontier.pop())
        for rule in getattr(policy, "binding_rules", ()) or ():
            source = getattr(rule, "source_policy_id", None)
            if (source and source in policies and source not in chosen
                    and policies[source].operation_id not in logins):
                chosen.add(source)
                frontier.append(source)
    return sorted(chosen)


def _prerequisites(project, selected_rows):
    """Selected obligations whose approved producer is not part of the same scope.

    A lifecycle check is only trustworthy when the identity it reads was produced (and, for
    an absence check, deleted) by an approved action in the same round. The IR compiler adds
    those instances; this is the independent audit that it really happened.
    """
    policies = {policy.id: policy for policy in project.registry.definitions()}
    logins = login_operations(project)
    gaps = []
    for policy_id, row in sorted(selected_rows.items()):
        policy = policies.get(policy_id)
        if policy is None:
            continue
        for rule in policy.binding_rules:
            source = rule.source_policy_id
            if not source or source not in policies or policies[source].operation_id in logins:
                continue
            if source in selected_rows:
                continue
            gaps.append(dict(policy_id=policy_id, operation_id=row["operation_id"],
                             producer_policy_id=source,
                             producer_operation_id=policies[source].operation_id,
                             reason="APPROVED_PRODUCER_NOT_SELECTED"))
    return gaps


def _added_prerequisites(project, selected_rows, requested):
    """Selected obligations the goal did not name but an approved lifecycle requires."""
    requested_ids = set(requested.get("policies") or ())
    policies = {policy.id: policy for policy in project.registry.definitions()}
    operations = set(requested.get("operations") or ())
    added = []
    for policy_id, row in sorted(selected_rows.items()):
        if policy_id in requested_ids:
            continue
        policy = policies.get(policy_id)
        if policy is None:
            continue
        status = policy.assertions.status
        absence = bool(status is not None and set(status.codes or ()) <= {404, 410})
        if absence or policy.operation_id not in operations:
            added.append(dict(policy_id=policy_id, operation_id=row["operation_id"],
                              kind=row["kind"],
                              reason=(FOCUS_ADDED_PREREQUISITE if absence
                                      else NOT_IN_REQUESTED_SCOPE)))
    return added


def _ratio(measured, total):
    if not total:
        return dict(measured=0, total=0, ratio=None)
    return dict(measured=len(measured), total=total, ratio=round(len(measured) / total, 4))


def build(project, summary, text: str | None = None, *, selected_policies=None) -> dict:
    """Build the four-scope ledger for one round; safe to call before any request.

    ``selected_policies`` carries the round's own actionable obligation ids (the same rows the
    completion criteria measure). Without it the ledger expands the Focus groups to every
    approved policy of those operations, which is the right fallback for a report built from a
    summary alone but not the same denominator the run used.
    """
    text = text if text is not None else str(summary.get("user_task") or "")
    classification = classify_task(text)
    kind = classification["kind"]
    subject = subject_scope(project, text, kind)
    subject = dict(subject, kind=kind)
    focus = dict(summary.get("focus") or {})
    evidence = executed_evidence(summary)
    measured = _measured(evidence)

    approved_rows = _all_approved(project)
    if selected_policies is not None:
        selected_rows = {policy_id: approved_rows[policy_id]
                         for policy_id in selected_policies if policy_id in approved_rows}
    else:
        selected_rows = _selected(project, summary)
    requested = _requested_rows(project, approved_rows, subject)
    approved_ids, selected_ids = set(approved_rows), set(selected_rows)
    policies = {policy.id: policy for policy in project.registry.definitions()}
    # Does this goal ask for the invalid-input variants at all? A pre-release round does; a
    # functional or regression goal only when its own text names boundary/invalid behaviour.
    wants_negatives = wants_negative_variants(kind, text)

    def negative(policy_id):
        policy = policies.get(policy_id)
        return bool(policy is not None and policy.negative is not None)

    requested_positive = [policy_id for policy_id in requested["policies"]
                          if not negative(policy_id)]
    requested_considered = (requested["policies"] if wants_negatives else requested_positive)

    excluded = []
    for policy_id in sorted(approved_ids - selected_ids):
        row = approved_rows[policy_id]
        reason = (NEGATIVE_VARIANT_NOT_SELECTED if negative(policy_id) and not wants_negatives
                  else _exclusion_reason(row, summary=summary, focus=focus,
                                         requested=requested, kind=kind))
        excluded.append(dict(policy_id=policy_id, operation_id=row["operation_id"],
                             kind=row["kind"], reason=reason))
    # An exclusion the ledger cannot attribute to the request, the goal kind or a blocked
    # operation is the shape of a silent narrowing: publish it and let completeness fail.
    unexplained = [item for item in excluded if item["reason"] == REQUESTED_BUT_NOT_SELECTED]

    executed_operations = set()
    for case in summary.get("cases") or ():
        for step in case.get("evidence") or ():
            if step.get("executed") and step.get("operation_id"):
                executed_operations.add(step["operation_id"])
    requests = sum(int(step.get("requests") or 0)
                   for case in summary.get("cases") or ()
                   for step in case.get("evidence") or ())

    adopted = _added_prerequisites(project, selected_rows, requested)
    prerequisites = _prerequisites(project, selected_rows)
    missing = [dict(policy_id=policy_id, operation_id=row["operation_id"],
                    kind=row["kind"], reason="NO_EXECUTED_EVIDENCE")
               for policy_id, row in sorted(selected_rows.items())
               if policy_id not in measured]
    coverage = dict(
        selected=_ratio(measured & selected_ids, len(selected_ids)),
        approved=_ratio(measured & approved_ids, len(approved_ids)),
        requested=(_ratio(measured & set(requested_considered), len(requested_considered))
                   if requested["resolved"] else
                   dict(measured=0, total=0, ratio=None, note="原文主体未能确定")),
        excluded_by_focus=len(approved_ids - selected_ids),
        negative_variants_deferred=sum(1 for item in excluded
                                       if item["reason"] == NEGATIVE_VARIANT_NOT_SELECTED),
        selected_added=len(adopted),
        unexplained=len(unexplained))
    covered = bool(selected_ids and selected_ids <= measured)
    selected_operations = sorted({operation for group in summary.get("groups") or ()
                                  for operation in group.get("primary") or ()})
    return dict(
        schema=SCHEMA, kind=kind, kind_reason=classification["reason"],
        requested=dict({key: value for key, value in requested.items() if key != "rows"},
                       count=len(requested["rows"]),
                       considered=len(requested_considered),
                       needs_negative_variants=wants_negatives),
        approved=dict(count=len(approved_ids),
                      operations=sorted({row["operation_id"]
                                         for row in approved_rows.values()}),
                      policies=sorted(approved_ids)),
        selected=dict(mode=focus.get("mode"), count=len(selected_ids),
                      operations=selected_operations,
                      policies=sorted(selected_ids),
                      questions=list(focus.get("not_covered") or ()),
                      blocked=sorted(focus.get("blocked_operation_ids") or ())),
        executed=dict(policies=sorted(measured & approved_ids),
                      operations=sorted(executed_operations), requests=requests),
        excluded=excluded[:200], unexplained=unexplained[:50],
        added=adopted[:50], missing=missing[:100], prerequisites=prerequisites[:50],
        coverage=coverage, covered=covered,
        basis=dict(subject_basis=subject.get("basis"),
                   focus_interpretation=str(focus.get("interpretation") or "")[:300] or None,
                   focus_mode=focus.get("mode"),
                   goal_kind_reason=classification["reason"]))


def completion_rows(ledger):
    """Two completion criteria rows: published coverage and a justified narrowing.

    ``APPROVED_SCOPE_COVERAGE`` is informational (``EXEMPT``): a narrowed round is allowed to
    cover only its selection, but its distance from the approved scope must be visible.
    ``REQUESTED_SCOPE_SELECTED`` is required: a goal whose own subject was silently dropped
    is not complete, whatever the smaller denominator says.
    """
    coverage = ledger.get("coverage") or {}
    selected, approved = coverage.get("selected") or {}, coverage.get("approved") or {}
    excluded = int(coverage.get("excluded_by_focus") or 0)
    selected_text = (f"选中范围 {selected.get('measured')}/{selected.get('total')} 项义务有执行证据"
                     if selected.get("total") else "本轮没有选中任何已批准义务")
    approved_text = (f"项目已批准范围 {approved.get('measured')}/{approved.get('total')} 项"
                     if approved.get("total") else "项目没有已批准义务")
    rows = [dict(
        id="APPROVED_SCOPE_COVERAGE", title="选中范围与项目已批准范围的覆盖差异已公开",
        status="EXEMPT",
        detail=(f"{selected_text}；{approved_text}；另有 {excluded} 项已批准义务不在本轮选中范围"
                "（不计入完成判据，但计入覆盖指标）。"),
        evidence=dict(selected=selected, approved=approved, excluded=excluded,
                      excluded_reasons={
                          reason: sum(1 for item in ledger.get("excluded") or ()
                                      if item.get("reason") == reason)
                          for reason in sorted({item.get("reason")
                                                for item in ledger.get("excluded") or ()})},
                      added=[item["policy_id"] for item in ledger.get("added") or ()][:12]))]
    unexplained = ledger.get("unexplained") or []
    requested = ledger.get("requested") or {}
    if not requested.get("resolved"):
        rows.append(dict(
            id="REQUESTED_SCOPE_SELECTED", title="原文点明的接口都进入了本轮选中范围",
            status="EXEMPT",
            detail="原文主体无法从接口或动作确定（" + str(requested.get("basis") or "未知")
                   + "）；以选中范围为准并保留该说明。",
            evidence=dict(basis=requested.get("basis"))))
    else:
        rows.append(dict(
            id="REQUESTED_SCOPE_SELECTED", title="原文点明的接口都进入了本轮选中范围",
            status="MET" if not unexplained else "UNMET",
            detail=("原文主体（" + str(requested.get("basis") or "")
                    + "）的已批准义务都在选中范围内。"
                    if not unexplained else
                    "原文点明但这些义务没有被选中，也没有记录排除原因："
                    + "、".join(item["policy_id"] for item in unexplained[:8])),
            evidence=dict(requested=requested.get("policies"),
                          unexplained=[item["policy_id"] for item in unexplained[:12]],
                          basis=requested.get("basis"))))
    prerequisites = ledger.get("prerequisites") or []
    if prerequisites:
        rows.append(dict(
            id="APPROVED_PREREQUISITES_PRESENT", title="选中义务的已批准产生者都在本轮范围内",
            status="UNMET",
            detail="以下义务依赖未进入本轮范围的已批准产生者："
                   + "、".join(item["policy_id"] for item in prerequisites[:8]),
            evidence=dict(gaps=[dict(policy=item["policy_id"],
                                     producer=item["producer_policy_id"])
                                for item in prerequisites[:12]])))
    return rows


def public_view(ledger) -> dict:
    """A compact ledger view for a task summary or a web response."""
    return dict(schema=ledger.get("schema"), kind=ledger.get("kind"),
                requested={key: ledger.get("requested", {}).get(key)
                           for key in ("kind", "operations", "basis", "resolved", "count",
                                       "unapproved")},
                approved=dict(count=(ledger.get("approved") or {}).get("count")),
                selected={key: (ledger.get("selected") or {}).get(key)
                          for key in ("mode", "count", "operations", "questions", "blocked")},
                executed=dict(count=len((ledger.get("executed") or {}).get("policies") or ()),
                              requests=(ledger.get("executed") or {}).get("requests")),
                coverage=ledger.get("coverage"),
                excluded={reason: sum(1 for item in ledger.get("excluded") or ()
                                      if item.get("reason") == reason)
                          for reason in sorted({item.get("reason")
                                                for item in ledger.get("excluded") or ()})},
                unexplained=[item["policy_id"] for item in ledger.get("unexplained") or ()][:12],
                prerequisites=[item["policy_id"] for item in
                               ledger.get("prerequisites") or ()][:12],
                basis=ledger.get("basis"))


__all__ = ["SCHEMA", "build", "completion_rows", "public_view"]
