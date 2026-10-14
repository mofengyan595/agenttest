"""Synthetic review records exercise consistency gates, never certify human review."""

import json

import pytest
from pydantic import ValidationError

from agenttest.composed_check import SUITE_PATH
from agenttest.composed_planning_protocol import PlanningConfig, PlanningLedger, planning_settings
from agenttest.composed_review import (
    PlanningCase,
    PlanningSuite,
    freeze_planning_review,
    label_payload,
    planning_review_packet,
    planning_review_template,
    require_planning_review,
    trusted_composition,
    verify_planning_review,
)
from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.review import ReviewRecord
from agenttest.report import fingerprint


def synthetic_reviewed_suite():
    """Unit-test-only consistency fixture; this is not an actual independent reviewer."""
    case = PlanningSuite.load(SUITE_PATH).cases[0].model_copy(deep=True)
    case.case_id = "P101"
    case.user_task = case.user_task.replace("组合一致性", "仅供单元测试的审核记录")
    case.expected.course_name = "仅供单元测试的审核记录"
    record = ReviewRecord(
        source_ref="SYNTHETIC_UNIT_TEST_ONLY",
        source_content_sha256=fingerprint("SYNTHETIC_UNIT_TEST_ONLY"),
        sampled_at="2026-10-14T00:00:00+08:00",
        sampling_method="synthetic fixture, not actual independent sampling",
        participated_in_development=False,
        operator_independence_confirmed=True,
        reviewer_ref="SYNTHETIC_UNIT_TEST_ONLY",
        reviewed_at="2026-10-14T00:01:00+08:00",
        decision="execute",
        rationale="Synthetic record consistency check only",
        task_sha256=fingerprint(case.user_task),
        labels_sha256=fingerprint(label_payload(case)),
        **trusted_composition()[-1],
        acceptable_topology="owner login create read",
        forbidden_extras="No extra operations",
        rejection_evidence=[],
    )
    case.review_status, case.label_provenance = "HUMAN_REVIEWED", "independent_human"
    case.review_record = record
    return PlanningSuite.model_validate(
        dict(
            dataset_version="synthetic-test-review-v1",
            split="independent_holdout",
            cases=[case.model_dump()],
        )
    )


def test_review_template_and_legacy_conversion_never_promote_drafts():
    suite = PlanningSuite.load(SUITE_PATH)
    assert len(suite.cases) == 12 and suite.split == "development"
    assert all(c.review_status == "UNREVIEWED" and c.review_record is None for c in suite.cases)
    template = planning_review_template()
    assert template["status"] == "UNREVIEWED" and not template["ready_for_paid_run"]
    assert template["case"]["user_task"] == "" and template["model_calls"] == 0
    with pytest.raises(ValueError, match="Independently reviewed"):
        require_planning_review(suite)


def test_review_packet_prefills_hashes_but_never_independence_or_human_identity():
    suite = PlanningSuite.load(SUITE_PATH)
    packet = planning_review_packet(suite)
    assert packet["contains_used_tasks"] and not packet["ready_for_paid_run"]
    assert all(c["review_status"] == "UNREVIEWED" for c in packet["cases"])
    record = packet["cases"][0]["review_record"]
    assert record["task_sha256"] == fingerprint(suite.cases[0].user_task)
    assert record["labels_sha256"] == fingerprint(label_payload(suite.cases[0]))
    assert record["reviewer_ref"] is None and not record["operator_independence_confirmed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("participated_in_development", True),
        ("operator_independence_confirmed", False),
        ("task_sha256", "0" * 64),
        ("labels_sha256", "0" * 64),
        ("contract_sha256", "0" * 64),
        ("policy_sha256", "0" * 64),
        ("target_config_sha256", "0" * 64),
        ("fixture_version", "wrong-fixture"),
        ("decision", "reject"),
        ("reviewed_at", "2026-10-13T00:00:00+08:00"),
    ],
)
def test_incomplete_or_mismatched_records_fail_closed(field, value):
    suite = synthetic_reviewed_suite()
    setattr(suite.cases[0].review_record, field, value)
    with pytest.raises(ValueError, match="Incomplete or inconsistent"):
        require_planning_review(suite)


def test_review_lock_is_exclusive_and_binds_tasks_labels_and_trusted_inputs(tmp_path):
    suite, registry = synthetic_reviewed_suite(), tmp_path / "review-locks"
    path = registry / (suite.dataset_version + ".json")
    payload = freeze_planning_review(suite, registry)
    assert verify_planning_review(suite, path) == fingerprint(payload)
    with pytest.raises(FileExistsError):
        freeze_planning_review(suite, registry)
    suite.cases[0].review_record.rationale = "changed after review lock"
    with pytest.raises(ValueError, match="unchanged"):
        verify_planning_review(suite, path)


def test_renamed_used_case_or_format_only_change_is_not_independent():
    suite = synthetic_reviewed_suite()
    original = PlanningSuite.load(SUITE_PATH).cases[0]
    suite.cases[0].user_task = original.user_task.replace("；", "; ")
    with pytest.raises(ValueError, match="overlap"):
        require_planning_review(suite)


@pytest.mark.parametrize("filename", ["composed-constraints-development.json",
                                     "composed-order-development.json",
                                     "composed-arguments-development.json"])
def test_recent_development_tasks_cannot_be_enrolled_as_independent_holdout(filename):
    source = PlanningSuite.load(PROJECT_ROOT / "examples" / filename)
    used = next(c for c in source.cases if c.expected is not None)
    suite = synthetic_reviewed_suite()
    case = suite.cases[0]
    case.user_task, case.expected = used.user_task, used.expected
    case.review_record.task_sha256 = fingerprint(case.user_task)
    case.review_record.labels_sha256 = fingerprint(label_payload(case))
    with pytest.raises(ValueError, match="overlap"):
        require_planning_review(suite)


def test_human_review_flags_require_actual_independent_record_fields():
    case = PlanningSuite.load(SUITE_PATH).cases[0].model_dump()
    case["review_status"] = "HUMAN_REVIEWED"
    with pytest.raises(ValidationError):
        PlanningCase.model_validate(case)


def test_ledger_never_refunds_reserved_tokens_and_zero_budget_makes_no_reservation():
    ledger = PlanningLedger()
    config = PlanningConfig(max_calls=2, max_total_tokens=48_192)
    assert ledger.reserve(config) and ledger.reserve(config)
    assert not ledger.reserve(config)
    assert ledger.slots == 2 and ledger.reserved_tokens == 48_192
    assert ledger.stop == "NOT_ATTEMPTED_BUDGET"
    assert not PlanningLedger().reserve(PlanningConfig(max_calls=0))
    assert not PlanningLedger().reserve(PlanningConfig(max_total_tokens=24_095))


def test_settings_use_only_explicit_budget_and_identity():
    from agenttest.llm_planner import ModelSettings

    config = PlanningConfig()
    offline = planning_settings(config)
    assert offline.max_total_tokens == config.token_reservation == 24_096
    with pytest.raises(ValueError, match="identity"):
        planning_settings(
            config,
            ModelSettings(
                model="other-model", base_url=config.endpoint, api_key="synthetic-fixture-only"
            ),
        )


def test_exact_live_proposal_binds_code_model_budget_tasks_requests_and_review(
    tmp_path, monkeypatch
):
    from agenttest.composed_planning_protocol import (
        PlanningFreeze,
        prepare_planning,
        verify_live_proposal,
    )
    from agenttest.demo import PROJECT_ROOT
    from agenttest.eval_v2.archive import read_json

    # A synthetic clean-checkout identity tests comparisons, never authorizes a live run.
    monkeypatch.setattr(
        "agenttest.composed_planning_protocol.checkout_identity",
        lambda root: {
            "commit": "SYNTHETIC_TEST_ONLY",
            "worktree_status": "",
            "checkout_sha256": "0" * 64,
        },
    )
    suite, registry = synthetic_reviewed_suite(), tmp_path / "synthetic-review-locks"
    freeze_planning_review(suite, registry)
    lock = registry / (suite.dataset_version + ".json")
    config = PlanningConfig(source="real")
    prepare_planning(suite, config, tmp_path / "proposal", root=PROJECT_ROOT, review_lock=lock)
    path = tmp_path / "proposal/proposal.json"
    frozen = PlanningFreeze(PROJECT_ROOT, suite, config, tmp_path / "unused", review_lock=lock)
    verify_live_proposal(path, frozen)
    original = read_json(path)
    for field, key in [
        ("config", "max_calls"),
        ("git", "commit"),
        ("model_config", "model"),
        ("inputs", "policy_sha256"),
    ]:
        changed = json.loads(json.dumps(original))
        changed[field][key] = "SYNTHETIC_CHANGED_VALUE"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="differs"):
            verify_live_proposal(path, frozen)
