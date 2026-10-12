"""Reuse v1 paired controls and mechanism evidence; apply explicit v2 denominators."""

from collections import Counter

from agenttest.defect_evaluation import FAULT_CHECKS, detected, evidence
from agenttest.report import Outcome


def _complete_pass(report):
    return (report.outcome == Outcome.PASS and report.workflow_completed is True
            and report.completed and report.task_satisfied is True
            and bool(report.plan_structure)
            and all(s.outcome == Outcome.PASS for s in report.steps)
            and len(report.steps) == len(report.plan_structure)
            and all((s.step_id, s.operation_id) == (p.step_id, p.operation_id)
                    and s.attempts for s, p in zip(report.steps, report.plan_structure)))


def pair_result(case_id, mechanism, normal, fault, planned):
    operation, _ = FAULT_CHECKS[mechanism]
    policy = {"response_name": "nl_read_v1", "response_id": "nl_read_v1",
              "create_status": "nl_create_v1", "ownership": "nl_forbidden_v1"}[mechanism]
    target_ids = {s.step_id for s in fault.plan_structure
                  if s.policy_id == policy and s.operation_id == operation}
    targets = [s for s in fault.steps if s.step_id in target_ids and s.operation_id == operation]
    executed = len(target_ids) == 1 and any(s.attempts for s in targets)
    paired = (normal.scenario_sha256 == fault.scenario_sha256
              and normal.user_task_sha256 == fault.user_task_sha256
              and normal.plan_structure == fault.plan_structure)
    control_pass = (_complete_pass(normal)
                    and any(s.step_id in target_ids and s.attempts for s in normal.steps))
    _, selector = FAULT_CHECKS[mechanism]
    target_detected = detected(mechanism, fault) and any(
        not c.passed and c.selector == selector
        and c.kind == ("http_status" if selector is None else "equals")
        for s in targets if s.attempts for c in s.checks
    )
    if not executed:
        status = "NOT_EXECUTED"
    elif not paired or not control_pass:
        status = "INCONCLUSIVE_CONTROL"
    elif target_detected:
        status = "DETECTED"
    elif _complete_pass(fault):
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
        "false_positive": bool(normal.outcome == Outcome.ASSERTION_FAILED
                               and any(s.attempts for s in normal.steps)),
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
        "conditional_detection": ratio(counts["DETECTED"], len(comparable)),
        "scheduled_controls": len(pairs),
        "executed_controls": len(controls),
        "executed_false_negative": ratio(counts["FALSE_NEGATIVE"], len(comparable)),
        "executed_false_positive": ratio(sum(p["false_positive"] for p in controls), len(controls)),
        "not_executed": counts["NOT_EXECUTED"],
        "inconclusive": sum(p["status"].startswith("INCONCLUSIVE") for p in pairs),
        "wrong_mechanism": counts["WRONG_MECHANISM"],
    }
