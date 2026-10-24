"""Only development requests execute here; the six frozen requests get metadata checks."""

import importlib
import json
from argparse import Namespace
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("evaluate_plan_reuse")


@pytest.mark.parametrize("version", ["plan-reuse", "plan-reuse-v2"])
def test_frozen_metadata_and_original_scoring_are_separate(harness, monkeypatch, version):
    data = ROOT / "examples" / version
    requests, labels, protocol = [json.loads((data / name).read_text(encoding="utf-8"))
        for name in ("requests.json", "labels.json", "protocol.json")]
    # CI may use shallow history; metadata tests do not download Git history.
    registry = (ROOT / "examples/plan-reuse-v2/baseline-exclusions.json").read_bytes()
    monkeypatch.setattr(harness, "git", lambda *args: registry)
    harness.validate_protocol(requests, labels, protocol)
    assert protocol["quality_denominator_per_arm"] == 15
    assert protocol["funded_project_denominator_per_arm"] == 6
    assert harness.VERSION == "trusted_testing_isolated_score_v1"
    assert harness.ATTRIBUTION_VERSION != harness.VERSION
    assert protocol["semantic_novelty_established"] is False
    label = labels[-1]
    span = label["unknown_requirement_span"]
    assert requests[-1]["user_task"][span["start"]:span["end"]] == "核验退款期限"


def test_used_requests_are_excluded_from_future_independent_holdouts():
    from agenttest.eval_v2.review import task_identity

    registry = json.loads((ROOT / "examples/eval-v2/goal-used-task-identities.json").read_text())
    excluded = {r["task_identity"] for r in registry["tasks"]}
    requests = json.loads((ROOT / "examples/plan-reuse-v2/requests.json").read_text(
        encoding="utf-8"))
    assert all(task_identity(r["user_task"]) in excluded for r in requests)


def test_v2_keeps_requests_budgets_and_diagnosis_and_adds_project_positive_coverage():
    old, new = [ROOT / "examples" / name for name in ("plan-reuse", "plan-reuse-v2")]
    assert (old / "requests.json").read_bytes() == (new / "requests.json").read_bytes()
    protocols = [json.loads((p / "protocol.json").read_text()) for p in (old, new)]
    for key in ("per_run", "per_position", "retries", "additional_attempts", "quality_targets"):
        assert protocols[0][key] == protocols[1][key]
    labels = [json.loads((p / "labels.json").read_text()) for p in (old, new)]
    for index in (0, 1, 4, 5):
        assert labels[0][index] == labels[1][index]
    for index, denominator in ((2, 19), (3, 24)):
        assert labels[1][index]["scenario_denominator"] == denominator
        assert sum(g["count"] for g in labels[1][index]["expected_groups"]) == denominator
        assert labels[1][index]["schema_variants"]


def test_unattempted_and_unconfirmed_slots_keep_denominators(harness):
    labels = [dict(id="development", scope="project")]
    frozen = dict(labels=labels, negative_denominator_per_arm=99)
    rows = [dict(arm=arm, case_id="development", attempted=False)
            for _ in range(18) for arm in ("baseline", "candidate")]
    rows[0].update(attempted=True, reason="WORKER_INFRA_ERROR_USAGE_UNCONFIRMED")
    summary = harness.summarize(rows, frozen)
    for arm in summary["arms"].values():
        assert arm["full_task_complete"] == dict(numerator=0, denominator=18)
        assert arm["approved_negative"]["denominator"] == 99
        assert not arm["eligible_full_task"]["target_met"]
    assert summary["arms"]["baseline"]["total_tokens"] is None
    assert summary["arms"]["baseline"]["model_calls"] is None


def test_paid_schedule_claim_cannot_be_reused_after_failure(tmp_path, harness):
    harness.claim_paid_schedule(tmp_path, tmp_path / "first", {"source": "development"})
    with pytest.raises(FileExistsError):
        harness.claim_paid_schedule(tmp_path, tmp_path / "second", {"source": "development"})
    record = json.loads((tmp_path / "paid-launch.json").read_text(encoding="utf-8"))
    assert record["output"].endswith("first")


@pytest.mark.parametrize("cause", ["worker", "unauthorized_executions", "wrong_evidence_adopted"])
def test_parent_stops_on_worker_or_scored_safety_evidence(harness, cause):
    row = dict(stop_paid=cause == "worker", score={cause: 1} if cause != "worker" else {})
    assert harness.stop_schedule(row)
    assert not harness.stop_schedule(dict(stop_paid=False, score={}))


def test_parent_amendment_cannot_change_product_or_worker(tmp_path, harness, monkeypatch):
    monkeypatch.setattr(harness, "source_files", lambda root: {"src/engine.py": "changed"})
    with pytest.raises(ValueError, match="PARENT_AMENDMENT_CHANGED_PRODUCT_OR_WORKER"):
        harness.freeze_parent_runner(tmp_path, dict(harness_files={"src/engine.py": "frozen"}))


def test_committed_scheduling_only_parent_is_frozen_separately(tmp_path, harness, monkeypatch):
    import hashlib

    files = {"src/engine.py": "same", "scripts/evaluate_plan_reuse.py": "new"}
    monkeypatch.setattr(harness, "source_files", lambda root: files)
    monkeypatch.setattr(harness, "git", lambda *args: b"" if args[0] == "diff" else b"commit")
    runner = harness.freeze_parent_runner(tmp_path, dict(harness_files={
        **files, "scripts/evaluate_plan_reuse.py": "old"}))
    assert runner["changed_files"] == ["scripts/evaluate_plan_reuse.py"]
    assert runner["sha256"] == hashlib.sha256((tmp_path / "evaluation-runner.py").read_bytes()
                                             ).hexdigest()
    assert runner["commit"] == "commit" and runner["source_files"] == files


def test_worker_preflight_uses_selected_source_without_model_or_http(
    tmp_path, harness, monkeypatch,
):
    import httpx
    from plan_reuse_worker import run, source_files
    from trusted_validation_support import copied_profiles

    from agenttest.eval_v2.archive import read_json

    copied_profiles(tmp_path / "projects")
    folder = tmp_path / "preflight-development"
    folder.mkdir()
    job = dict(request=dict(project="tickets", user_task="请验证创建工单接口"),
               source_files=source_files(ROOT), harness_files=source_files(ROOT))
    job_path = folder / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    async def forbidden(*args, **kwargs):
        raise AssertionError("Preflight must never send HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    result = folder / "result.json"
    run(Namespace(source=ROOT, job=job_path, folder=folder, result=result,
                  preflight=True, model_env=None))
    receipt = read_json(result)
    assert receipt["new_model_calls"] == receipt["http_requests"] == 0
    assert len(receipt["encoded"]) == 1
    assert receipt["encoded"][0]["input_token_upper_bound"] <= 20000


@pytest.mark.asyncio
async def test_development_position_accounts_for_rule_proposals(tmp_path, harness):
    from trusted_validation_support import copied_profiles

    from agenttest.eval_v2.archive import read_json
    from agenttest.project_goals import load_goal_project

    copied_profiles(tmp_path / "projects")
    folder = tmp_path / "position-development"
    folder.mkdir()
    position = importlib.import_module("evaluate_trusted_testing").position
    row = await position(folder,
        dict(project="tickets", user_task="请验证创建工单接口"), "none", None, lambda: None)
    project = load_goal_project(tmp_path / "projects/tickets/registered.project.json")
    product, reports = harness.recount(folder, row, project)
    result = harness.supplementary(product, reports, read_json(folder / "plans.json"))
    assert result["model_plan_calls"] == result["legal_model_proposals"] == 1
    assert result["derived_cases"] == result["derived_executed"] == 9
    assert result["missing_plan_evidence"] == result["reused_execution_state"] == 0


@pytest.mark.asyncio
async def test_mature_tool_fixtures_use_same_approved_subset_and_request_budget(tmp_path, harness):
    from trusted_validation_support import copied_profiles

    from agenttest.evaluation import save_evaluation

    tools = importlib.import_module("compare_plan_reuse_tools")
    copied_profiles(tmp_path / "projects")
    save_evaluation([dict(id=f"DEVELOPMENT_{i}", project=project)
        for i, project in enumerate(("tickets", "courses", "laboratory", "tickets"))],
        tmp_path / "public-requests.json")
    result = await tools.compare(tmp_path, tmp_path / "comparison", with_generator=False, rounds=1)
    assert len(result["rows"]) == 4
    for row in result["rows"]:
        assert row["arm"] == "fixed_pytest"
        assert row["actual_requests"] == row["request_budget"]
        assert row["business_passed"] == row["schema_passed"] == row["common_denominator"]
    assert result["real_model_calls"] == 0 and not result["labels_or_fault_implementation_read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["single", "flow", "project"])
async def test_three_scope_demo_exports_data_only_and_labels_simulation(tmp_path, harness, scope):
    module = importlib.import_module("demo_plan_reuse")
    result = await module.demo(tmp_path, scope)
    assert result["result"]["complete"]
    assert result["source"] == "SIMULATED" and result["real_model_calls"] == 0
    assert result["human_time_seconds"] is None and not result["independent_human_review"]
    assert len(result["exports"]) == (2 if scope == "single" else 0)
