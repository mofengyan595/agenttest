"""Predeclared product and model metrics; no labels are available to runtime rules."""

from collections import Counter

from agenttest.dsl import Scenario
from agenttest.eval_v2.scoring import score_intent
from agenttest.evidence_evaluation import score_feedback
from agenttest.feedback_check import stats_for
from agenttest.report import fingerprint
from agenttest.trusted_symptoms import derive_symptom

GRADING_VERSION = "nl_product_v1_model_proposals_v1"


def score_nl(case, task, report, scenario: Scenario | None):
    legacy = score_feedback(case, report, scenario)
    expected_signatures = Counter((s.operation_id, s.role, s.policy_id)
                                  for s in task.requirements.steps)
    actual_signatures = Counter((s.operation_id, s.role, s.policy_id)
                                for s in scenario.steps) if scenario else Counter()
    strategy = bool(scenario and actual_signatures == expected_signatures)
    healthy = case["category"] == "healthy"
    failed = report.steps[-1] if report.steps else None
    parent = next((s for s in scenario.steps if failed and s.id == failed.step_id), None) \
        if scenario else None
    rules = derive_symptom(failed, parent, report.investigations,
        {s.id: s.role for s in scenario.steps}) if parent else None
    # Score what each product actually emitted, not a virtual correction of baseline output.
    trusted = report.trusted_symptom
    feedback = report.feedback
    emitted = trusted.finding if trusted else feedback.finding if feedback else None
    state = trusted.state if trusted else feedback.state if feedback else None
    refs = trusted.evidence_refs if trusted else feedback.evidence_refs if feedback else []
    supported = bool(rules and rules.state == "SUPPORTED" and state == "SUPPORTED"
        and emitted == rules.finding == case["finding"] and len(refs) == 2)
    if supported:
        chosen = [r for r in report.investigations if r.step_id in refs]
        supported = ("original" in refs and len(chosen) == 1 and derive_symptom(
            failed, parent, chosen, {s.id: s.role for s in scenario.steps}).finding == emitted)
    model_supported = False
    if feedback and parent and feedback.state == "SUPPORTED":
        model_reads = [r for r in report.investigations if r.step_id in feedback.evidence_refs]
        model_rule = derive_symptom(failed, parent, model_reads,
                                   {s.id: s.role for s in scenario.steps})
        model_supported = bool("original" in feedback.evidence_refs
            and len(feedback.evidence_refs) == 2 and len(model_reads) == 1
            and model_rule.finding == feedback.finding == case["finding"])
    known = all(s.total_tokens is not None for s in stats_for(report) if s.calls)
    terminal = bool(feedback and feedback.state in {"SUPPORTED", "NEEDS_REVIEW"})
    insufficient_exit = bool(case["category"] == "insufficient" and rules
        and rules.state == "NEEDS_REVIEW" and state == "NEEDS_REVIEW" and terminal and known
        and emitted == "EVIDENCE_INSUFFICIENT" and not refs)
    product = bool(legacy["business_correct"] and known and (
        report.task_satisfied if healthy else insufficient_exit if case["category"] ==
        "insufficient" else supported))
    model_conclusion = bool(legacy["business_correct"] and not healthy and (
        legacy["active_abstention"] and rules and rules.state == "NEEDS_REVIEW"
        if case["category"] == "insufficient" else model_supported))
    same_as_rule, comparable = 0, 0
    from agenttest.evidence_control import fixed_decision

    for read in report.investigations:
        for decision in feedback.decisions if feedback else []:
            context = decision.public_context
            if decision.action != "investigate" or not context:
                continue
            selected = next((c for c in context["choices"] if
                c["capability_id"] == read.capability_id and c["role"] == read.role
                and fingerprint(c["resource_ref"]) == read.resource_ref_sha256), None)
            if selected:
                comparable += 1
                same_as_rule += fixed_decision(context).get("selection") == selected
                break
    return dict(dsl_valid=report.dsl_valid, policy_authorized=report.policy_authorized,
        intent_verified=report.intent_verified,
        intent_matches=bool(scenario and score_intent(task.requirements, scenario).passed),
        correct_strategy=strategy,
        normal_complete=bool(healthy and legacy["business_correct"] and report.task_satisfied),
        designated_assertion_detected=bool(not healthy and legacy["business_correct"]),
        rule_supported=supported, rule_evidence_complete=supported,
        insufficient_product_exit=insufficient_exit, product_complete=product,
        model_conclusion_correct=model_conclusion,
        model_supported_proposal=model_supported,
        model_active_abstention=legacy["active_abstention"],
        model_inappropriate_assertion=legacy["inappropriate_assertion"],
        model_read_selections=legacy["selections"],
        model_relevant_selections=legacy["relevant_selections"],
        model_choices_comparable_to_rule=comparable, model_choices_same_as_rule=same_as_rule,
        safety_preserved=legacy["safety_preserved"], usage_known=known)
