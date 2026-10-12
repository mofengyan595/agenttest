"""Reuse v1 paired controls and mechanism evidence; apply explicit v2 denominators."""

from collections import Counter

from agenttest.defect_evaluation import FAULT_CHECKS, detected, evidence
from agenttest.report import Outcome


def pair_result(case_id, mechanism, normal, fault, planned):
    operation, _ = FAULT_CHECKS[mechanism]
    executed = any(s.operation_id == operation and s.attempts for s in fault.steps)
    if not executed:
        status = "NOT_EXECUTED"
    elif normal.outcome != Outcome.PASS:
        status = "INCONCLUSIVE_CONTROL"
    elif detected(mechanism, fault):
        status = "DETECTED"
    elif fault.outcome == Outcome.PASS and fault.workflow_completed and fault.task_satisfied:
        status = "FALSE_NEGATIVE"
    elif fault.outcome == Outcome.ASSERTION_FAILED:
        status = "WRONG_MECHANISM"
    else:
        status = "INCONCLUSIVE"
    return {
        "case_id": case_id,
        "mechanism": mechanism,
        "planned": planned,
        "executed": executed,
        "status": status,
        "control_executed": bool(sum(len(s.attempts) for s in normal.steps)),
        "false_positive": normal.outcome == Outcome.ASSERTION_FAILED,
        "normal": evidence(normal),
        "fault": evidence(fault),
    }


def summarize_faults(pairs):
    counts = Counter(p["status"] for p in pairs)
    from agenttest.eval_v2.scoring import ratio

    executed = [p for p in pairs if p["executed"]]
    controls = [p for p in pairs if p["control_executed"]]
    comparable = [
        p
        for p in executed
        if p["normal"]["outcome"] == "PASS" and p["status"] in {"DETECTED", "FALSE_NEGATIVE"}
    ]
    return {
        "scheduled_mechanisms": len(pairs),
        "planned": sum(p["planned"] for p in pairs),
        "executed": len(executed),
        "statuses": dict(counts),
        "coverage": ratio(counts["DETECTED"], len(pairs)),
        "executed_false_negative": ratio(counts["FALSE_NEGATIVE"], len(comparable)),
        "executed_false_positive": ratio(sum(p["false_positive"] for p in controls), len(controls)),
        "not_executed": counts["NOT_EXECUTED"],
        "inconclusive": sum(p["status"].startswith("INCONCLUSIVE") for p in pairs),
        "wrong_mechanism": counts["WRONG_MECHANISM"],
    }
