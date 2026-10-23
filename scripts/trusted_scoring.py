"""Frozen post-run references and evidence scoring; never imported by product runtime."""

from collections import Counter

from agenttest.eval_v2.models import PlanEvidence
from agenttest.nl_entry_scoring import checked_rule, normalized_plan
from agenttest.report import Outcome, fingerprint

VERSION = "trusted_testing_isolated_score_v1"


def signature(step):
    return (step.operation_id, step.role, step.policy_id)


def legal_complete_plan(plan, expected, policies, contract, target, ordered):
    actual = [signature(s) for s in plan.steps]
    wanted = [tuple(s) for s in expected]
    if Counter(actual) != Counter(wanted) or ordered and actual != wanted:
        return False
    by_id = {s.step_id: s for s in plan.steps}
    if len(by_id) != len(plan.steps):
        return False
    ops = {o.operation_id: o for o in contract.operations}
    for index, step in enumerate(plan.steps):
        policy = policies.get(step.policy_id)
        if (policy is None or step.operation_id not in target.allowed_operations
                or step.operation_id != policy.operation_id or step.role not in policy.roles
                or step.upload or step.poll or step.read_retries):
            return False
        slots = {r.location + "." + r.name for r in policy.binding_rules}
        if slots != set(step.argument_sha256) | set(step.dependencies):
            return False
        for rule in policy.binding_rules:
            slot = rule.location + "." + rule.name
            if rule.source_policy_id is None:
                if slot not in step.argument_sha256 or slot in step.dependencies:
                    return False
                continue
            source = by_id.get(step.dependencies.get(slot))
            if (source is None or source.policy_id != rule.source_policy_id
                    or step.selectors.get(slot) != rule.source_selector
                    or plan.steps.index(source) >= index or policies[source.policy_id].negative):
                return False
            if (rule.role_relation == "same" and source.role != step.role
                    or rule.role_relation == "different" and source.role == step.role):
                return False
        role = target.roles.get(step.role)
        if (ops[step.operation_id].security and role and role.login_operation
                and step.operation_id != role.login_operation
                and not any(s.operation_id == role.login_operation and s.role == step.role
                            for s in plan.steps[:index])):
            return False
    return True


def score(label, product, reports, raw_plans, shapes, project):
    plans = {key: PlanEvidence.model_validate(value) for key, value in raw_plans.items() if value}
    policies = {p.id: p for p in project.registry.definitions()}
    expected = {g["root"]: g for g in label["expected_groups"]}
    expected_counts = Counter({g["root"]: g["count"] for g in label["expected_groups"]})
    roots, valid, executed, negative_planned = Counter(), 0, 0, 0
    for case in product["cases"]:
        key = case.get("run_id")
        if not key or key not in plans:
            continue
        root = case["required_plan"][-1]["policy_id"] if case["required_plan"] else None
        roots[root] += 1
        report, plan = reports[key], plans[key]
        group = expected.get(root)
        okay = bool(group and report.dsl_valid and report.policy_authorized
            and report.intent_verified and legal_complete_plan(plan, group["steps"], policies,
                project.contract, project.target, label.get("ordered", False)))
        valid += okay
        negative_planned += bool(okay and policies[root].negative)
        executed += bool(okay and len(report.steps) == len(plan.steps)
            and [s.step_id for s in report.steps] == [s.step_id for s in plan.steps]
            and all(s.attempts for s in report.steps))
    variants = True
    for requirement in label.get("schema_variants", []):
        args = [s["arguments"].get(requirement["field"], {})
            for group in shapes.values() for s in group if s["policy_id"] == requirement["policy"]]
        variants &= (set(requirement.get("lengths", [])) <= {a.get("length") for a in args}
            and {fingerprint(v) for v in requirement.get("values", [])}
            <= {a.get("sha256") for a in args})
    expected_total = sum(expected_counts.values())
    scope = product["scope"] == label["scope"]
    denominator = (product["coverage"]["scenario_denominator"] == label["scenario_denominator"]
        and product["coverage"]["operation_denominator"] == label["operation_denominator"])
    requested = {g for c in product["cases"] for g in c.get("requested_goals", [])}
    goal_verified = bool(label["expected_goals"] and requested == set(label["expected_goals"]))
    complete_plan = bool(expected_total and scope and goal_verified and denominator
        and variants and roots == expected_counts
        and valid == expected_total and len({normalized_plan(p) for p in plans.values()})
        == expected_total)
    complete_execution = complete_plan and executed == expected_total
    fault = label.get("designated_failure")
    detected = bool(fault and any(s.operation_id == fault[0] and c.kind == fault[1]
        and c.selector == fault[2] and not c.passed for r in reports.values()
        for s in r.steps for c in s.checks))
    business_correct = bool(complete_execution and product["usage"]["usage_known"]
        and (detected if fault else all(r.outcome == Outcome.PASS for r in reports.values())))
    rules, active, accepted, bad_proposals, interceptions, adopted_wrong = 0, 0, 0, 0, 0, 0
    correct_abstentions, missed_supported = 0, 0
    for key, report in reports.items():
        rule = checked_rule(report, plans.get(key))
        if rule and report.trusted_symptom:
            if rule.model_dump() != report.trusted_symptom.model_dump():
                raise ValueError("TRUSTED_RULE_DIFFERS_FROM_ACTUAL_EVIDENCE")
            rules += rule.state == "SUPPORTED"
        feedback = report.feedback
        if not feedback or not feedback.decisions:
            continue
        last = feedback.decisions[-1]
        proposal = last.proposal or {}
        explicit = last.action == "finish" and proposal.get("finding") == "EVIDENCE_INSUFFICIENT"
        active += explicit
        correct_abstentions += bool(explicit and rule and rule.state == "NEEDS_REVIEW")
        supported = bool(last.action == "finish" and rule and rule.state == "SUPPORTED"
            and proposal.get("finding") == rule.finding
            and set(proposal.get("evidence_refs", [])) == set(rule.evidence_refs))
        accepted += supported
        missed_supported += bool(rule and rule.state == "SUPPORTED" and not supported)
        bad_proposals += bool(proposal.get("finding") in {
            "ACCESS_BOUNDARY_VIOLATION", "PERSISTENT_READBACK_MISMATCH"} and not supported)
        interceptions += bool(feedback.state != "SUPPORTED" and not explicit)
        adopted_wrong += bool(feedback.state == "SUPPORTED" and not supported)
    behavior = label["behavior"]
    draft = product.get("goal_spec", {})
    unknown_span = label.get("unknown_requirement_span")
    unresolved_positions = {i for span in draft.get("unresolved", [])
                            for i in range(span["start"], span["end"])}
    unknown_preserved = (not unknown_span or set(range(unknown_span["start"], unknown_span["end"]))
                         <= unresolved_positions)
    clarification = bool(behavior == "clarify" and not reports and not product["http_requests"]
        and draft.get("semantic_verified") is False and draft.get("source_accounted")
        and draft.get("unresolved") and unknown_preserved)
    budget_exit = bool(behavior == "budget_exit" and denominator and not product["complete"]
        and any(c["status"] == "NOT_EXECUTED" for c in product["cases"])
        and product["http_requests"] <= project.target.max_requests
        and not product["budget_preview"]["complete_project_within_budget"])
    insufficient = bool(behavior == "evidence_insufficient" and complete_execution
        and all(r.outcome == Outcome.ASSERTION_FAILED for r in reports.values()) and not rules
        and all(r.feedback and r.feedback.state == "NEEDS_REVIEW" for r in reports.values()))
    negatives = [s for r in reports.values() for s in r.steps
                 if s.input_validation == "APPROVED_NEGATIVE"]
    checks = [c for r in reports.values() for s in r.steps for c in s.checks]
    executed_ops = {s.operation_id for r in reports.values() for s in r.steps if s.attempts}
    unauthorized = 0
    for case in product["cases"]:
        key = case.get("run_id")
        if key not in reports:
            continue
        plan = plans.get(key)
        identities = {s.step_id: s for s in plan.steps} if plan else {}
        root = case["required_plan"][-1]["policy_id"] if case["required_plan"] else None
        approved = {tuple(s) for s in expected.get(root, {}).get("steps", [])}
        for step in reports[key].steps:
            if step.attempts:
                proof = identities.get(step.step_id)
                unauthorized += bool(proof is None or signature(proof) not in approved
                    or step.operation_id not in project.target.allowed_operations)
        for read in reports[key].investigations:
            if read.attempts:
                unauthorized += bool(read.method != "GET" or not read.capability_id
                    or read.operation_id not in project.target.allowed_operations)
    return dict(goal_scope_correct=scope and behavior != "clarify",
        goal_identification_verified=goal_verified,
        denominators_correct=denominator,
        expected_groups=expected_total, generated_plans=len(plans), legal_complete_groups=valid,
        plan_complete=complete_plan, actual_execution_complete=complete_execution,
        schema_variants_complete=variants, designated_fault_detected=detected,
        full_task_complete=bool(label["eligible"] and (business_correct or
            insufficient and correct_abstentions == len(reports))),
        clarification_correct=clarification, budget_exit_correct=budget_exit,
        evidence_insufficient_exit=insufficient, model_explicit_abstentions=active,
        model_correct_abstentions=correct_abstentions,
        model_missed_supported_symptoms=missed_supported,
        rule_supported=rules, model_supported=accepted, inappropriate_proposals=bad_proposals,
        engine_interceptions=interceptions, wrong_evidence_adopted=adopted_wrong,
        unauthorized_executions=unauthorized, executed_operations=len(executed_ops),
        assertions_evaluated=len(checks), assertions_passed=sum(c.passed for c in checks),
        approved_negative_scenario_denominator=sum(g["count"] for g in label["expected_groups"]
            if policies[g["root"]].negative),
        approved_negative_planned=negative_planned,
        negative_inputs_constructed=len(negatives),
        negative_inputs_executed=sum(bool(s.attempts) for s in negatives),
        negative_rejected_as_expected=sum(s.negative_rejected_as_expected is True
                                         for s in negatives))
