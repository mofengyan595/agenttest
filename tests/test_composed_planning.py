"""Offline planner attempts, budget exhaustion, failure archives and strict live gates."""

import asyncio
import json

import httpx
import pytest

pytest.importorskip("langgraph", reason="Planning measurements use the existing agent graph")

from agenttest.composed_check import SUITE_PATH
from agenttest.composed_planning import (
    report_digest,
    run_planning,
    verify_planning,
    write_planning_fixtures,
)
from agenttest.composed_planning_protocol import PlanningConfig, PlanningFreeze, prepare_planning
from agenttest.composed_review import PlanningSuite
from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.report import ModelCallStats, Outcome, RunReport


def selected(*ids):
    suite = PlanningSuite.load(SUITE_PATH)
    return PlanningSuite.model_validate(
        {
            **suite.model_dump(),
            "cases": [next(c.model_dump() for c in suite.cases if c.case_id == key) for key in ids],
        }
    )


def explicit_fixtures(suite, source, path):
    write_planning_fixtures(suite, source, path)
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fake", "mock_transport"])
async def test_actual_sources_full_catalog_no_oracle_fallback_and_rescore(
    tmp_path, source, monkeypatch
):
    suite = selected("P001", "P005", "P009")
    path = explicit_fixtures(suite, source, tmp_path / "fixtures.json")
    requests, original = [], httpx.MockTransport.handle_async_request

    async def capture(self, request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        assert (
            not {"expected", "label_provenance", "review_record", "rejection_code"} & context.keys()
        )
        assert len(context["operations"]) == 6 and len(context["policies"]) == 7
        requests.append(request)
        return await original(self, request)

    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", capture)
    monkeypatch.setattr(
        "agenttest.composed_planning.reference_draft",
        lambda *a: pytest.fail("Evaluation must not generate an oracle response"),
    )
    output = tmp_path / "run"
    result = await run_planning(
        suite, PlanningConfig(source=source), output, root=PROJECT_ROOT, responses_path=path
    )
    assert result["supported"] == result["supported_success"] == 2
    assert result["planner_safe_rejections"] == result["gate_safe_rejections"] == 0
    assert result["task_preflight_rejections"] == 1 and result["unprocessed"] == 0
    assert result["real_model_calls"] == result["human_reviewed"] == 0
    assert result["formal_eval_v2_score"] is None
    assert result["model_calls"] == len(requests) == (2 if source == "mock_transport" else 0)
    assert result["results"][-1]["reserved"] is False
    assert result["usage_kind"] == ("SIMULATED" if source == "mock_transport" else "NOT_APPLICABLE")
    assert verify_planning(output)["rescored_cases"] == 3
    assert all(
        b"offline-planning-fixture-only" not in p.read_bytes() for p in output.rglob("*.json")
    )


@pytest.mark.asyncio
async def test_task_preflight_does_not_read_an_unrelated_fixture_or_claim_model_rejection(tmp_path):
    suite = selected("P005", "P009")
    path = explicit_fixtures(suite, "mock_transport", tmp_path / "fixtures.json")
    data = read_json(path)
    data["P009"] = data["P005"]
    path.write_text(json.dumps(data), encoding="utf-8")
    result = await run_planning(
        suite, PlanningConfig(), tmp_path / "run", root=PROJECT_ROOT, responses_path=path
    )
    assert result["planner_safe_rejections"] == result["gate_safe_rejections"] == 0
    assert result["task_preflight_rejections"] == 1 and result["model_calls"] == 1
    assert result["results"][1]["grade"]["requests"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "max_calls,tokens,attempted", [(0, 192_768, 0), (1, 192_768, 1), (8, 24_095, 0), (8, 24_096, 1)]
)
async def test_full_denominator_and_no_calls_after_budget_exhaustion(
    tmp_path, max_calls, tokens, attempted
):
    suite = selected("P001", "P005", "P009")
    path = explicit_fixtures(suite, "mock_transport", tmp_path / "fixtures.json")
    result = await run_planning(
        suite,
        PlanningConfig(max_calls=max_calls, max_total_tokens=tokens),
        tmp_path / "run",
        root=PROJECT_ROOT,
        responses_path=path,
    )
    assert result["requested"] == 3 and result["attempted"] == result["model_calls"] == attempted
    assert result["not_attempted"] == 3 - attempted
    assert result["ledger"]["reserved_tokens"] == attempted * 24_096
    assert result["ledger"]["stop"] == "NOT_ATTEMPTED_BUDGET"
    assert all(r["classification"] == "NOT_ATTEMPTED_BUDGET"
               for r in result["results"][attempted:-1])
    assert result["results"][-1]["classification"] == "TASK_PREFLIGHT_REJECTED"
    assert result["task_preflight_rejections"] == 1 and result["unprocessed"] == 2 - attempted
    assert result["results"][-1]["reserved"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["invalid_json", "http_429", "unknown_usage", "wrong_role"])
async def test_failures_are_archived_without_retry_or_assertion_repair(tmp_path, fault):
    suite = selected("P001", "P005")
    path = explicit_fixtures(suite, "mock_transport", tmp_path / "fixtures.json")
    data = read_json(path)
    if fault == "invalid_json":
        data["P001"]["choices"][0]["message"]["content"] = "{invalid"
    elif fault == "http_429":
        data["P001"] = {"http_status": 429, "body": {"private": "never persist provider body"}}
    elif fault == "unknown_usage":
        data["P001"].pop("usage")
    else:
        draft = json.loads(data["P001"]["choices"][0]["message"]["content"])
        draft["steps"][-1]["role"] = "bob"
        data["P001"]["choices"][0]["message"]["content"] = json.dumps(draft)
    path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "run"
    result = await run_planning(
        suite, PlanningConfig(), output, root=PROJECT_ROOT, responses_path=path
    )
    row = result["results"][0]
    assert not row["grade"]["end_to_end_success"] and row["grade"]["requests"] == 0
    assert row["model_calls"] == 1 and row["run_id"]
    assert result["model_calls"] == (2 if fault == "wrong_role" else 1)
    assert len(result["results"]) == 2
    assert verify_planning(output)["archive_verified"]
    assert all(b"never persist provider body" not in p.read_bytes() for p in output.rglob("*.json"))


@pytest.mark.asyncio
async def test_missing_fixtures_and_real_without_review_fail_before_transport(
    tmp_path, monkeypatch
):
    suite = selected("P001")
    monkeypatch.setattr(
        httpx,
        "AsyncHTTPTransport",
        lambda **kw: pytest.fail("Unapproved real transport construction"),
    )
    with pytest.raises(ValueError, match="fixtures"):
        await run_planning(suite, PlanningConfig(), tmp_path / "missing", root=PROJECT_ROOT)
    with pytest.raises(ValueError, match="opt-in"):
        await run_planning(
            suite, PlanningConfig(source="real"), tmp_path / "real", root=PROJECT_ROOT
        )
    with pytest.raises(ValueError, match="review lock"):
        await run_planning(
            suite,
            PlanningConfig(source="real", real_opt_in=True),
            PROJECT_ROOT / "runs/composed-planning-unapproved-never-created",
            root=PROJECT_ROOT,
            settings=__import__(
                "agenttest.composed_planning_protocol", fromlist=["planning_settings"]
            ).planning_settings(PlanningConfig()),
        )


@pytest.mark.asyncio
async def test_freeze_drift_blocks_model_and_business_requests(tmp_path, monkeypatch):
    suite = selected("P001", "P005")
    path = explicit_fixtures(suite, "mock_transport", tmp_path / "fixtures.json")
    original, count = PlanningFreeze.verify, 0

    def changed(self):
        nonlocal count
        count += 1
        if count >= 2:
            raise ValueError("PLANNING_FREEZE_CHANGED")
        return original(self)

    monkeypatch.setattr(PlanningFreeze, "verify", changed)
    result = await run_planning(
        suite, PlanningConfig(), tmp_path / "run", root=PROJECT_ROOT, responses_path=path
    )
    assert result["model_calls"] == 0 and result["ledger"]["slots"] == 1
    assert result["ledger"]["stop"] == "PLANNING_FREEZE_CHANGED"
    assert not result["results"][0]["grade"]["requests"]


@pytest.mark.asyncio
async def test_cancel_preserves_reservation_and_report_without_sealing_success(
    tmp_path, monkeypatch
):
    suite = selected("P001")
    path = explicit_fixtures(suite, "mock_transport", tmp_path / "fixtures.json")
    started = asyncio.Event()

    async def wait(self, request):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", wait)
    output = tmp_path / "run"
    task = asyncio.create_task(
        run_planning(suite, PlanningConfig(), output, root=PROJECT_ROOT, responses_path=path)
    )
    await asyncio.wait_for(started.wait(), 20)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert read_json(output / "abort.json")["current_model_calls"] == 1
    assert (output / "P001.reservation.json").exists()
    assert not (output / "archive.json").exists() and not (output / "results.json").exists()
    report = read_json(next((output / "reports").glob("*/report.json")))
    assert report["outcome"] == Outcome.TEST_INFRA_ERROR and not report["workflow_completed"]


@pytest.mark.asyncio
async def test_archive_rescore_detects_changed_grade_even_after_resealing(tmp_path):
    suite = selected("P001")
    path = explicit_fixtures(suite, "mock_transport", tmp_path / "fixtures.json")
    output = tmp_path / "run"
    await run_planning(suite, PlanningConfig(), output, root=PROJECT_ROOT, responses_path=path)
    row = read_json(output / "P001.json")
    row["classification"] = "ASSERTION_FAILED"
    (output / "P001.json").write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        verify_planning(output)
    manifest = read_json(output / "archive.json")
    manifest["files"] = inventory(output)
    (output / "archive.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="rescore"):
        verify_planning(output)


def test_prepare_is_keyless_and_never_authorizes_paid_calls(tmp_path):
    suite = selected("P001", "P005", "P009")
    result = prepare_planning(
        suite, PlanningConfig(source="real"), tmp_path / "proposal", root=PROJECT_ROOT
    )
    assert result["real_model_calls"] == 0 and not result["operator_authorized"]
    assert not result["ready_for_paid_run"] and result["review_lock_sha256"] is None
    assert result["ceiling"]["model_calls"] == 8
    assert result["ceiling"]["reserved_tokens"] == 192_768


def test_uninvoked_model_defaults_keep_stable_report_digest_after_json_roundtrip():
    report = RunReport(
        scenario_sha256="0" * 64,
        contract_sha256="0" * 64,
        environment="isolated",
        target_origin="http://127.0.0.1:9001",
        model_call=ModelCallStats(),
    )
    parsed = RunReport.model_validate_json(report.model_dump_json())
    assert report_digest(report) == report_digest(parsed)


@pytest.mark.asyncio
async def test_contract_preflight_failure_is_saved_without_model_or_business_calls(
    tmp_path, monkeypatch
):
    suite = selected("P001", "P005")
    path = explicit_fixtures(suite, "mock_transport", tmp_path / "fixtures.json")

    def unavailable(*args, **kwargs):
        raise ValueError("synthetic contract-loader failure")

    monkeypatch.setattr("agenttest.workflow.load_openapi", unavailable)
    output = tmp_path / "run"
    result = await run_planning(
        suite, PlanningConfig(), output, root=PROJECT_ROOT, responses_path=path
    )
    assert result["model_calls"] == result["supported_success"] == 0
    assert result["ledger"]["stop"] == "PLANNING_PREFLIGHT_FAILED"
    assert result["results"][0]["run_id"]
    assert verify_planning(output)["archive_verified"]
