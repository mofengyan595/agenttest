"""Attribute correct abstention to a model only when it actually returned that action."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.eval_v2.scoring import capture_plan
from agenttest.investigation import FeedbackBudget, InvestigationConfig
from agenttest.paired_evaluation import grade_agent, grade_agent_v1
from agenttest.paired_tasks import paired_cases, paired_inputs
from agenttest.report import ReportStore
from agenttest.task_contract import task_validator


@pytest.mark.asyncio
@pytest.mark.parametrize("finding,correct", [("PERSISTENT_READBACK_MISMATCH", False),
                                           ("EVIDENCE_INSUFFICIENT", True)])
async def test_unsupported_guess_is_not_model_abstention(tmp_path, finding, correct):
    pytest.importorskip("langgraph")
    from agenttest.workflow import FakeLLM, Workflow

    class Finish:
        async def generate(self, context):
            return json.dumps({"action": "finish", "selection": None,
                               "finding": finding, "evidence_refs": []})

    case = paired_cases()[22]
    app, doc, target, policies, environ, task, draft, capabilities = paired_inputs(case)
    report = await Workflow(doc, target, policies, FakeLLM(draft.scenario().model_dump_json()),
        ReportStore(tmp_path), httpx.ASGITransport(app), environ, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(doc), target, policies),
        investigation=InvestigationConfig(enabled=True, feedback=FeedbackBudget()),
        capabilities=capabilities, analyzer=Finish()).run()
    plan = capture_plan(draft.scenario())
    assert report.outcome.value == "ASSERTION_FAILED"
    assert report.feedback.state == "NEEDS_REVIEW"
    assert grade_agent_v1(case, task, report, plan)["conclusion_correct"] is True
    strict = grade_agent(case, task, report, plan)
    assert strict["business_correct"] is True
    assert strict["conclusion_correct"] is correct
    assert report.feedback.reason == ("FEEDBACK_EVIDENCE_INSUFFICIENT" if correct else
                                      "FEEDBACK_UNSUPPORTED_FINDING")
