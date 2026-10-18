"""Candidate relations and full action combinations retain the deterministic safety boundary."""

import json

import pytest

pytest.importorskip("langgraph")

from jsonschema import ValidationError, validate
from test_feedback_runtime import Decider
from test_planner_input import scenario
from test_readonly_runtime import assert_original_preserved, setup

from agenttest.evidence_context import action_schema, proposal_summary
from agenttest.evidence_control import FixedEvidenceControl
from agenttest.investigation import FeedbackBudget, InvestigationConfig


def test_full_choice_combinations_and_action_shapes():
    a = dict(capability_id="read_a", role="alice", resource_ref="a"*32)
    b = dict(capability_id="read_b", role="bob", resource_ref="b"*32)
    ctx = dict(choices=[a, b], evidence=[], finish_required=False)
    schema = action_schema(ctx)
    valid = dict(action="investigate", selection=a, finding=None, evidence_refs=[])
    validate(valid, schema)
    for wrong in [dict(valid, selection=dict(a, role="bob")),
                  dict(valid, finding="EVIDENCE_INSUFFICIENT"),
                  dict(valid, evidence_refs=["original"])]:
        with pytest.raises(ValidationError):
            validate(wrong, schema)
    with pytest.raises(ValidationError):
        validate(valid, action_schema(dict(ctx, finish_required=True)))
    validate(dict(action="finish", selection=None, finding="EVIDENCE_INSUFFICIENT",
                  evidence_refs=[]), schema)


def test_proposal_diagnostics_do_not_copy_unknown_strings():
    ctx = dict(choices=[], evidence=[])
    summary = proposal_summary(dict(action="secret", finding="private cause",
        selection={"url": "private-url"}, evidence_refs=["private-value"]), ctx)
    assert "private" not in json.dumps(summary) and summary["refs_valid"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,finding", [("ownership", "ACCESS_BOUNDARY_VIOLATION"),
                                         ("name", "PERSISTENT_READBACK_MISMATCH")])
async def test_public_relations_and_zero_model_control(tmp_path, fault, finding):
    executor, calls, _ = setup(tmp_path, analyzer=FixedEvidenceControl(), fault=fault)
    executor.investigation = InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=5))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.finding == finding and report.feedback.state == "SUPPORTED"
    assert report.feedback.requests_used == 1
    assert all(d.model_call is None for d in report.feedback.decisions)
    context = report.feedback.decisions[-1].public_context
    relation = context["relations"][0]
    assert relation["same_resource_as_original"] and relation["same_operation_as_original"]
    if fault == "name":
        assert relation["same_role_as_original"] and relation["repeated_failed_checks"]
    serialized = json.dumps(context)
    assert "private-wrong-name" not in serialized and "session-private" not in serialized
    assert "expected_symptom" not in serialized and "fault" not in serialized
    assert report == executor.store.read(report.run_id)


@pytest.mark.asyncio
async def test_unsupported_proposal_is_saved_but_never_upgraded(tmp_path):
    analyzer = Decider("unsupported")
    analyzer.evidence_relations = True
    executor, calls, _ = setup(tmp_path, analyzer=analyzer)
    executor.investigation = InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=5))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.reason == "FEEDBACK_UNSUPPORTED_FINDING"
    assert report.feedback.decisions[-1].proposal["finding"] == "ACCESS_BOUNDARY_VIOLATION"
    assert report.feedback.finding == "EVIDENCE_INSUFFICIENT"
