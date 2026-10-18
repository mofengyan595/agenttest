"""Cold proposal attribution: an engine rejection does not reveal a discarded model claim."""

from agenttest.eval_v2.archive import read_json
from agenttest.evidence_evaluation import verify_evidence
from agenttest.report import ReportStore

VERSION = "proposal_observation_v1"


def proposal_attribution(feedback, expected):
    proposals = [d.proposal for d in feedback.decisions if d.proposal] if feedback else []
    known_assertion = any(p.get("finding") in {
        "ACCESS_BOUNDARY_VIOLATION", "PERSISTENT_READBACK_MISMATCH"} for p in proposals)
    accepted_assertion = bool(feedback and feedback.state == "SUPPORTED")
    unresolved = bool(feedback and (
        feedback.reason in {"FEEDBACK_MODEL_ERROR", "FEEDBACK_ACTION_REJECTED"} and
        any(d.action == "rejected" and (not d.proposal or d.proposal.get("finding") is None)
            for d in feedback.decisions) or
        feedback.reason == "FEEDBACK_UNSUPPORTED_FINDING" and not proposals))
    return {"confirmed_inappropriate_assertion": bool(expected == "EVIDENCE_INSUFFICIENT"
                and (known_assertion or accepted_assertion)),
        "unknown_rejected_proposal": unresolved,
        "engine_rejected_conclusion_or_refs": bool(feedback and
            feedback.reason == "FEEDBACK_UNSUPPORTED_FINDING"),
        "active_abstention": bool(feedback and feedback.reason == "FEEDBACK_EVIDENCE_INSUFFICIENT"
            and feedback.decisions and feedback.decisions[-1].action == "finish")}


def audit_attribution(archive):
    verify_evidence(archive)
    frozen, result = (read_json(archive / n) for n in ("freeze.json", "results.json"))
    cases = {c["id"]: c for c in frozen["cases"]}
    store = ReportStore(archive / "reports")
    rows = []
    for row in result["results"]:
        if not row.get("run_id"):
            continue
        report = store.read(row["run_id"])
        rows.append({"case_id": row["case_id"], "arm": row["arm"], "round": row["round"],
            "category": row["category"], "record": f"reports/{row['run_id']}/report.json",
            "feedback_reason": report.feedback.reason if report.feedback else None,
            **proposal_attribution(report.feedback, cases[row["case_id"]]["finding"])})
    groups = {}
    for arm in sorted({r["arm"] for r in result["results"]}):
        for category in ["all", *sorted({r["category"] for r in result["results"]})]:
            selected = [r for r in rows if r["arm"] == arm and
                        (category == "all" or r["category"] == category)]
            scheduled = [r for r in result["results"] if r["arm"] == arm and
                         (category == "all" or r["category"] == category)]
            groups[f"{arm}/{category}"] = {"denominator": len(scheduled), "observed": len(selected),
                **{k: sum(r[k] for r in selected) for k in (
                    "confirmed_inappropriate_assertion", "unknown_rejected_proposal",
                    "engine_rejected_conclusion_or_refs", "active_abstention")}}
    return {"attribution_version": VERSION, "strict_scores_unchanged": True,
        "new_model_calls": 0, "groups": groups, "results": rows}
