"""Isolated post-run scoring. Never imported by the product goal runtime."""

from agenttest.dsl import ArgumentBinding, Step
from agenttest.eval_v2.models import PlanEvidence
from agenttest.report import Outcome, fingerprint
from agenttest.trusted_symptoms import derive_symptom

GRADING_VERSION = "nl_entry_isolated_score_v1"
EXPECTED_ROLES = {
    "nl_login_v1": {"alice", "bob"},
    "nl_create_v1": {"alice"},
    "nl_read_v1": {"alice"},
    "nl_forbidden_v1": {"bob"},
    "ticket_open_v1": {"owner"},
    "ticket_read_v1": {"owner"},
    "ticket_closed_read_v1": {"owner"},
    "ticket_close_v1": {"owner"},
    "ticket_denied_v1": {"visitor"},
    "ticket_list_v1": {"owner"},
}


def normalized_plan(plan):
    signatures = {s.step_id: [s.operation_id, s.role, s.policy_id] for s in plan.steps}
    return fingerprint(
        [
            dict(
                signature=signatures[s.step_id],
                literals=s.argument_sha256,
                sources={k: signatures.get(v) for k, v in s.dependencies.items()},
                selectors=s.selectors,
            )
            for s in plan.steps
        ]
    )


def checked_rule(report, plan):
    if not report.steps or not plan or report.steps[-1].outcome != Outcome.ASSERTION_FAILED:
        return None
    failed = report.steps[-1]
    parent = next((s for s in plan.steps if s.step_id == failed.step_id), None)
    if parent is None:
        return None
    parameters = {
        key.split(".", 1)[1]: ArgumentBinding(from_step=value, select=parent.selectors[key])
        for key, value in parent.dependencies.items()
        if key.startswith("path_parameters.")
    }
    step = Step(
        id=parent.step_id,
        operation_id=parent.operation_id,
        role=parent.role,
        policy_id=parent.policy_id,
        path_parameters=parameters,
    )
    return derive_symptom(
        failed, step, report.investigations, {s.step_id: s.role for s in plan.steps}
    )


def score_request(label, result, reports, plans, shapes=None):
    evidence = {
        key: PlanEvidence.model_validate(value) for key, value in plans.items() if value is not None
    }
    decoded = [p for p in evidence.values() if p.steps]
    scope_correct = result["scope"] == label["scope"]
    requested = {g for case in result["cases"] for g in case.get("requested_goals", [])}
    goal_correct = scope_correct and requested == set(label.get("expected_goals", []))
    required = set(label["required_policies"])
    selected = {s.policy_id for p in decoded for s in p.steps}
    order_correct = True
    for plan in decoded:
        positions = {s.policy_id: i for i, s in enumerate(plan.steps)}
        for first, second in label.get("policy_order", []):
            if first in positions and second in positions and positions[first] >= positions[second]:
                order_correct = False
    roles_correct = all(
        s.role in EXPECTED_ROLES.get(s.policy_id, set()) for p in decoded for s in p.steps
    )
    expected = label["expected_normal_plans"]
    unique = len({normalized_plan(p) for p in decoded})
    variants_correct = True
    for variant in label.get("schema_variants", []):
        entries = [
            step
            for group in (shapes or {}).values()
            for step in group
            if step["policy_id"] == variant["policy_id"]
        ]
        observed = [step["arguments"].get(variant["field"], {}) for step in entries]
        if "lengths" in variant:
            variants_correct &= set(variant["lengths"]) <= {v.get("length") for v in observed}
        if "values" in variant:
            variants_correct &= {fingerprint(v) for v in variant["values"]} <= {
                v.get("sha256") for v in observed
            }
    planned = (
        label["behavior"] == "execute"
        and goal_correct
        and variants_correct
        and len(decoded) == expected
        and selected == required
        and roles_correct
        and order_correct
        and all(r.intent_verified and r.dsl_valid and r.policy_authorized for r in reports.values())
    )
    if label["scope"] == "single" and label["behavior"] == "execute":
        planned = planned and unique == expected
    complete_execution = planned and all(
        key in evidence
        and len(report.steps) == len(evidence[key].steps)
        and all(s.attempts for s in report.steps)
        for key, report in reports.items()
    )
    designated = label.get("designated_failure")
    detected = bool(
        designated
        and any(
            report.outcome == Outcome.ASSERTION_FAILED
            and any(
                [step.operation_id, check.kind, check.selector] == designated and not check.passed
                for step in report.steps
                for check in step.checks
            )
            for report in reports.values()
        )
    )
    passing = sum(r.outcome == Outcome.PASS and r.task_satisfied is True for r in reports.values())
    auto_correct = bool(
        label["behavior"] == "execute"
        and scope_correct
        and complete_execution
        and result["usage"]["usage_known"]
        and (detected if designated else passing == expected)
    )
    review = bool(
        label["behavior"] == "review"
        and scope_correct
        and not reports
        and result["model_calls"] == result["http_requests"] == 0
        and any(c.get("reason") == label["review_reason"] for c in result["cases"])
    )
    supported, explicit_abstention, model_supported, model_inappropriate = 0, 0, 0, 0
    for key, report in reports.items():
        rule = checked_rule(report, evidence.get(key))
        if rule is not None and report.trusted_symptom is not None:
            if rule.model_dump() != report.trusted_symptom.model_dump():
                raise ValueError("Rule output does not match original evidence")
            supported += rule.state == "SUPPORTED"
        feedback = report.feedback
        if not feedback or not feedback.decisions:
            continue
        last = feedback.decisions[-1]
        proposal = last.proposal or {}
        finding = proposal.get("finding", feedback.finding)
        finish = last.action == "finish"
        explicit_abstention += bool(finish and finding == "EVIDENCE_INSUFFICIENT")
        model_supported += bool(
            finish
            and rule
            and rule.state == "SUPPORTED"
            and finding == rule.finding
            and set(proposal.get("evidence_refs", feedback.evidence_refs))
            == set(rule.evidence_refs)
        )
        model_inappropriate += bool(
            finding in {"ACCESS_BOUNDARY_VIOLATION", "PERSISTENT_READBACK_MISMATCH"}
            and not (finish and rule and rule.state == "SUPPORTED" and finding == rule.finding)
        )
    actual_ops = {s.operation_id for r in reports.values() for s in r.steps if s.attempts}
    assertions = [c for r in reports.values() for s in r.steps for c in s.checks]
    return dict(
        goal_scope_correct=scope_correct,
        goal_identification_correct=goal_correct,
        schema_variants_complete=variants_correct,
        generated_plans=len(decoded),
        expected_plans=expected,
        unique_plan_inputs=unique,
        plan_complete=planned,
        roles_correct=roles_correct,
        order_correct=order_correct,
        actual_execution_complete=complete_execution,
        asserted_checks=len(assertions),
        passed_checks=sum(c.passed for c in assertions),
        passing_runs=passing,
        designated_fault_detected=detected,
        clarification_correct=review,
        supported_scope_outcome_correct=auto_correct,
        full_task_complete=auto_correct and all(c["status"] == "FINISHED" for c in result["cases"]),
        required_operation_count=len(label["required_operations"]),
        executed_required_operations=len(actual_ops & set(label["required_operations"])),
        rule_supported=supported,
        model_supported=model_supported,
        model_explicit_abstentions=explicit_abstention,
        model_inappropriate_claims=model_inappropriate,
        model_readonly_selections=sum(
            d.action == "investigate"
            for r in reports.values()
            if r.feedback
            for d in r.feedback.decisions
        ),
        investigation_requests=sum(
            len(s.attempts) for r in reports.values() for s in r.investigations
        ),
    )
