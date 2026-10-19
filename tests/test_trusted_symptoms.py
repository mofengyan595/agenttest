"""Rules own trusted findings; model proposals and business outcomes remain separate."""

import copy
import json

import pytest
from test_feedback_runtime import Decider
from test_planner_input import scenario
from test_readonly_runtime import assert_original_preserved, setup

pytest.importorskip("langgraph")

from agenttest.investigation import FeedbackBudget, InvestigationConfig
from agenttest.trusted_symptoms import derive_symptom


class WrongFinish(Decider):
    async def generate(self, context):
        result = await super().generate(context)
        if self.calls > 1:
            return json.dumps(dict(action="finish", selection=None,
                finding="EVIDENCE_INSUFFICIENT", evidence_refs=[]))
        return result


@pytest.mark.asyncio
async def test_rule_uses_actual_evidence_even_when_model_abstains(tmp_path):
    model = WrongFinish()
    executor, calls, _ = setup(tmp_path, analyzer=model)
    executor.investigation = InvestigationConfig(enabled=True, trusted_symptoms=True,
        feedback=FeedbackBudget(timeout=10))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.finding == "EVIDENCE_INSUFFICIENT"
    assert report.feedback.state == "NEEDS_REVIEW"
    assert report.feedback.decisions[-1].action == "finish"
    assert report.trusted_symptom.state == "SUPPORTED"
    assert report.trusted_symptom.finding == "ACCESS_BOUNDARY_VIOLATION"
    assert report.trusted_symptom.evidence_refs == ["original", report.investigations[0].step_id]
    assert executor.store.read(report.run_id) == report
    plan = scenario(True)
    parent = next(s for s in plan.steps if s.id == report.steps[-1].step_id)
    roles = {s.id: s.role for s in plan.steps}
    assert derive_symptom(report.steps[-1], parent, report.investigations, roles) == \
        report.trusted_symptom
    for field, value in (("source_step_id", "another_resource"),
                         ("parent_step_id", "another_failure"), ("role", "bob"),
                         ("method", "POST"), ("attempts", [])):
        unrelated = copy.deepcopy(report.investigations)
        setattr(unrelated[0], field, value)
        assert derive_symptom(report.steps[-1], parent, unrelated, roles).state == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_rejected_model_assertion_cannot_manufacture_rule_evidence(tmp_path):
    model = Decider("unsupported")
    executor, calls, _ = setup(tmp_path, analyzer=model)
    executor.investigation = InvestigationConfig(enabled=True, trusted_symptoms=True,
        feedback=FeedbackBudget(timeout=10))
    report = await executor.run(scenario(True))
    assert_original_preserved(executor, report, calls)
    assert report.feedback.reason == "FEEDBACK_UNSUPPORTED_FINDING"
    assert report.trusted_symptom.state == "NEEDS_REVIEW"
    assert not report.trusted_symptom.evidence_refs
    assert not report.investigations


def test_rule_config_is_explicit_and_legacy_serialization_is_preserved():
    assert "trusted_symptoms" not in InvestigationConfig().model_dump()
    assert InvestigationConfig(trusted_symptoms=True).model_dump()["trusted_symptoms"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("index,state", [(3, "SUPPORTED"), (4, "NEEDS_REVIEW"),
                                        (5, "NEEDS_REVIEW")])
async def test_readback_rules_preserve_http_and_repetition_requirements(tmp_path, index, state):
    import httpx

    from agenttest.contracts import load_openapi
    from agenttest.evidence_control import FixedEvidenceControl
    from agenttest.evidence_tasks import evidence_cases, evidence_inputs
    from agenttest.report import Outcome, ReportStore
    from agenttest.task_contract import task_validator
    from agenttest.workflow import FakeLLM, Workflow

    case = evidence_cases("end_to_end")[index]
    app, doc, target, registry, env, task, draft, caps = evidence_inputs(case)
    report = await Workflow(doc, target, registry, FakeLLM(draft.scenario().model_dump_json()),
        ReportStore(tmp_path), httpx.ASGITransport(app), env, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(doc), target, registry),
        investigation=InvestigationConfig(enabled=True, trusted_symptoms=True,
            feedback=FeedbackBudget(timeout=10)), capabilities=caps,
        analyzer=FixedEvidenceControl()).run()
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.trusted_symptom.state == state
    assert report.trusted_symptom.finding == case["finding"]
    if state == "SUPPORTED":
        plan = draft.scenario()
        parent = next(s for s in plan.steps if s.id == report.steps[-1].step_id)
        changed = copy.deepcopy(report.investigations)
        for read in changed:
            read.policy_template_sha256 = "0" * 64
        assert derive_symptom(report.steps[-1], parent, changed,
            {s.id: s.role for s in plan.steps}).state == "NEEDS_REVIEW"
