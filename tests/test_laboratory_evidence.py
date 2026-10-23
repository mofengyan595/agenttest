"""Real ASGI observations test rule/model/interception separation; model replies are fixtures."""

import json

import httpx
import pytest
from test_laboratory_portability import ROOT, runtime

from agenttest.goal_runtime import run_goal
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.project_goals import load_goal_project
from agenttest.report import ModelCallStats, Outcome, ReportStore


class DecisionFixture:
    evidence_relations = True

    def __init__(self, mode):
        self.mode = mode
        self.stats = ModelCallStats()
        self.calls = 0

    async def generate(self, context):
        self.calls += 1
        self.stats = ModelCallStats(calls=1, total_tokens=30, model_name="SIMULATED")
        if self.mode == "invalid_json":
            return "not a structured decision"
        if self.mode == "foreign_report":
            return json.dumps(dict(action="finish", selection=None,
                finding="PERSISTENT_READBACK_MISMATCH", evidence_refs=["original", "foreign_run"]))
        if self.mode in {"investigate_then_abstain", "investigate_then_claim"} and self.calls == 1:
            return json.dumps(dict(action="investigate", selection=context["choices"][0],
                                   finding=None, evidence_refs=[]))
        return json.dumps(dict(action="finish", selection=None,
            finding="PERSISTENT_READBACK_MISMATCH" if self.mode == "investigate_then_claim"
                else "EVIDENCE_INSUFFICIENT",
            evidence_refs=["original", context["evidence"][0]["ref"]]
                if self.mode == "investigate_then_claim" else []))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,mode,rule,model_state", [
    ("evidence_gap", "abstain", "NEEDS_REVIEW", "NEEDS_REVIEW"),
    ("evidence_gap", "investigate_then_abstain", "NEEDS_REVIEW", "NEEDS_REVIEW"),
    ("evidence_gap", "investigate_then_claim", "NEEDS_REVIEW", "NEEDS_REVIEW"),
    ("readback", "investigate_then_claim", "SUPPORTED", "SUPPORTED"),
    ("readback", "investigate_then_abstain", "SUPPORTED", "NEEDS_REVIEW"),
    ("evidence_gap", "invalid_json", "NEEDS_REVIEW", "NEEDS_REVIEW"),
    ("evidence_gap", "foreign_report", "NEEDS_REVIEW", "NEEDS_REVIEW"),
])
async def test_observed_evidence_and_active_abstention_have_separate_attribution(
    tmp_path, fault, mode, rule, model_state,
):
    pytest.importorskip("langgraph")
    project = load_goal_project(ROOT / "registered.project.json")
    app, env = runtime(fault)
    model = DecisionFixture(mode)
    store = ReportStore(tmp_path)
    _, report = await run_goal(project, "装载、查验", CatalogFixturePlanner(), store,
        environ=env, transport=httpx.ASGITransport(app=app), analyzer=model)
    assert report.outcome == Outcome.ASSERTION_FAILED
    assert report.steps[-1].outcome == Outcome.ASSERTION_FAILED and not report.task_satisfied
    assert report.trusted_symptom.state == rule and report.feedback.state == model_state
    assert report.feedback.model_calls_used == model.calls <= 3
    assert len(report.investigations) <= 2
    assert app.state.requests == 4 + len(report.investigations)
    last = report.feedback.decisions[-1]
    if mode in {"invalid_json", "foreign_report"}:
        assert report.feedback.finding == "EVIDENCE_INSUFFICIENT"
        assert not (last.action == "finish" and last.proposal
                    and last.proposal.get("finding") == "EVIDENCE_INSUFFICIENT")
    elif mode in {"abstain", "investigate_then_abstain"}:
        assert last.action == "finish" and last.proposal["finding"] == "EVIDENCE_INSUFFICIENT"
    elif mode == "investigate_then_claim" and fault == "evidence_gap":
        assert last.proposal["finding"] == "PERSISTENT_READBACK_MISMATCH"
        assert report.feedback.finding == "EVIDENCE_INSUFFICIENT"
    assert store.read(report.run_id) == report
