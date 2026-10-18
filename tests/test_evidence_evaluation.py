"""Fresh task fixtures must fail at the intended check and pass unchanged on healthy targets."""

import json

import httpx
import pytest

pytest.importorskip("langgraph")

from agenttest.contracts import load_openapi
from agenttest.evidence_control import FixedEvidenceControl
from agenttest.evidence_evaluation import score_feedback
from agenttest.evidence_tasks import evidence_cases, evidence_inputs
from agenttest.investigation import FeedbackBudget, InvestigationConfig
from agenttest.report import ReportStore
from agenttest.task_contract import task_validator
from agenttest.workflow import FakeLLM, Workflow

CASES = [c for split in ("development", "validation", "end_to_end") for c in evidence_cases(split)]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_fixed_plan_controls_and_healthy_pairs(case, tmp_path):
    for healthy in (True, False):
        app, doc, target, registry, env, task, draft, caps = evidence_inputs(case, healthy=healthy)
        report = await Workflow(doc, target, registry, FakeLLM(draft.scenario().model_dump_json()),
            ReportStore(tmp_path / str(healthy)), httpx.ASGITransport(app), env,
            user_task=task.user_task,
            intent_validator=task_validator(task, load_openapi(doc), target, registry),
            investigation=InvestigationConfig(enabled=True, feedback=FeedbackBudget(timeout=10)),
            capabilities=caps, analyzer=FixedEvidenceControl()).run()
        expected = dict(case, category="healthy", finding=None, fault="none") if healthy else case
        grade = score_feedback(expected, report, draft.scenario())
        assert grade["business_correct"] and grade["strict_correct"], (
            case["id"], healthy, report.outcome, report.reason, report.diagnostic,
            [(s.step_id, [c.selector for c in s.checks if not c.passed]) for s in report.steps])
        assert grade["safety_preserved"]
        for decision in report.feedback.decisions if report.feedback else []:
            public = json.dumps(decision.public_context)
            assert all(key not in public for key in (
                "fault", "finding", "score", "expected_symptom"))


def test_task_semantics_include_real_phase_and_dependency_changes():
    cases = evidence_cases("validation")
    assert {c["category"]: sum(x["category"] == c["category"] for x in cases)
            for c in cases} == {"access": 4, "readback": 4, "insufficient": 4}
    assert {c["project"] for c in cases} == {"courses", "tickets"}
    plans = [evidence_inputs(c)[6].scenario() for c in cases]
    assert any(sum(s.operation_id == "createCourse" for s in p.steps) == 2 for p in plans)
    assert any(any(s.operation_id == "closeTicket" for s in p.steps) and
               any(s.policy_id == "ticket_closed_read_v1" for s in p.steps) for p in plans)
    assert any(p.steps[0].operation_id == "listTickets" for p in plans)


def test_mock_fixture_requires_http_checks_before_corroborating_fields():
    from agenttest.evidence_evaluation import mock_decision

    check = {"kind": "equals", "selector": "$.name", "passed": False}
    failure = dict(ref="original", operation_id="read", role="owner", checks=[check,
        dict(kind="http_status", selector=None, passed=False, expected=[200], actual=503)])
    read = dict(failure, ref="read1", outcome="ASSERTION_FAILED")
    action = mock_decision(dict(failure=failure, evidence=[read], choices=[], finish_required=True))
    assert action["finding"] == "EVIDENCE_INSUFFICIENT"


def test_scoring_identity_separates_mock_fixture_from_rules():
    from agenttest.evidence_evaluation import scoring_rules_identity

    source = ("def score_feedback(): return False\n"
              "def evaluation_row(): return 1\n"
              "def summary(): return 2\n"
              "def mock_decision(): return 3\n")
    assert scoring_rules_identity(source) == scoring_rules_identity(source.replace(
        "return 3", "return 4"))
    assert scoring_rules_identity(source) != scoring_rules_identity(source.replace(
        "return False", "return True"))


def test_git_source_decodes_utf8_independently_of_console(monkeypatch):
    from types import SimpleNamespace

    from agenttest import evidence_evaluation

    monkeypatch.setattr(evidence_evaluation.subprocess, "run", lambda *a, **kw:
        SimpleNamespace(stdout="中文源码".encode("utf-8")))
    assert evidence_evaluation.git_source("sha", "source.py") == "中文源码"
