"""Compute bounded symptoms from approved execution evidence, never model proposals."""

from agenttest.dsl import Step
from agenttest.report import InvestigationResult, Outcome, StepResult, TrustedSymptomResult


def derive_symptom(failed: StepResult, parent: Step,
                   investigations: list[InvestigationResult],
                   source_roles: dict[str, str | None]) -> TrustedSymptomResult:
    """The caller supplies only reads already authorized and executed in this session.

    Resource and parent linkage are rechecked here so an unrelated observed read
    cannot corroborate the failure. Rules certify observable symptoms only.
    """
    from agenttest.feedback import FeedbackAction
    from agenttest.feedback_runtime import supported_finding

    if (failed.outcome != Outcome.ASSERTION_FAILED or not failed.attempts
            or failed.step_id != parent.id):
        return TrustedSymptomResult()
    sources = {b.from_step for b in parent.path_parameters.values() if b.from_step}
    eligible = [r for r in investigations if r.parent_step_id == failed.step_id
                and r.source_step_id in sources and r.method == "GET"
                and r.source_step_id in source_roles and r.role is not None
                and r.capability_id and r.policy_sha256 and r.attempts and r.checks]
    for finding in ("ACCESS_BOUNDARY_VIOLATION", "PERSISTENT_READBACK_MISMATCH"):
        for read in eligible:
            if finding == "PERSISTENT_READBACK_MISMATCH" and (
                not failed.policy_template_sha256
                or read.policy_template_sha256 != failed.policy_template_sha256
            ):
                continue
            refs = ["original", read.step_id]
            proposal = FeedbackAction(action="finish", selection=None,
                                      finding=finding, evidence_refs=refs)
            if supported_finding(proposal, failed, parent.role, eligible, source_roles):
                return TrustedSymptomResult(state="SUPPORTED", finding=finding,
                    evidence_refs=refs, reason="RULE_SUPPORTED")
    return TrustedSymptomResult()
