"""Score an isolated read-write lifecycle from the target's own audit and state hashes.

Nothing here trusts the caller's story: every verdict is derived from the fixture's audit
rows (method, role, status, path, idempotency replay flag, fault label) and from the state
hashes the target computed before and after each request. A case declares its initial
state, the expected state after each write, the terminal state, and the cleanup proof.
"""

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_ATTENTION = "NEEDS_REVIEW"


def _rows(audit, operation):
    return [row for row in audit if row["operation_id"] == operation]


def _first(rows):
    return rows[0] if rows else None


def _deleted(item_ids, audit):
    return {row["item_id"] for row in _rows(audit, "deleteItem") if row["status"] == 204}


def score(case, audit):
    """Return a deterministic lifecycle verdict for one prepared case.

    ``case`` keys: ``created`` (expected count), ``initial`` ({"state": str}),
    ``after_create``, ``after_update``, ``after_replace``, ``terminal``, ``namespace``,
    ``cleanup`` (True when a delete and a 404 read-back are both required),
    ``identity`` (role allowed to write), ``allowed_operations``, ``compensation`` (True
    when a post-commit fault must be compensated).
    """
    findings, metrics = [], {}

    creates = _rows(audit, "createItem")
    updates = _rows(audit, "updateItem")
    replaces = _rows(audit, "replaceItem")
    deletes = _rows(audit, "deleteItem")
    reads = [row for row in audit if row["operation_id"] in {"getItem", "listItems"}]

    metrics["creates"] = len([row for row in creates if row["status"] == 201])
    metrics["replays"] = len([row for row in creates if row["idempotent_replay"]])
    metrics["updates"] = len([row for row in updates + replaces if row["status"] == 200])
    metrics["deletes"] = len([row for row in deletes if row["status"] == 204])
    metrics["reads"] = len(reads)
    metrics["requests"] = len(audit)

    reasons = {"401": [], "403": [], "404": [], "409": [], "422": [], "500": []}
    for row in audit:
        reasons.setdefault(str(row["status"]), []).append(row)
    identity_violations = [
        row
        for row in audit
        if row["status"] in {401, 403}
        and row["operation_id"] in {"createItem", "updateItem", "replaceItem", "deleteItem"}
    ]
    denials = [row for row in audit if row["status"] in {401, 403}]
    unapproved = [row for row in audit if row["operation_id"] not in case["allowed_operations"]]
    scope = [row for row in audit if row.get("namespace") != case["namespace"]]
    metrics.update(
        identity_violations=len(identity_violations),
        denials=len(denials),
        unapproved_operations=len(unapproved),
        namespace_violations=len(scope),
    )

    if case.get("denied") is not None:
        if len(denials) < case["denied"]:
            findings.append(("missing_expected_denial", len(denials)))
        elif len(denials) > case["denied"]:
            findings.append(("unexpected_denial", len(denials)))

    if scope:
        findings.append(("namespace_violation", len(scope)))
    if unapproved:
        findings.append(("unapproved_operation", len(unapproved)))
    if case.get("identity") and [
        row
        for row in creates + updates + replaces + deletes
        if row["status"] in {200, 201, 204} and row["role"] != case["identity"]
    ]:
        findings.append(
            (
                "wrong_identity_wrote",
                len(
                    [
                        row
                        for row in creates + updates + replaces + deletes
                        if row["status"] in {200, 201, 204} and row["role"] != case["identity"]
                    ]
                ),
            )
        )

    if not creates or creates[0]["status"] != 201:
        findings.append(("create_not_confirmed", metrics["creates"]))
    if case.get("created", 1) != metrics["creates"]:
        findings.append(("create_count_mismatch", metrics["creates"]))
    created_id = next((row["item_id"] for row in creates if row["status"] == 201), None)
    metrics["created_id"] = created_id

    if case.get("idempotent"):
        if not metrics["replays"]:
            findings.append(("idempotency_unverified", 0))
        if case.get("created", 1) != metrics["creates"]:
            findings.append(("idempotency_duplicate_create", metrics["creates"]))
    if not case.get("idempotent") and metrics["replays"]:
        findings.append(("unexpected_replay", metrics["replays"]))

    for stage, rows in (
        ("after_create", creates),
        ("after_update", updates),
        ("after_replace", replaces),
    ):
        expected = case.get(stage)
        if expected and rows:
            last = [row for row in rows if row["status"] in {200, 201}]
            if last and last[-1]["state_after"] != expected:
                findings.append((f"{stage}_state_mismatch", last[-1]["state_after"]))
        elif expected and not rows:
            findings.append((f"{stage}_missing", 0))

    if case.get("terminal") and audit:
        if audit[-1]["state_after"] != case["terminal"]:
            findings.append(("terminal_state_mismatch", audit[-1]["state_after"]))

    if case.get("cleanup"):
        deleted = _deleted({created_id}, audit)
        confirmed = [row for row in reads if row["status"] == 404 and row["item_id"] in deleted]
        metrics["cleanup_verified"] = bool(deleted) and bool(confirmed)
        if not metrics["cleanup_verified"]:
            findings.append(
                ("cleanup_unverified", dict(deleted=bool(deleted), read_back=bool(confirmed)))
            )
        leftovers = len(deleted) - len(confirmed)
        metrics["leftovers"] = max(leftovers, 0)
        if metrics["leftovers"]:
            findings.append(("leftovers", metrics["leftovers"]))
    else:
        metrics["cleanup_verified"] = None

    faults = [row for row in audit if row.get("fault")]
    metrics["faults"] = len(faults)
    if case.get("compensation"):
        compensated = bool(faults) and bool(
            [
                row
                for row in deletes
                if row["status"] == 204 and row["sequence"] > faults[0]["sequence"]
            ]
        )
        metrics["compensation_verified"] = compensated
        if not compensated:
            findings.append(("compensation_missing", metrics["faults"]))
        if not [row for row in audit if row["status"] == 500]:
            findings.append(("fault_not_injected", 0))
    else:
        metrics["compensation_verified"] = None
        if any(row["status"] == 500 for row in audit):
            findings.append(("unexpected_infrastructure_failure", len(faults)))

    if case.get("initial") and audit and audit[0]["state_before"] != case["initial"]:
        findings.append(("initial_state_mismatch", audit[0]["state_before"]))

    blocking = {
        name for name, _ in findings if name not in {"idempotency_unverified", "unexpected_replay"}
    }
    verdict = VERDICT_FAIL if blocking else (VERDICT_ATTENTION if findings else VERDICT_PASS)
    return dict(
        verdict=verdict,
        findings=[dict(reason=name, detail=detail) for name, detail in findings],
        metrics=metrics,
    )
