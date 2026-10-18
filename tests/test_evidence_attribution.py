"""Discarded decisions must remain unknown rather than be inferred from an engine fallback."""

import pytest

pytest.importorskip("langgraph")

from agenttest.evidence_attribution import proposal_attribution
from agenttest.report import FeedbackDecisionEvidence, FeedbackResult


def test_unsupported_abstention_with_bad_refs_is_not_an_inappropriate_assertion():
    feedback = FeedbackResult(reason="FEEDBACK_UNSUPPORTED_FINDING", decisions=[
        FeedbackDecisionEvidence(index=0, action="finish", proposal={
            "finding": "EVIDENCE_INSUFFICIENT", "refs_valid": False})])
    result = proposal_attribution(feedback, "EVIDENCE_INSUFFICIENT")
    assert not result["confirmed_inappropriate_assertion"]
    assert not result["active_abstention"]


def test_unrecorded_rejection_stays_unknown():
    feedback = FeedbackResult(reason="FEEDBACK_UNSUPPORTED_FINDING", decisions=[
        FeedbackDecisionEvidence(index=0, action="finish")])
    result = proposal_attribution(feedback, "EVIDENCE_INSUFFICIENT")
    assert result["unknown_rejected_proposal"]
    assert not result["confirmed_inappropriate_assertion"]


def test_retained_enum_claim_can_be_attributed_without_trusting_it():
    feedback = FeedbackResult(reason="FEEDBACK_UNSUPPORTED_FINDING", decisions=[
        FeedbackDecisionEvidence(index=0, action="finish", proposal={
            "finding": "PERSISTENT_READBACK_MISMATCH", "refs_valid": True})])
    result = proposal_attribution(feedback, "EVIDENCE_INSUFFICIENT")
    assert result["confirmed_inappropriate_assertion"]
    assert not result["unknown_rejected_proposal"]
