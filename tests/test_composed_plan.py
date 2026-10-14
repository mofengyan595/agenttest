"""Bounded goal selection and independent execution evidence; no graph extra required."""

import json

import pytest
from pydantic import ValidationError

from agenttest.composed_check import SUITE_PATH
from agenttest.composed_demo import composed_inputs
from agenttest.composed_evaluation import (
    CompositionSuite,
    capture_composition,
    goal_rubric,
    reference_draft,
    score_composition,
)
from agenttest.composed_intent import interpret_composed_task, validate_composed_intent
from agenttest.composed_plan import ComposedPlannerDraft, composed_registry
from agenttest.contracts import summarize_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import ArgumentBinding
from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.scoring import score_intent
from agenttest.planner_input import planning_context
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.report import fingerprint

SUITE = CompositionSuite.model_validate(read_json(SUITE_PATH))
COMPLETE = SUITE.cases[4]


@pytest.mark.parametrize("case", SUITE.cases, ids=lambda c: c.case_id)
def test_new_development_labels_agree_with_runtime_grammar(case):
    if case.expected:
        plan = reference_draft(case.expected).scenario()
        validate_composed_intent(case.user_task, plan)
        registry = composed_registry()
        score = score_composition(goal_rubric(case.expected),
                                  capture_composition(plan, registry), registry)
        assert score.passed and set(score.covered_goals) == set(case.expected.goals)
    else:
        with pytest.raises(NeedsReview) as exc:
            interpret_composed_task(case.user_task)
        assert exc.value.diagnostic.code == case.rejection_code


def test_every_task_sees_the_same_full_catalog_without_labels_or_answers():
    target, config, registry, environ = composed_inputs()
    contract, contexts = summarize_openapi(target.openapi()), []
    for case in SUITE.cases:
        context = planning_context(contract, config, registry, case.user_task,
                                   ComposedPlannerDraft.model_json_schema())
        assert context.pop("user_task") == case.user_task
        contexts.append(context)
    assert all(c == contexts[0] for c in contexts)
    assert len(contexts[0]["operations"]) == 6 and len(contexts[0]["policies"]) == 7
    assert contexts[0]["roles"] == ["alice", "bob"]
    raw = json.dumps(contexts[0])
    for private in [*environ.values(), "expected", "rejection_code", "rubric", "P001",
                    "first synthetic row", "max_attempts", "DEMO_ALICE", "base_url"]:
        if private in {"alice", "bob"}:
            continue
        assert private not in raw


def test_namespacing_preserves_approved_assertions_roles_fixtures_and_limits():
    old = {p.id: p for filename in ("nl-policies.json", "batch-policies.json")
           for p in PolicyRegistry.from_json((PROJECT_ROOT / "examples" / filename).read_bytes()
                                             ).definitions()}
    new = {p.id: p for p in composed_registry().definitions()}
    mapping = dict(zip(
        ("nl_login_v1", "nl_create_v1", "nl_read_v1", "nl_forbidden_v1",
         "batch_upload_v1", "batch_poll_v1", "batch_results_v1"),
        ("composed_login_v1", "composed_create_v1", "composed_read_v1", "composed_forbidden_v1",
         "composed_upload_v1", "composed_poll_v1", "composed_results_v1")))
    for source, dest in mapping.items():
        assert new[dest].assertions == old[source].assertions
        assert new[dest].roles == old[source].roles
        assert new[dest].csv_fixture == old[source].csv_fixture
        assert new[dest].poll == old[source].poll
        assert new[dest].required_bindings == {"poll": old[source].poll is not None,
                                               "read_retries": 0}
    assert new["composed_upload_v1"].binding_rules[0].source_policy_id == "composed_create_v1"


def test_independent_steps_may_interleave_and_clause_order_does_not_set_plan_order():
    plan = reference_draft(COMPLETE.expected).scenario()
    steps = {s.id: s for s in plan.steps}
    plan.steps = [steps[k] for k in (
        "other_login", "login", "create", "upload", "poll", "read", "denied", "results")]
    validate_composed_intent(COMPLETE.user_task, plan)
    registry = composed_registry()
    assert score_composition(goal_rubric(COMPLETE.expected),
                             capture_composition(plan, registry), registry).passed


@pytest.mark.parametrize("mutation", [
    "content", "path", "poll_limit", "wrong_poll_id", "fixture_elsewhere", "role",
    "policy", "headers", "retries",
])
def test_typed_choices_cannot_invent_execution_authority(mutation):
    data = reference_draft(COMPLETE.expected).model_dump()
    upload = next(s for s in data["steps"] if s["id"] == "upload")
    if mutation == "content":
        upload["upload"] = {"content": "arbitrary"}
    elif mutation == "path":
        upload["csv_fixture_id"] = "C:/private.csv"
    elif mutation == "poll_limit":
        next(s for s in data["steps"] if s["id"] == "poll")["max_attempts"] = 100
    elif mutation == "wrong_poll_id":
        next(s for s in data["steps"] if s["id"] == "poll")["poll_policy_id"] = "batch_poll_v1"
    elif mutation == "fixture_elsewhere":
        data["steps"][0]["csv_fixture_id"] = "mini_two_rows_v1"
    elif mutation == "role":
        upload["role"] = "bob"
    elif mutation == "policy":
        upload["policy_id"] = "composed_read_v1"
    elif mutation == "headers":
        upload["headers"] = {"Authorization": "untrusted"}
    else:
        upload["read_retries"] = 1
    with pytest.raises(ValidationError):
        ComposedPlannerDraft.model_validate(data)


@pytest.mark.parametrize("mutation", [
    "csv_content", "csv_field", "csv_filename", "csv_missing", "poll_off", "poll_elsewhere",
    "retry", "name", "selector", "resource", "literal_id", "missing_goal", "extra_step",
    "role", "order", "registry", "assertion_template", "binding_rules", "poll_limits",
])
def test_independent_grade_rejects_missing_or_changed_semantic_evidence(mutation):
    registry = composed_registry()
    plan = reference_draft(COMPLETE.expected).scenario()
    steps = {s.id: s for s in plan.steps}
    if mutation.startswith("csv_"):
        if mutation == "csv_missing":
            steps["upload"].upload = None
        else:
            field = {"csv_content": "content", "csv_field": "field",
                     "csv_filename": "filename"}[mutation]
            setattr(steps["upload"].upload, field, "unapproved")
    elif mutation == "poll_off":
        steps["poll"].poll = False
    elif mutation == "poll_elsewhere":
        steps["results"].poll = True
    elif mutation == "retry":
        steps["read"].read_retries = 1
    elif mutation == "name":
        steps["create"].body["name"].value = "wrong goal"
    elif mutation == "selector":
        steps["poll"].path_parameters["batchId"].select = "$.courseId"
    elif mutation == "resource":
        steps["results"].path_parameters["batchId"].from_step = "poll"
    elif mutation == "literal_id":
        steps["read"].path_parameters["courseId"] = ArgumentBinding(value=1)
    elif mutation == "missing_goal":
        plan.steps.remove(steps["denied"])
    elif mutation == "extra_step":
        plan.steps.append(steps["read"].model_copy(update={"id": "extra"}))
    elif mutation == "role":
        steps["read"].role = "bob"
    elif mutation == "order":
        plan.steps[-1], plan.steps[-2] = plan.steps[-2], plan.steps[-1]
    evidence = capture_composition(plan, registry)
    captured = {s.step_id: s for s in evidence.steps}
    if mutation == "registry":
        evidence.registry_sha256 = "0" * 64
    elif mutation == "assertion_template":
        captured["results"].execution.assertion_template_sha256 = fingerprint({})
    elif mutation == "binding_rules":
        captured["read"].execution.binding_rules_sha256 = fingerprint([])
    elif mutation == "poll_limits":
        captured["poll"].execution.poll_policy_sha256 = fingerprint({"max_attempts": 100})
    score = score_composition(goal_rubric(COMPLETE.expected), evidence, registry)
    assert not score.passed, mutation
    assert score.diagnostics


def test_formal_eval_v2_still_rejects_unlabeled_upload_and_poll():
    plan = reference_draft(COMPLETE.expected).scenario()
    old_score = score_intent(goal_rubric(COMPLETE.expected), plan)
    assert not old_score.passed
    assert "UNLABELED_EXECUTION_OPTION:upload" in old_score.diagnostics
    assert "UNLABELED_EXECUTION_OPTION:poll" in old_score.diagnostics


def test_development_scoring_never_calls_the_runtime_task_parser(monkeypatch):
    def unexpected(*args):
        pytest.fail("An independent grade cannot call the runtime intent gate")

    monkeypatch.setattr("agenttest.composed_intent.interpret_composed_task", unexpected)
    monkeypatch.setattr("agenttest.composed_intent.validate_composed_intent", unexpected)
    registry = composed_registry()
    plan = reference_draft(COMPLETE.expected).scenario()
    assert score_composition(goal_rubric(COMPLETE.expected),
                             capture_composition(plan, registry), registry).passed


@pytest.mark.parametrize("mutation,missing_goals", [
    ("results", {"batch"}), ("read", {"consistency", "ownership"}),
    ("csv", {"batch"}), ("other_login", {"ownership"}),
    ("create", {"consistency", "ownership", "batch"}), ("order", {"batch"}),
    ("registry", {"consistency", "ownership", "batch"}),
])
def test_covered_goals_require_every_related_step_and_transitive_prerequisite(mutation,
                                                                            missing_goals):
    registry = composed_registry()
    evidence = capture_composition(reference_draft(COMPLETE.expected).scenario(), registry)
    steps = {s.step_id: s for s in evidence.steps}
    if mutation in {"results", "other_login"}:
        evidence.steps.remove(steps[mutation])
    elif mutation == "read":
        steps["read"].selectors["path_parameters.courseId"] = "$.batchId"
    elif mutation == "csv":
        steps["upload"].execution.upload_sha256 = "0" * 64
    elif mutation == "create":
        steps["create"].argument_sha256["body.name"] = fingerprint("wrong name")
    elif mutation == "order":
        evidence.steps[-1], evidence.steps[-2] = evidence.steps[-2], evidence.steps[-1]
    else:
        evidence.registry_sha256 = "0" * 64
    score = score_composition(goal_rubric(COMPLETE.expected), evidence, registry)
    assert not score.passed
    assert set(score.covered_goals) == set(COMPLETE.expected.goals) - missing_goals


def test_generated_development_labels_cannot_self_certify_human_review():
    data = SUITE.model_dump()
    data["cases"][0]["review_status"] = "HUMAN_REVIEWED"
    with pytest.raises(ValidationError):
        CompositionSuite.model_validate(data)
