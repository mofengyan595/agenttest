"""Classify what each write actually did, from evidence instead of from the status code.

P3-A.1 counted every executed POST that carried request evidence as a resource, so an
approved *negative* check that the target rejected with 422 was booked as a possibly
created resource without a cleanup receipt (``CLEANUP_UNPROVEN``). The count was wrong in
both directions: a rejected request is not a resource, and a 422 does not prove that the
server ignored the request either.

This module derives one of six states per dispatched write, strictly from evidence:

``CREATED_AND_CLEANED``      a create receipt bound the identity *and* an approved read
                            showed it gone (or the approved cleanup was read back).
``NO_SIDE_EFFECT_PROVEN``    nothing was sent, or the request was rejected and an approved
                            read of the same identity afterwards showed no resource.
``CREATION_UNPROVEN``        a write was sent but no creation receipt exists and no approved
                            observation proves the target stayed unchanged.
``CREATED_CLEANUP_FAILED``   creation is bound, the approved cleanup ran and failed or the
                            read-back still found the resource.
``CLEANUP_UNPROVEN``         creation is bound, but no approved observation proves the
                            resource is gone (a 204 receipt alone is not proof).
``WRITE_OUTCOME_UNKNOWN``    the request was dispatched and its result is unknown.

A bare 422, a bare 204, ``record_count == 0`` from an unapproved endpoint or a model's
opinion are never accepted as proof. Unknown write outcomes are never replayed.
"""

from __future__ import annotations

CREATED_AND_CLEANED = "CREATED_AND_CLEANED"
NO_SIDE_EFFECT_PROVEN = "NO_SIDE_EFFECT_PROVEN"
CREATION_UNPROVEN = "CREATION_UNPROVEN"
CREATED_CLEANUP_FAILED = "CREATED_CLEANUP_FAILED"
CLEANUP_UNPROVEN = "CLEANUP_UNPROVEN"
WRITE_OUTCOME_UNKNOWN = "WRITE_OUTCOME_UNKNOWN"

SCHEMA = "agenttest.write-effects.v1"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
# States that mean "the target may still hold something this round created".
RESOURCE_PENDING = frozenset({CREATED_CLEANUP_FAILED, CLEANUP_UNPROVEN})
# States that mean "a write happened but no resource can be booked as resolved".
WRITE_UNRESOLVED = frozenset({CREATION_UNPROVEN, WRITE_OUTCOME_UNKNOWN})
PROVEN = frozenset({CREATED_AND_CLEANED, NO_SIDE_EFFECT_PROVEN})

LIFECYCLE_OWNED = "OWNED"
LIFECYCLE_CREATED = frozenset({LIFECYCLE_OWNED, "CLEANED", "CLEANUP_PENDING",
                               "CLEANUP_OUTCOME_UNKNOWN", "MUTATION_OUTCOME_UNKNOWN",
                               "RESOURCE_ID_UNVERIFIED"})
LIFECYCLE_UNKNOWN = "CREATE_OUTCOME_UNKNOWN"
LIFECYCLE_REJECTED = "NOT_CONFIRMED_CREATED"


def _attempt_unknown(step) -> bool:
    """A dispatched request with no readable answer is unknown, not a rejection."""
    code = str((step.get("error_code") or "") if isinstance(step, dict)
               else getattr(step, "error_code", "") or "")
    if code in {"ATTEMPT_OUTCOME_UNKNOWN", "REQUEST_TIMEOUT", "MODEL_TIMEOUT",
                "CONNECTION_ERROR", "REQUEST_CANCELLED"}:
        return True
    attempt = step.get("attempt") if isinstance(step, dict) else None
    return isinstance(attempt, dict) and bool(attempt.get("error_code")) and not attempt.get(
        "status_code")


def _absence_verified(case, policy_id, step_id, policies=None):
    """An approved absence read ran after the approved cleanup and found nothing."""
    policies = policies or {}
    # The policies that consume this write (normally the approved delete) are legitimate
    # producers for the absence read, so the read proves *this* resource is gone.
    consumers = {other_id for other_id, other in policies.items()
                 if any(getattr(rule, "source_policy_id", None) == policy_id
                        for rule in (getattr(other, "binding_rules", ()) or ()))}
    for row in case.get("absence_expectations") or ():
        if not row.get("satisfied"):
            continue
        if policy_id and row.get("policy_id") == policy_id:
            return True
        if step_id and row.get("deleted_step") == step_id:
            return True
        # The absence policy reads the identity a producer policy created or deleted.
        absence_policy = policies.get(row.get("policy_id"))
        sources = {getattr(rule, "source_policy_id", None)
                   for rule in (getattr(absence_policy, "binding_rules", ()) or ())}
        if sources & (consumers | {policy_id}):
            return True
    return False


def _row_for_step(lifecycle, step_id):
    for row in lifecycle:
        if row.get("create_step") == step_id or row.get("mutation_step") == step_id:
            return row
    return None


def classify(*, case, step, lifecycle_row, policies) -> dict:
    """Classify one write step of one case; every branch names its evidence basis."""
    policy_id = step.get("policy_id")
    step_id = step.get("step_id")
    policy = policies.get(policy_id) if policies else None
    row = lifecycle_row or {}
    state = row.get("status")
    basis = []
    status_code = step.get("status")
    if status_code is None and isinstance(step.get("attempt"), dict):
        status_code = step["attempt"].get("status_code")
    if policy is not None and policy.negative is not None:
        basis.append("APPROVED_NEGATIVE_POLICY")
    if state:
        basis.append(f"LIFECYCLE_{state}")
    if status_code is not None:
        basis.append(f"HTTP_{status_code}")

    if not step.get("executed") or not step.get("request"):
        return dict(status=NO_SIDE_EFFECT_PROVEN, step_id=step_id, policy_id=policy_id,
                    operation_id=step.get("operation_id"), method=step.get("method"),
                    cleanup_proven=True, replay_allowed=False,
                    basis=[*basis, "NOT_SENT"],
                    detail="该写操作没有发送请求（预检拒绝或未执行），没有副作用。")

    if _attempt_unknown(step) or state == LIFECYCLE_UNKNOWN:
        return dict(status=WRITE_OUTCOME_UNKNOWN, step_id=step_id, policy_id=policy_id,
                    operation_id=step.get("operation_id"), method=step.get("method"),
                    cleanup_proven=False, replay_allowed=False,
                    basis=[*basis, "OUTCOME_UNKNOWN"],
                    detail="请求已发出但没有可用结果；不得重放，需人工核实目标状态。",
                    diagnostic=dict(code=WRITE_OUTCOME_UNKNOWN, replayable=False))

    if state in LIFECYCLE_CREATED:
        cleaned = row.get("cleanup_result") == "PASS" or state in {"CLEANED"}
        if state == "RESOURCE_ID_UNVERIFIED":
            return dict(status=CREATION_UNPROVEN, step_id=step_id, policy_id=policy_id,
                        operation_id=step.get("operation_id"), method=step.get("method"),
                        cleanup_proven=False, replay_allowed=False,
                        basis=[*basis, "NO_BOUND_IDENTITY"],
                        detail="目标返回了创建成功，但没有可绑定的资源标识，无法证明已清理。",
                        diagnostic=dict(code=CREATION_UNPROVEN,
                                        basis="RESOURCE_ID_UNVERIFIED"))
        if cleaned:
            absence = _absence_verified(case, policy_id, step_id, policies)
            if absence:
                return dict(status=CREATED_AND_CLEANED, step_id=step_id,
                            policy_id=policy_id, operation_id=step.get("operation_id"),
                            method=step.get("method"), cleanup_proven=True,
                            replay_allowed=False, basis=[*basis, "ABSENCE_READ_BACK"],
                            detail="创建有绑定标识，已批准的删除后回读证明资源已消失。")
            return dict(status=CLEANUP_UNPROVEN, step_id=step_id, policy_id=policy_id,
                        operation_id=step.get("operation_id"), method=step.get("method"),
                        cleanup_proven=False, replay_allowed=False,
                        basis=[*basis, "DELETE_RECEIPT_WITHOUT_READ_BACK"],
                        detail=("删除返回成功，但没有已批准的回读证明资源确实消失；"
                                "204 本身不是清理证明。"),
                        diagnostic=dict(code=CLEANUP_UNPROVEN,
                                        basis="DELETE_RECEIPT_WITHOUT_READ_BACK"))
        if state in {"CLEANUP_PENDING"} or row.get("cleanup_result") not in {None, "PASS",
                                                                          "NOT_SENT"}:
            return dict(status=CREATED_CLEANUP_FAILED, step_id=step_id,
                        policy_id=policy_id, operation_id=step.get("operation_id"),
                        method=step.get("method"), cleanup_proven=False,
                        replay_allowed=False, basis=[*basis, "CLEANUP_ATTEMPTED"],
                        detail="创建有绑定标识，已批准的清理执行过但没有成功。",
                        diagnostic=dict(code=CREATED_CLEANUP_FAILED,
                                        cleanup_result=row.get("cleanup_result")))
        return dict(status=CLEANUP_UNPROVEN, step_id=step_id, policy_id=policy_id,
                    operation_id=step.get("operation_id"), method=step.get("method"),
                    cleanup_proven=False, replay_allowed=False,
                    basis=[*basis, "NO_APPROVED_CLEANUP_RAN"],
                    detail="创建有绑定标识，但没有执行任何已批准的清理。",
                    diagnostic=dict(code=CLEANUP_UNPROVEN, basis="NO_APPROVED_CLEANUP_RAN"))

    if state == LIFECYCLE_REJECTED or (status_code is not None and 400 <= int(status_code) < 500):
        absence = _absence_verified(case, policy_id, step_id, policies)
        if absence:
            return dict(status=NO_SIDE_EFFECT_PROVEN, step_id=step_id, policy_id=policy_id,
                        operation_id=step.get("operation_id"), method=step.get("method"),
                        cleanup_proven=True, replay_allowed=False,
                        basis=[*basis, "REJECTED_AND_READ_BACK_ABSENT"],
                        detail="请求被拒绝，且随后的已批准回读证明没有产生资源。")
        return dict(status=CREATION_UNPROVEN, step_id=step_id, policy_id=policy_id,
                    operation_id=step.get("operation_id"), method=step.get("method"),
                    cleanup_proven=False, replay_allowed=False,
                    basis=[*basis, "REJECTED_BUT_NO_OBSERVATION"],
                    detail=("请求被目标拒绝，但没有已批准的回读证明目标状态未被改变；"
                            "拒绝状态码本身不足以推断没有副作用。"),
                    diagnostic=dict(
                        code=CREATION_UNPROVEN, basis="REJECTED_BUT_NO_OBSERVATION",
                        note=("若该项目没有针对该资源的已批准缺失读（404/410）或列表读，"
                              "无法在不扩大授权的前提下证明未创建，保留待人工核实。")))

    # A rejected approved-negative check is observable from the policy and the outcome alone:
    # its reviewed expectation *is* the rejection. That is still not proof of "no side effect"
    # (the target may create before answering 422), so it stays a write without a receipt.
    if (policy is not None and policy.negative is not None
            and step.get("outcome") == "PASS" and status_code is None):
        return dict(status=CREATION_UNPROVEN, step_id=step_id, policy_id=policy_id,
                    operation_id=step.get("operation_id"), method=step.get("method"),
                    cleanup_proven=False, replay_allowed=False,
                    basis=[*basis, "APPROVED_REJECTION_OBSERVED"],
                    detail=("已批准的异常取值被目标拒绝（预期结果成立），但没有创建凭据；"
                            "拒绝结果本身不证明没有副作用。"),
                    diagnostic=dict(code=CREATION_UNPROVEN,
                                    basis="APPROVED_REJECTION_OBSERVED",
                                    note=("本项目没有针对该资源的已批准回读，"
                                          "无法在不扩大授权的前提下证明未创建。")))

    # No lifecycle row, but nothing says the write was rejected: the same shape the pre-release
    # round leaked ten parcels with. Book it as a possibly created resource whose cleanup is
    # unproven, never as clean.
    return dict(status=CLEANUP_UNPROVEN, step_id=step_id, policy_id=policy_id,
                operation_id=step.get("operation_id"), method=step.get("method"),
                cleanup_proven=False, replay_allowed=False,
                basis=[*basis, "EXECUTED_WRITE_WITHOUT_RECEIPT"],
                detail="写操作已执行但没有生命周期凭据；可能已创建资源且无法证明已清理。",
                diagnostic=dict(code=CLEANUP_UNPROVEN,
                                basis="EXECUTED_WRITE_WITHOUT_RECEIPT"))


def for_case(case, policies=None) -> list[dict]:
    """The *creating* writes of one case with their effect classification.

    A property mutation (``PUT``/``PATCH``) acts on a resource that already exists, and a
    ``DELETE`` is judged by its own read-back, so neither creates something that would still
    need cleanup. Counting them as resources inflated every functional round; the lifecycle
    journal and a collection ``POST`` are the only evidence that a new resource exists.
    """
    policies = policies or {}
    lifecycle = case.get("resource_lifecycle") or ()
    rows = []
    for step in case.get("evidence") or ():
        method = str(step.get("method") or "").upper()
        if method not in WRITE_METHODS:
            continue
        row = _row_for_step(lifecycle, step.get("step_id"))
        if row is None and method != "POST":
            continue
        rows.append(classify(case=case, step=step, lifecycle_row=row, policies=policies))
    return rows


def analyse(cases, policies=None) -> dict:
    """Aggregate the per-write states for one round (or one case list)."""
    rows = []
    for case in cases or ():
        if case.get("status") != "FINISHED":
            continue
        for row in case.get("write_effects") or for_case(case, policies):
            rows.append(dict(row, case_id=case.get("case_id"), run_id=case.get("run_id")))
    counts = {state: sum(1 for row in rows if row["status"] == state) for state in
              (CREATED_AND_CLEANED, NO_SIDE_EFFECT_PROVEN, CREATION_UNPROVEN,
               CREATED_CLEANUP_FAILED, CLEANUP_UNPROVEN, WRITE_OUTCOME_UNKNOWN)}
    created = sum(counts[state] for state in
                  (CREATED_AND_CLEANED, CREATED_CLEANUP_FAILED, CLEANUP_UNPROVEN))
    pending = [row for row in rows if row["status"] in RESOURCE_PENDING]
    unresolved = [row for row in rows if row["status"] in WRITE_UNRESOLVED]
    return dict(schema=SCHEMA, rows=rows, counts=counts, created=created,
                requests=len(rows), pending=pending, unresolved=unresolved,
                proven=counts[CREATED_AND_CLEANED] + counts[NO_SIDE_EFFECT_PROVEN],
                replayed=[row for row in rows if row.get("replayed")])


def public_view(effects) -> dict:
    """A compact, secret-free view for a task summary or web response."""
    return dict(schema=effects.get("schema"), counts=effects.get("counts"),
                created=effects.get("created"), requests=effects.get("requests"),
                pending=[dict(case_id=row.get("case_id"), operation_id=row.get("operation_id"),
                              policy_id=row.get("policy_id"), status=row["status"],
                              basis=row.get("basis"), detail=row.get("detail"))
                         for row in effects.get("pending") or ()][:20],
                unresolved=[dict(case_id=row.get("case_id"),
                                 operation_id=row.get("operation_id"),
                                 policy_id=row.get("policy_id"), status=row["status"],
                                 basis=row.get("basis"), detail=row.get("detail"))
                            for row in effects.get("unresolved") or ()][:20],
                replayed=len(effects.get("replayed") or ()))


__all__ = [
    "CLEANUP_UNPROVEN",
    "CREATED_AND_CLEANED",
    "CREATED_CLEANUP_FAILED",
    "CREATION_UNPROVEN",
    "NO_SIDE_EFFECT_PROVEN",
    "RESOURCE_PENDING",
    "SCHEMA",
    "WRITE_METHODS",
    "WRITE_OUTCOME_UNKNOWN",
    "WRITE_UNRESOLVED",
    "analyse",
    "classify",
    "for_case",
    "public_view",
]
