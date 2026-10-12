"""Offline integration through the unchanged graph, adapter and trusted Executor."""

import importlib.util
import json

import pytest

pytest.importorskip("langgraph", reason="Eval runner uses optional Agent graph")

from test_eval_v2 import DATA, suite
from test_eval_v2_review import reviewed_fixture

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.audit import rescore
from agenttest.eval_v2.comparison import compare
from agenttest.eval_v2.review import freeze_review
from agenttest.eval_v2.runner import EvalConfig, evaluate
from agenttest.executor import Executor
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import fingerprint


@pytest.mark.asyncio
async def test_manual_support_rejection_and_future_are_distinct(tmp_path):
    result = await evaluate(
        suite("C004", "C022", "C019"),
        EvalConfig(development=True),
        tmp_path / "manual",
        root=PROJECT_ROOT,
    )
    summary = result["summary"]
    assert summary["requested"] == summary["eligible"] == 3
    assert summary["attempted"] == 2 and summary["executed"] == 1
    assert summary["end_to_end_success"]["rate"] == summary["safe_rejection"]["rate"] == 1
    assert summary["real_api_calls"] == summary["model_calls"] == 0
    assert summary["total_tokens"] is None
    assert result["results"][-1]["classification"] == "SAFE_REJECTION"
    assert result["review_status"] == "DEVELOPMENT_ONLY"
    assert (tmp_path / "manual/summary.md").exists()
    rescored = rescore(suite("C004", "C022", "C019"), tmp_path / "manual")
    assert rescored["model_calls"] == rescored["business_http_requests"] == 0
    assert rescored["results"][0]["end_to_end_success"]
    with pytest.raises(FileExistsError):
        await evaluate(
            suite("C004"), EvalConfig(development=True), tmp_path / "manual", root=PROJECT_ROOT
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fake", "mock_transport"])
@pytest.mark.parametrize("variant", ["A", "B"])
async def test_offline_paths_actual_provenance_and_context_size(tmp_path, source, variant):
    responses = (
        PROJECT_ROOT / f"examples/eval-v2/{'fake' if source == 'fake' else 'mock'}-responses.json"
    )
    result = await evaluate(
        suite("C004", "C005"),
        EvalConfig(development=True, source=source, variant=variant),
        tmp_path / (source + variant),
        root=PROJECT_ROOT,
        responses_path=responses,
    )
    summary = result["summary"]
    assert summary["plan_validity"]["rate"] == 1
    assert summary["intent_satisfaction"]["rate"] == 0.5
    assert summary["end_to_end_success"]["rate"] == 0.5
    assert summary["real_api_calls"] == 0
    assert summary["model_calls"] == (2 if source == "mock_transport" else 0)
    assert summary["total_tokens"] == (400 if source == "mock_transport" else None)
    assert all(r["source"] == source for r in result["results"])
    assert result["results"][1]["classification"] == "INTENT_MISMATCH"
    assert result["results"][1]["http_requests"] == 0


@pytest.mark.asyncio
async def test_formal_unreviewed_and_budget_stop_never_invoke_executor(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("No execution authorized")

    monkeypatch.setattr(Executor, "run", forbidden)
    with pytest.raises(ValueError, match="human-reviewed"):
        await evaluate(suite("C004"), EvalConfig(), tmp_path / "formal", root=PROJECT_ROOT)
    assert not (tmp_path / "formal").exists()
    result = await evaluate(
        suite("C004", "C005"),
        EvalConfig(development=True, max_cases_attempted=0),
        tmp_path / "budget",
        root=PROJECT_ROOT,
    )
    assert result["summary"]["attempted"] == result["summary"]["executed"] == 0
    assert result["summary"]["plan_validity"]["denominator"] == 2
    assert result["summary"]["plan_validity"]["rate"] == 0
    assert result["summary"]["conditional_plan_validity"]["rate"] is None


@pytest.mark.asyncio
async def test_unknown_mock_usage_stops_remaining_cases(tmp_path):
    path = tmp_path / "missing-usage.json"
    path.write_text(json.dumps({"default": {"choices": []}}), encoding="utf-8")
    result = await evaluate(
        suite("C004", "C005"),
        EvalConfig(development=True, source="mock_transport"),
        tmp_path / "unknown",
        root=PROJECT_ROOT,
        responses_path=path,
    )
    assert result["summary"]["model_calls"] == 1
    assert result["summary"]["total_tokens"] is None
    assert result["stop_reason"] == "MODEL_USAGE_UNAVAILABLE"
    assert result["results"][1]["classification"] == "MODEL_USAGE_UNAVAILABLE"
    assert result["summary"]["plan_validity"]["denominator"] == 2


@pytest.mark.asyncio
async def test_scheduled_faults_have_fresh_normal_controls(tmp_path):
    result = await evaluate(
        suite("C016"), EvalConfig(development=True), tmp_path / "faults", root=PROJECT_ROOT
    )
    assert result["fault_detection"]["scheduled_mechanisms"] == 3
    assert result["fault_detection"]["coverage"]["rate"] == 1
    assert all(p["normal"]["outcome"] == "PASS" for p in result["pairs"])
    assert len({r["run_id"] for p in result["pairs"] for r in (p["normal"], p["fault"])}) == 6


@pytest.mark.asyncio
async def test_real_label_cannot_bypass_review_or_dirty_freeze(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Paid adapter must never be called")

    monkeypatch.setattr(OpenAICompatiblePlanner, "generate", forbidden)
    settings = ModelSettings(model="fixture", base_url="https://fixture.invalid", api_key="private")
    config = EvalConfig(
        source="real",
        real_opt_in=True,
        max_calls=1,
        max_total_tokens=1,
        allowed_model="fixture",
        allowed_endpoint_sha256=fingerprint(settings.base_url),
    )
    with pytest.raises(ValueError, match="human-reviewed"):
        await evaluate(
            suite("C004"), config, tmp_path / "real", root=PROJECT_ROOT, settings=settings
        )
    tasks = reviewed_fixture("C004")
    freeze_review(tasks, tmp_path / "reviews")
    monkeypatch.setattr(
        "agenttest.eval_v2.freeze.checkout_identity",
        lambda root: {
            "commit": "synthetic",
            "worktree_status": " M fixture.py",
            "checkout_sha256": "dirty",
        },
    )
    with pytest.raises(ValueError, match="clean committed"):
        await evaluate(tasks, config, tmp_path / "dirty", root=PROJECT_ROOT, settings=settings,
                       review_lock_path=tmp_path / "reviews" / (tasks.dataset_version + ".json"))


def test_cli_validation_and_explicit_real_opt_in(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location(
        "evaluate_v2_cli", PROJECT_ROOT / "scripts/evaluate_v2.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Validation does not load a model or create an event loop.
    import sys

    original = sys.argv
    try:
        sys.argv = ["evaluate_v2.py", "validate", "--suite", str(DATA)]
        assert module.main() == 0
        assert json.loads(capsys.readouterr().out)["human_reviewed"] == 0
        sys.argv = ["evaluate_v2.py", "run", "--source", "real", "--output", str(tmp_path / "x")]
        assert module.main() == 2
    finally:
        sys.argv = original


@pytest.mark.asyncio
async def test_ab_comparison_checks_frozen_pair_identity(tmp_path):
    responses = PROJECT_ROOT / "examples/eval-v2/mock-responses.json"
    for variant in ("A", "B"):
        await evaluate(
            suite("C004"),
            EvalConfig(source="mock_transport", development=True, variant=variant),
            tmp_path / variant,
            root=PROJECT_ROOT,
            responses_path=responses,
        )
    result = compare(tmp_path / "A", tmp_path / "B")
    assert result["source"] == "mock_transport"
    assert result["statistical_significance"] == "NOT_ESTABLISHED"
    pair = result["paired_results"][0]
    assert pair["B"]["context_bytes"] > pair["A"]["context_bytes"]
    assert pair["A"]["real_api_calls"] == pair["B"]["real_api_calls"] == 0
    path = tmp_path / "B/freeze.json"
    value = json.loads(path.read_text())
    value["labels_sha256"] = "changed-labels"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        compare(tmp_path / "A", tmp_path / "B")


@pytest.mark.asyncio
async def test_live_budget_insufficient_never_constructs_transport(tmp_path, monkeypatch):
    tasks = reviewed_fixture("C004", "C005")
    freeze_review(tasks, tmp_path / "reviews")
    monkeypatch.setattr(
        "agenttest.eval_v2.freeze.checkout_identity",
        lambda root: {"commit": "synthetic", "worktree_status": "", "checkout_sha256": "fixed"},
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Budget must be reserved before creating a network transport")

    monkeypatch.setattr("httpx.AsyncHTTPTransport", forbidden)
    settings = ModelSettings(model="fixture", base_url="https://fixture.invalid", api_key="private")
    config = EvalConfig(
        source="real",
        real_opt_in=True,
        max_calls=1,
        max_total_tokens=1,
        allowed_model="fixture",
        allowed_endpoint_sha256=fingerprint(settings.base_url),
    )
    result = await evaluate(
        tasks, config, tmp_path / "budget", root=PROJECT_ROOT, settings=settings,
        review_lock_path=tmp_path / "reviews" / (tasks.dataset_version + ".json")
    )
    assert result["summary"]["real_api_calls"] == result["summary"]["attempted"] == 0
    assert result["summary"]["plan_validity"]["denominator"] == 2
    assert all(r["classification"] == "NOT_ATTEMPTED_BUDGET" for r in result["results"])


@pytest.mark.asyncio
async def test_partial_budget_keeps_scheduled_and_conditional_denominators(tmp_path):
    result = await evaluate(
        suite("C004", "C005", "C022"),
        EvalConfig(development=True, max_cases_attempted=1),
        tmp_path / "partial", root=PROJECT_ROOT,
    )
    summary = result["summary"]
    assert summary["requested"] == 3 and summary["attempted"] == summary["executed"] == 1
    assert summary["end_to_end_success"]["denominator"] == 2
    assert summary["end_to_end_success"]["rate"] == 0.5
    assert summary["conditional_end_to_end_success"]["rate"] == 1
    assert summary["safe_rejection"]["denominator"] == 1
    assert summary["safe_rejection"]["rate"] == 0


@pytest.mark.asyncio
async def test_execution_budget_interrupt_is_not_a_success(tmp_path):
    result = await evaluate(
        suite("C004"), EvalConfig(development=True, max_tool_calls=0),
        tmp_path / "interrupted", root=PROJECT_ROOT,
    )
    summary = result["summary"]
    assert summary["plan_validity"]["rate"] == 1
    assert summary["intent_satisfaction"]["rate"] == 1
    assert summary["end_to_end_success"]["rate"] == 0
    assert result["results"][0]["classification"] == "TEST_INFRA_ERROR"
    assert "TOOL_CALL_LIMIT" in result["results"][0]["diagnostics"]


@pytest.mark.asyncio
async def test_freeze_change_after_original_prevents_all_paired_requests(tmp_path, monkeypatch):
    from agenttest import llm_planner
    from agenttest.eval_v2 import runner

    original, calls = runner.guarded_run, []

    async def changed(*args, **kwargs):
        calls.append(kwargs.get("fault", "none"))
        report = await original(*args, **kwargs)
        monkeypatch.setattr(llm_planner, "SYSTEM_PROMPT", "Changed before paired controls")
        return report

    monkeypatch.setattr(runner, "guarded_run", changed)
    result = await evaluate(
        suite("C016"), EvalConfig(development=True), tmp_path / "drift", root=PROJECT_ROOT
    )
    assert calls == ["none"]
    assert not result["freeze_valid"] and result["stop_reason"] == "EVALUATION_CONFIGURATION_ERROR"
    assert result["fault_detection"]["not_executed"] == 3
    assert result["paired_http_requests"] == 0


@pytest.mark.asyncio
async def test_injected_mock_cannot_claim_real_transport(tmp_path, monkeypatch):
    import httpx

    tasks = reviewed_fixture()
    freeze_review(tasks, tmp_path / "reviews")
    monkeypatch.setattr(
        "agenttest.eval_v2.freeze.checkout_identity",
        lambda root: {"commit": "a" * 40, "worktree_status": "", "checkout_sha256": "fixed"},
    )
    monkeypatch.setattr("httpx.AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(
        lambda request: pytest.fail("Provenance mismatch must prevent any model call")
    ))
    settings = ModelSettings(model="synthetic", base_url="https://mock.invalid", api_key="private")
    config = EvalConfig(
        source="real", real_opt_in=True, max_calls=1, max_total_tokens=25000,
        allowed_model="synthetic", allowed_endpoint_sha256=fingerprint(settings.base_url),
    )
    with pytest.raises(ValueError, match="provenance mismatch"):
        await evaluate(
            tasks, config, tmp_path / "injected", root=PROJECT_ROOT, settings=settings,
            review_lock_path=tmp_path / "reviews" / (tasks.dataset_version + ".json"),
        )


@pytest.mark.asyncio
async def test_hidden_labels_do_not_enter_adapter_http_payload(monkeypatch):
    import httpx

    from agenttest.eval_v2.runner import prepare_inputs

    tasks, payloads = suite("C004"), []
    response = json.loads((PROJECT_ROOT / "examples/eval-v2/mock-responses.json").read_bytes())

    def capture(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=response["default"])

    settings = ModelSettings(
        model="synthetic", base_url="https://mock.invalid", api_key="offline-only"
    )
    for iteration in range(2):
        if iteration:
            tasks.cases[0].labels.rubric.steps[1].arguments[0].value = "HIDDEN_SENTINEL"
            tasks.cases[0].labels.faults = ["response_name"]
        context = prepare_inputs(tasks, EvalConfig(variant="B"))[1]["C004"]
        await OpenAICompatiblePlanner(settings, httpx.MockTransport(capture)).generate(context)
    assert payloads[0] == payloads[1]
    assert "HIDDEN_SENTINEL" not in json.dumps(payloads)
    assert "labels" not in payloads[0]["messages"][1]["content"]
