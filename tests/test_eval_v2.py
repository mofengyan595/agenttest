"""Rubric correctness and evidence/denominator boundaries; no optional graph needed."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from agenttest import llm_planner
from agenttest.demo import PROJECT_ROOT
from agenttest.diagnostics import Diagnostic, Layer
from agenttest.dsl import ArgumentBinding
from agenttest.eval_v2.context import assert_no_labels, build_context
from agenttest.eval_v2.faults import pair_result, summarize_faults
from agenttest.eval_v2.freeze import Freeze
from agenttest.eval_v2.models import CaseResult, Suite
from agenttest.eval_v2.runner import EvalConfig, ObservedTransport, oracle_plan, prepare_inputs
from agenttest.eval_v2.scoring import (
    capture_plan,
    distribution,
    score_intent,
    score_report,
    summarize,
)
from agenttest.report import AttemptEvidence, CheckResult, Outcome, RunReport, StepResult

DATA = PROJECT_ROOT / "examples/eval-v2/c-drafts.json"


def suite(*ids):
    value = Suite.load(DATA)
    if ids:
        value.cases = [c for c in value.cases if c.case_id in ids]
    return value


def report():
    return RunReport(
        scenario_sha256="synthetic",
        contract_sha256="synthetic",
        environment="local",
        target_origin="http://127.0.0.1:8080",
        outcome=Outcome.PASS,
        plan_valid=True,
        dsl_valid=True,
        policy_authorized=True,
        intent_verified=True,
        completed=True,
        workflow_completed=True,
        task_mode="natural_language",
        task_satisfied=True,
        steps=[
            StepResult(
                step_id="read",
                operation_id="getCourse",
                method="GET",
                path_template="/courses/{courseId}",
                outcome=Outcome.PASS,
                reason="",
                policy_sha256="synthetic",
                attempts=[AttemptEvidence(attempt=1, status_code=200, elapsed_ms=1)],
            )
        ],
    )


def row(case, **kwargs):
    return CaseResult(
        case_id=case.case_id,
        category=case.category,
        source="fake",
        variant="A",
        classification="NOT_ATTEMPTED_BUDGET",
        usage_status="NOT_APPLICABLE",
        **kwargs,
    )


def test_drafts_are_explicitly_not_independent_or_human_reviewed():
    tasks = suite()
    assert len(tasks.cases) == 27 and len({c.category for c in tasks.cases}) == 9
    assert all(c.labels.review_status == "UNREVIEWED" for c in tasks.cases)
    assert sum(c.labels.expected_behavior == "execute" for c in tasks.cases) == 8
    assert sum(c.labels.expected_behavior == "reject" for c in tasks.cases) == 8
    with pytest.raises(ValueError, match="human-reviewed"):
        tasks.require_formal()


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing_labels",
        "missing_rubric",
        "missing_basis",
        "fake_review",
        "cycle",
        "missing_goal",
    ],
)
def test_invalid_labels_or_schema_fail(mutation):
    tasks = suite("C004", "C022").model_dump(mode="json")
    case = tasks["cases"][0]
    if mutation == "duplicate":
        tasks["cases"].append(case)
    elif mutation == "missing_labels":
        del case["labels"]
    elif mutation == "missing_rubric":
        case["labels"]["rubric"] = None
    elif mutation == "missing_basis":
        tasks["cases"][1]["labels"]["rejection_basis"] = []
    elif mutation == "fake_review":
        case["labels"].update(review_status="HUMAN_REVIEWED", review_source="AI said reviewed")
    elif mutation == "cycle":
        case["labels"]["rubric"]["before"].append(["read", "login_owner"])
    else:
        case["labels"]["rubric"]["required_goals"].append("not_labeled")
    with pytest.raises(ValidationError):
        Suite.model_validate_json(json.dumps(tasks))


def test_duplicate_json_key_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"cases": [], "cases": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        Suite.load(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "role",
        "operation",
        "policy",
        "value",
        "parameter",
        "source",
        "selector",
        "order",
        "extra",
        "missing",
        "poll",
        "retry",
        "partial",
    ],
)
def test_independent_rubric_detects_wrong_plan(mutation):
    case = suite("C004").cases[0]
    plan = oracle_plan(case)
    assert score_intent(case.labels.rubric, plan).passed
    if mutation == "role":
        plan.steps[1].role = "bob"
    elif mutation == "operation":
        plan.steps[1].operation_id = "getCourse"
    elif mutation == "policy":
        plan.steps[1].policy_id = "nl_read_v1"
    elif mutation == "value":
        plan.steps[1].body["name"] = ArgumentBinding(value="wrong")
    elif mutation == "parameter":
        plan.steps[1].body["other"] = plan.steps[1].body.pop("name")
    elif mutation == "source":
        plan.steps[2].path_parameters["courseId"].from_step = "login_owner"
    elif mutation == "selector":
        plan.steps[2].path_parameters["courseId"].select = "$.wrongId"
    elif mutation == "order":
        plan.steps[0], plan.steps[1] = plan.steps[1], plan.steps[0]
    elif mutation == "extra":
        extra = plan.steps[2].model_copy(deep=True)
        extra.id = "extra"
        plan.steps.append(extra)
    elif mutation == "missing":
        plan.steps.pop()
    elif mutation == "poll":
        plan.steps[2].poll = True
    elif mutation == "retry":
        plan.steps[2].read_retries = 1
    else:
        plan.steps = plan.steps[:1]
    score = score_intent(case.labels.rubric, plan)
    assert not score.passed and score.diagnostics
    # An execution safety flag cannot override a failing quality rubric.
    assert not score_report(case, report(), plan)["end_to_end_success"]


def test_rubric_accepts_renamed_ids_and_valid_login_permutations():
    case = suite("C007").cases[0]
    plan = oracle_plan(case)
    other_login = next(s for s in plan.steps if s.id == "login_other")
    plan.steps.remove(other_login)
    plan.steps.insert(0, other_login)
    for step in plan.steps:
        if step.id == "create":
            step.id = "new_resource"
        for binding in step.path_parameters.values():
            binding.from_step = "new_resource"
    assert score_intent(case.labels.rubric, plan).passed


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_completed", False),
        ("completed", False),
        ("task_satisfied", False),
        ("task_mode", "operator_plan"),
        ("policy_authorized", False),
        ("dsl_valid", False),
        ("investigation_error", "TOOL_ERROR"),
        ("steps", []),
    ],
)
def test_pass_alone_or_partial_workflow_never_counts_as_success(field, value):
    case, result = suite("C004").cases[0], report()
    setattr(result, field, value)
    assert not score_report(case, result, oracle_plan(case))["end_to_end_success"]


def test_selectors_are_not_replaced_by_intent_verified_flag():
    case, result = suite("C004").cases[0], report()
    plan = oracle_plan(case)
    plan.steps[-1].path_parameters["courseId"].select = "$.name"
    assert result.intent_verified and not score_report(case, result, plan)["intent"].passed


def test_pass_with_only_one_of_the_planned_steps_is_incomplete():
    case = suite("C004").cases[0]
    score = score_report(case, report(), oracle_plan(case))
    assert score["plan_valid"] and score["intent"].passed
    assert not score["end_to_end_success"] and score["classification"] == "TASK_MISMATCH"


def test_archived_structure_is_sufficient_without_raw_literals():
    case = suite("C004").cases[0]
    evidence = capture_plan(oracle_plan(case))
    assert "晴川研讨" not in evidence.model_dump_json()
    assert evidence.steps[-1].selectors == {"path_parameters.courseId": "$.courseId"}
    assert score_intent(case.labels.rubric, evidence).passed
    evidence.steps[-1].selectors["path_parameters.courseId"] = "$.wrong"
    assert not score_intent(case.labels.rubric, evidence).passed


def test_contract_identity_mismatch_blocks_candidate_input():
    from agenttest.contracts import load_openapi
    from agenttest.dsl import Scenario
    from agenttest.natural_demo import natural_inputs

    tasks = suite("C004")
    artifact = prepare_inputs(tasks, EvalConfig(variant="B"))[3]
    target, config, registry, _ = natural_inputs()
    contract = load_openapi(json.dumps(target.openapi()).encode())
    artifact.contract_sha256 = "changed"
    with pytest.raises(ValueError, match="identity mismatch"):
        build_context(
            tasks.cases[0].task,
            contract,
            config,
            registry,
            Scenario.model_json_schema(),
            discovery=artifact,
        )


def test_freeze_detects_source_file_edits_and_content_not_only_status(tmp_path):
    task_path = tmp_path / "suite.json"
    task_path.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    tasks, config = Suite.load(task_path), EvalConfig(development=True)
    frozen = Freeze(PROJECT_ROOT, tasks, {}, config, tmp_path, suite_path=task_path)
    task_path.write_text(task_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="FREEZE_CHANGED"):
        frozen.verify()


def test_denominators_include_budget_and_separate_rejection_future():
    tasks = suite("C004", "C005", "C022", "C019")
    rows = [row(c) for c in tasks.cases]
    rows[0].attempted = rows[0].plan_valid = True
    rows[2].attempted = rows[2].safe_rejection = True
    summary = summarize(tasks, rows)
    assert summary["requested"] == summary["eligible"] == 4
    assert summary["plan_validity"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert summary["conditional_plan_validity"]["rate"] == 1
    assert summary["safe_rejection"]["denominator"] == 1
    assert summary["total_tokens"] is None
    with pytest.raises(ValueError, match="Every requested"):
        summarize(tasks, rows[:-1])
    rows[0].source = "real"
    with pytest.raises(ValueError, match="Cannot pool"):
        summarize(tasks, rows)


def test_zero_denominator_and_percentile_minimum():
    tasks = suite("C019")
    summary = summarize(tasks, [row(tasks.cases[0])])
    assert summary["plan_validity"]["rate"] is None
    assert summary["safe_rejection"]["rate"] is None
    assert distribution([1.0])["p95"] is None
    assert distribution(list(range(1, 21)))["p95"] == 19


@pytest.mark.parametrize("kind", ["approved", "model_error", "infra_error", "executed"])
def test_rejection_requires_annotated_basis_no_execution_and_no_error(kind):
    case, result = suite("C022").cases[0], report()
    result.steps = []
    result.outcome = Outcome.NEEDS_REVIEW
    result.diagnostic = Diagnostic(layer=Layer.MODEL, code="MODEL_NO_APPROVED_PLAN")
    if kind == "model_error":
        result.outcome = Outcome.TOOL_ERROR
        result.diagnostic.code = "MODEL_INVALID_RESPONSE"
    elif kind == "infra_error":
        result.outcome = Outcome.TEST_INFRA_ERROR
        result.diagnostic.code = "MODEL_TIMEOUT"
    elif kind == "executed":
        result.steps = report().steps
    score = score_report(case, result, None)
    assert score["safe_rejection"] == (kind == "approved")


def test_candidate_context_ab_and_label_noninterference():
    tasks = suite("C004")
    base, with_candidates, inputs, artifact = prepare_inputs(tasks, EvalConfig(variant="B"))
    assert "candidate_discovery" not in base["C004"]
    b = with_candidates["C004"]
    extra = b.pop("candidate_discovery")
    assert b == base["C004"] and extra["candidates"]
    assert len(json.dumps(extra, ensure_ascii=False).encode()) <= 6000
    assert all(
        c["scenario_kind"] not in {"required_missing", "type_mismatch"} for c in extra["candidates"]
    )
    assert "STATIC_VALIDATED" not in json.dumps(extra)
    tasks.cases[0].labels.rubric.steps[1].role = "SENTINEL_HIDDEN_ANSWER"
    tasks.cases[0].labels.limitation = "SENTINEL_SCORE"
    assert prepare_inputs(tasks, EvalConfig(variant="B"))[1] == with_candidates | {
        "C004": {**base["C004"], "candidate_discovery": extra}
    }
    assert inputs["candidate_artifact_sha256"]
    assert artifact.model_calls == artifact.http_requests == 0
    with pytest.raises(TypeError):
        build_context(tasks.cases[0], None, None, None, {})


@pytest.mark.parametrize("key", ["labels", "required_roles", "fault_mechanism", "review_source"])
def test_recursive_label_leak_detection(key):
    with pytest.raises(ValueError, match="EVAL_LABEL_LEAK"):
        assert_no_labels({"nested": [{key: "secret"}]})


@pytest.mark.parametrize("drift", ["task", "label", "prompt", "config", "inputs", "manifest"])
def test_v2_freeze_detects_drift(tmp_path, monkeypatch, drift):
    tasks, config = suite("C004"), EvalConfig(development=True)
    inputs = prepare_inputs(tasks, config)[2]
    frozen = Freeze(PROJECT_ROOT, tasks, inputs, config, tmp_path)
    frozen.verify()
    if drift == "task":
        tasks.cases[0].task.user_task = "changed"
    elif drift == "label":
        tasks.cases[0].labels.limitation = "changed"
    elif drift == "prompt":
        monkeypatch.setattr(llm_planner, "SYSTEM_PROMPT", "changed")
    elif drift == "config":
        config.max_cases_attempted = 0
    elif drift == "inputs":
        inputs["contract_sha256"] = "changed"
    else:
        frozen.manifest["retries"] = 1
    with pytest.raises(ValueError, match="FREEZE_CHANGED"):
        frozen.verify()


def test_provenance_comes_from_transport_not_baseline():
    assert ObservedTransport(httpx.MockTransport(lambda request: None)).source == "mock_transport"
    with pytest.raises(ValueError, match="Unaudited"):
        ObservedTransport(object())
    with pytest.raises(ValueError):
        EvalConfig(source="real")
    with pytest.raises(ValueError):
        EvalConfig(
            source="real",
            real_opt_in=True,
            development=True,
            max_calls=1,
            max_total_tokens=25000,
            allowed_model="model",
            allowed_endpoint_sha256="sha",
        )


@pytest.mark.parametrize(
    "kind,status",
    [
        ("detected", "DETECTED"),
        ("pass", "FALSE_NEGATIVE"),
        ("wrong", "WRONG_MECHANISM"),
        ("infra", "INCONCLUSIVE"),
        ("none", "NOT_EXECUTED"),
        ("control", "INCONCLUSIVE_CONTROL"),
    ],
)
def test_paired_faults_fp_fn_and_inconclusive(kind, status):
    normal, fault = report(), report()
    fault.steps[0].checks = [CheckResult(kind="field", selector="$.name", passed=False)]
    if kind != "pass":
        fault.outcome = Outcome.ASSERTION_FAILED
    if kind == "wrong":
        fault.steps[0].checks[0].selector = "$.courseId"
    elif kind == "infra":
        fault.outcome = Outcome.TEST_INFRA_ERROR
    elif kind == "none":
        fault.steps = []
    elif kind == "control":
        normal.outcome = Outcome.ASSERTION_FAILED
    pair = pair_result("C016", "response_name", normal, fault, True)
    assert pair["status"] == status
    metrics = summarize_faults([pair])
    assert metrics["executed_false_positive"]["numerator"] == (kind == "control")
    assert metrics["executed_false_negative"]["numerator"] == (kind == "pass")
    assert metrics["coverage"]["denominator"] == 1


def test_unknown_usage_is_null_in_summary():
    tasks = suite("C004", "C005")
    rows = [row(c, model_calls=1) for c in tasks.cases]
    rows[0].total_tokens = 100
    rows[0].input_tokens, rows[0].output_tokens = 80, 20
    rows[1].usage_status = "MODEL_USAGE_UNAVAILABLE"
    assert summarize(tasks, rows)["total_tokens"] is None
    assert summarize(tasks, rows)["input_tokens"] is None


def test_files_are_only_local_repository_refs():
    for case in suite().cases:
        assert case.task.target_profile_id == "mini_nl_v1"
        assert not Path(case.task.contract_ref).is_absolute()
