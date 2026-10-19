"""Fresh-task mechanism checks and strict separation of product/model credit."""

import json
from pathlib import Path

import httpx
import pytest

pytest.importorskip("langgraph")

from agenttest.contracts import load_openapi
from agenttest.evidence_control import FixedEvidenceControl
from agenttest.investigation import FeedbackBudget, InvestigationConfig
from agenttest.nl_evaluation import run_nl, schedule_for, summarize
from agenttest.nl_scoring import score_nl
from agenttest.nl_tasks import cases, task_inputs_for
from agenttest.planner_input import planning_context
from agenttest.report import Outcome, ReportStore
from agenttest.task_contract import task_validator
from agenttest.workflow import FakeLLM, Workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("case", cases("development"), ids=lambda c: c["id"])
async def test_development_fixtures_detect_designated_checks_and_healthy_pairs(case, tmp_path):
    for healthy in (False, True):
        app, doc, target, registry, env, task, draft, caps = task_inputs_for(case, healthy=healthy)
        context = planning_context(load_openapi(doc), target, registry, task.user_task, {})
        assert all(p.get("purpose") for p in context["policies"])
        assert not {"requirements", "fault", "sequence", "finding", "case_id"} & context.keys()
        report = await Workflow(doc, target, registry, FakeLLM(draft.scenario().model_dump_json()),
            ReportStore(tmp_path / str(healthy)), httpx.ASGITransport(app), env,
            user_task=task.user_task,
            intent_validator=task_validator(task, load_openapi(doc), target, registry),
            investigation=InvestigationConfig(enabled=True, trusted_symptoms=True,
                feedback=FeedbackBudget(timeout=10)), capabilities=caps,
            analyzer=FixedEvidenceControl()).run()
        expected = dict(case, category="healthy", finding=None, fault="none") if healthy else case
        grade = score_nl(expected, task, report, draft.scenario())
        assert grade["product_complete"], (case["id"], healthy, report.reason, report.diagnostic,
            [(s.step_id, s.reason, [c.selector for c in s.checks if not c.passed])
             for s in report.steps], report.trusted_symptom)
        assert grade["safety_preserved"]
        assert report.outcome == (Outcome.PASS if healthy or case["category"] == "healthy"
                                  else Outcome.ASSERTION_FAILED)


def test_new_validation_scope_and_counterbalanced_denominators():
    dev, validation = cases("development"), cases("validation")
    assert len(dev) <= 6 and len(validation) == 12
    assert len({c["user_task"] for c in dev + validation}) == len(dev + validation)
    assert {category: sum(c["category"] == category for c in validation)
            for category in ("healthy", "access", "readback", "insufficient")} == {
                "healthy": 6, "access": 2, "readback": 2, "insufficient": 2}
    assert {c["project"] for c in validation} == {"courses", "tickets"}
    schedule = schedule_for(validation, 3)
    assert len(schedule) == 72
    assert all(sum(s["arm"] == arm for s in schedule) == 36
               for arm in ("baseline", "candidate"))
    assert len({tuple(c["sequence"]) for c in validation}) >= 9
    from agenttest.demo import PROJECT_ROOT
    from agenttest.eval_v2.review import task_identity

    exclusions = json.loads((PROJECT_ROOT / "examples/eval-v2/goal-used-task-identities.json").
                            read_bytes())["tasks"]
    registered = {x["case_id"]: x["task_identity"] for x in exclusions}
    assert all(registered[c["id"]] == task_identity(c["user_task"]) for c in dev + validation)


def test_paid_dirty_source_rejected_before_environment_or_worker(tmp_path, monkeypatch):
    import agenttest.nl_evaluation as evaluation

    monkeypatch.setattr(evaluation, "checkout_identity", lambda root: {"worktree_status": "dirty"})
    monkeypatch.setattr(evaluation, "private_output", lambda *args: None)
    with pytest.raises(ValueError, match="clean committed"):
        run_nl(tmp_path / "never_created", "development", model_env=Path("absent-private.env"))
    assert not (tmp_path / "never_created").exists()


def test_unknown_attempt_remains_in_denominator_with_unknown_cost():
    slot = dict(case_id="unknown", category="healthy", arm="baseline", round=1)
    summary = summarize([dict(**slot, run_id=None, attempted_unknown=True)], [slot], "real")
    group = summary["groups"]["baseline/all"]
    assert summary["attempted"] == 1 and group["denominator"] == 1
    assert group["unknown_usage_positions"] == 1 and group["total_tokens"] is None
    assert not group["metrics"].get("product_complete", 0)
    assert summary["review_status"] == "UNREVIEWED"
    assert json.dumps(summary)


@pytest.mark.asyncio
async def test_rule_correction_is_never_model_credit(tmp_path):
    from agenttest.evidence_control import fixed_decision

    class WrongAbstention:
        evidence_relations = True

        async def generate(self, context):
            action = fixed_decision(context)
            if action["action"] == "finish":
                action.update(finding="EVIDENCE_INSUFFICIENT", evidence_refs=[])
            return json.dumps(action)

    case = cases("development")[4]
    app, doc, target, registry, env, task, draft, caps = task_inputs_for(case)
    report = await Workflow(doc, target, registry, FakeLLM(draft.scenario().model_dump_json()),
        ReportStore(tmp_path), httpx.ASGITransport(app), env, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(doc), target, registry),
        investigation=InvestigationConfig(enabled=True, trusted_symptoms=True,
            feedback=FeedbackBudget(timeout=10)), capabilities=caps,
        analyzer=WrongAbstention()).run()
    grade = score_nl(case, task, report, draft.scenario())
    assert grade["product_complete"] and grade["rule_supported"]
    assert grade["model_active_abstention"]
    assert not grade["model_conclusion_correct"] and not grade["model_supported_proposal"]


@pytest.mark.asyncio
async def test_safe_intent_rejection_is_not_normal_completion(tmp_path):
    case = cases("development")[0]
    app, doc, target, registry, env, task, draft, _ = task_inputs_for(case)
    invalid = draft.scenario()
    invalid.steps.pop()
    report = await Workflow(doc, target, registry, FakeLLM(invalid.model_dump_json()),
        ReportStore(tmp_path), httpx.ASGITransport(app), env, user_task=task.user_task,
        intent_validator=task_validator(task, load_openapi(doc), target, registry)).run()
    grade = score_nl(case, task, report, invalid)
    assert report.outcome == Outcome.NEEDS_REVIEW and not report.steps
    assert not grade["normal_complete"] and not grade["product_complete"]


def test_isolated_worker_preserves_workflow_schema_context(tmp_path):
    import subprocess
    import sys

    from agenttest.demo import PROJECT_ROOT
    from agenttest.dsl import Scenario

    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(cases("development")[0]), encoding="utf-8")
    result = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts/run_nl_worker.py"),
        "--engine-root", str(PROJECT_ROOT), "--case-file", str(case_file),
        "--output", str(tmp_path / "worker"), "--arm", "candidate", "--mode", "mock"],
        capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout)["outcome"] == "PASS"
    prepared = json.loads((tmp_path / "worker/prepared.json").read_bytes())
    assert prepared["context"]["plan_schema"] == Scenario.model_json_schema()
    public = json.loads(prepared["payload"]["messages"][1]["content"])
    assert not {"requirements", "sequence", "finding", "task_contract"} & public.keys()
