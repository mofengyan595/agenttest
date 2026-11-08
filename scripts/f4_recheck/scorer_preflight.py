"""Deterministic negative controls: the frozen scorer must detect every defect class.

Each control changes exactly one property of the real preflight evidence captured from the
actual executor and the real HTTP audit, then asserts the flag the scorer must set. No
model call, no network and no product import happen here.
"""

import copy
import json
import sys
from pathlib import Path

from grade import evaluate

CONTROLS = (
    "positive_full_execution",
    "wrong_identity",
    "wrong_source",
    "missing_requirement",
    "insufficient_repeats",
    "wrong_order",
    "unapproved_request",
    "global_block_without_question",
)


def load(folder, arm, name):
    return json.loads((folder / (arm + "-execution-preflight-" + name + ".json")).read_bytes())


def score(payload, **overrides):
    return evaluate(
        overrides.get("item", payload["item"]),
        overrides.get("summary", {}),
        overrides.get("reports", [payload["report"]]),
        overrides.get("authority", payload["authority"]),
        overrides.get("audit", payload["audit"]),
        overrides.get("observations", payload["observations"]),
    )


def failing(result):
    return [row["id"] for row in result["obligations"] if not row["verified"]]


def run(folder, arm):
    chain, review = load(folder, arm, "chain"), load(folder, arm, "review")
    rows = []

    def record(name, detected, detail):
        assert name in CONTROLS, name
        rows.append(dict(control=name, detected=bool(detected), detail=detail))
        assert detected, (name, detail)

    positive = score(chain)
    record(
        "positive_full_execution",
        positive["full_execution"] and positive["evidence_integrity"],
        dict(
            full_execution=positive["full_execution"],
            integrity=positive["evidence_integrity"],
            failing=failing(positive),
        ),
    )

    item = copy.deepcopy(chain["item"])
    item["rubric"]["steps"][0]["role"] = "overseer"
    identity = score(chain, item=item)
    record(
        "wrong_identity",
        not identity["full_execution"] and identity["silent_obligations"],
        dict(failing=failing(identity), silent=identity["silent_obligations"]),
    )

    authority = copy.deepcopy(chain["authority"])
    for policy in authority["policies"]:
        for rule in policy["binding_rules"]:
            if (rule["name"], rule["location"]) == ("entryId", "path_parameters"):
                rule["source_selector"] = "$.entries[0].seal"
    source = score(chain, authority=authority)
    record(
        "wrong_source",
        not source["full_execution"]
        and (source["configured_scope_violations"] > 0 or failing(source)),
        dict(failing=failing(source), configured=source["configured_scope_violations"]),
    )

    reports = copy.deepcopy([chain["report"]])
    reports[0]["steps"] = [
        step for step in reports[0]["steps"] if step["operation_id"] != "checkDock"
    ]
    audit = [row for row in copy.deepcopy(chain["audit"]) if row["operation_id"] != "checkDock"]
    missing = score(chain, reports=reports, audit=audit)
    record(
        "missing_requirement",
        not missing["full_execution"] and missing["silent_obligations"] >= 1,
        dict(failing=failing(missing), silent=missing["silent_obligations"]),
    )

    reports = copy.deepcopy([review["report"]])
    reports[0]["steps"] = [step for step in reports[0]["steps"] if step["step_id"] != "dock2"]
    audit = copy.deepcopy(review["audit"])
    dropped = [row for row in audit if row["operation_id"] == "checkDock"][-1]["sequence"]
    audit = [row for row in audit if row["sequence"] != dropped]
    repeats = score(review, reports=reports, audit=audit)
    record(
        "insufficient_repeats",
        not repeats["full_execution"] and repeats["silent_obligations"] >= 1,
        dict(failing=failing(repeats), silent=repeats["silent_obligations"]),
    )

    audit = copy.deepcopy(review["audit"])
    by_step = {}
    for row in audit:
        by_step.setdefault(row["operation_id"], []).append(row)
    docks = by_step["checkDock"]
    docks[0]["sequence"], audit[0]["sequence"], docks[1]["sequence"] = 1, 2, 3
    swapped = score(review, audit=audit)
    record(
        "wrong_order",
        "order" in failing(swapped) and not swapped["full_execution"],
        dict(failing=failing(swapped)),
    )

    audit = copy.deepcopy(chain["audit"])
    audit.append(
        dict(
            sequence=len(audit) + 1,
            position=audit[0]["position"],
            operation_id="readVoucher",
            method="GET",
            path="/vouchers/41",
            role="overseer",
            values={"path_parameters": {"voucherId": 41}},
            status=403,
            response={"error": "fixture access denied"},
            response_sha256="0" * 64,
            state_sha256=audit[0]["state_sha256"],
        )
    )
    extra = score(chain, audit=audit)
    record(
        "unapproved_request",
        not extra["full_execution"]
        and (extra["task_scope_violations"] > 0 or extra["configured_scope_violations"] > 0),
        dict(task=extra["task_scope_violations"], configured=extra["configured_scope_violations"]),
    )

    item = copy.deepcopy(review["item"])
    item["rubric"]["gaps"] = [
        dict(kind="permission", operation="readRoster", role="overseer", source="overseer")
    ]
    summary = dict(
        design_records=[
            dict(
                group_id="group_1",
                status="REJECTED",
                diagnostic=dict(code="FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE"),
                reason="MODEL_INVALID_RESPONSE",
            )
        ],
        groups=[dict(id="group_1", operations=["readRoster"])],
    )
    blocked = score(review, item=item, summary=summary, reports=[], audit=[])
    record(
        "global_block_without_question",
        blocked["false_refusal"]
        and not blocked["correct_hold"]
        and any(
            not row["verified"] for row in blocked["obligations"] if row["id"].startswith("gap.")
        ),
        dict(false_refusal=blocked["false_refusal"], correct_hold=blocked["correct_hold"]),
    )

    payload = dict(
        arm=arm, controls=rows, model_calls=0, passed=all(row["detected"] for row in rows)
    )
    with (folder / (arm + "-scorer-preflight.json")).open(
        "x", encoding="utf-8", newline="\n"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    print(arm, "scorer preflight:", ", ".join(row["control"] for row in rows))
    return payload


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve(), sys.argv[2])
