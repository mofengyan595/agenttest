"""Offline attribution of historical feedback; never reconstruct discarded model text."""

import json
from collections import Counter
from pathlib import Path

from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.models import PlanEvidence
from agenttest.feedback import FeedbackAction
from agenttest.feedback_runtime import supported_finding
from agenttest.paired_evaluation import grade_agent, verify_paired
from agenttest.report import ReportStore
from agenttest.task_contract import TaskContract


def audit_failures(archive: Path) -> dict:
    verified = verify_paired(archive)
    frozen, result = (read_json(archive / name) for name in ("freeze.json", "results.json"))
    cases = {case["id"]: case for case in frozen["cases"]}
    store, rows = ReportStore(archive / "reports"), []
    for row in result["results"]:
        case = cases[row["case_id"]]
        if not row["run_id"] or case["fault"] == "none":
            continue
        report = store.read(row["run_id"])
        plan = PlanEvidence.model_validate(row["plan"])
        task = TaskContract.model_validate_json(json.dumps(frozen["tasks"][case["id"]]))
        healthy = store.read(row["healthy_run_id"])
        grade = grade_agent(case, task, report, plan, healthy)
        feedback, roles = report.feedback, {step.step_id: step.role for step in plan.steps}
        related, supporting = [], []
        for read in report.investigations:
            operation = "getCourse" if case["project"] == "courses" else "readTicket"
            if read.role == case["owner"] and read.operation_id == operation:
                related.append(read.step_id)
            if case["finding"] != "EVIDENCE_INSUFFICIENT" and supported_finding(
                FeedbackAction(action="finish", selection=None, finding=case["finding"],
                    evidence_refs=["original", read.step_id]), report.steps[-1],
                roles[report.steps[-1].step_id], report.investigations, roles):
                supporting.append(read.step_id)
        explicit = bool(feedback and feedback.reason == "FEEDBACK_EVIDENCE_INSUFFICIENT"
                        and feedback.decisions[-1].action == "finish")
        labels = []
        if case["finding"] == "EVIDENCE_INSUFFICIENT":
            labels.append("CORRECT_ACTIVE_ABSTENTION" if explicit else
                          "INSUFFICIENT_ENGINE_REJECTION_NOT_ACTIVE_ABSTENTION")
        elif not supporting:
            labels.append("NO_SUPPORTING_EVIDENCE_ACQUIRED")
        elif explicit:
            labels.append("WRONG_ABSTENTION_WITH_SUFFICIENT_EVIDENCE")
        elif grade["conclusion_correct"]:
            labels.append("SUPPORTED_CORRECT_CONCLUSION")
        else:
            labels.append("EVIDENCE_PRESENT_CONCLUSION_FAILURE_UNKNOWN_OUTPUT")
        if len(related) != len(report.investigations):
            labels.append("IRRELEVANT_ROLE_OR_OPERATION_SELECTION")
        if feedback and feedback.reason in {"FEEDBACK_MODEL_ERROR", "FEEDBACK_ACTION_REJECTED",
                "FEEDBACK_FINISH_REQUIRED", "FEEDBACK_MODEL_BUDGET_EXHAUSTED"}:
            labels.append("ACTION_STRUCTURE_OR_TERMINATION_FAILURE")
        rows.append({"case_id": case["id"], "round": row["round"], "labels": labels,
            "record": f"reports/{report.run_id}/report.json", "run_id": report.run_id,
            "report_sha256": row["report_sha256"], "expected_symptom": case["finding"],
            "effective_evidence": supporting, "related_reads": related,
            "feedback_reason": feedback.reason if feedback else None,
            "strict_correct": grade["conclusion_correct"],
            "decisions": [d.model_dump(mode="json") for d in feedback.decisions]
                if feedback else [],
            "observations": [{"ref": "original", "operation": report.steps[-1].operation_id,
                "checks": [c.model_dump() for c in report.steps[-1].checks]}, *[
                {"ref": r.step_id, "operation": r.operation_id, "role": r.role,
                 "checks": [c.model_dump() for c in r.checks]} for r in report.investigations]],
            "discarded_output": "UNKNOWN: proposed finding and rejected refs were not saved"})
    return {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "source_archive": str(archive), "verification": verified, "fault_positions": len(rows),
        "strict_correct": sum(r["strict_correct"] for r in rows),
        "labels_overlap": True, "counts": dict(Counter(x for r in rows for x in r["labels"])),
        "correct_choice_bad_citation": "UNKNOWN: rejected proposals were not retained",
        "new_model_calls": 0, "results": rows}
