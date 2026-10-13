"""Offline tests: real credentials and outbound traffic are never needed."""

import importlib.util
import json

import httpx
import pytest

pytest.importorskip("langgraph", reason="Development diagnostics use optional Agent graph")

from test_eval_v2 import suite

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import (
    DevelopmentConfig,
    development_suite,
    pair_inputs,
    run_development_ab,
)
from agenttest.eval_v2.archive import verify_archive
from agenttest.eval_v2.audit import rescore
from agenttest.eval_v2.context import assert_no_labels
from agenttest.eval_v2.runner import ContextPlanner, EvalConfig, guarded_run, oracle_plan
from agenttest.llm_planner import ModelSettings
from agenttest.report import ReportStore
from agenttest.workflow import FakeLLM, WorkflowBudget


def fixtures(tasks, tmp_path, source="mock_transport"):
    """Oracle-derived test fixtures only; production driver has no oracle fallback."""
    responses = {}
    for case in tasks.cases:
        plan = oracle_plan(case)
        if source == "fake":
            responses[case.case_id] = plan.model_dump(mode="json")
            continue
        steps = []
        for step in plan.steps:
            arguments = [{"location": loc, "name": name, "value": arg.value,
                          "from_step": arg.from_step,
                          "select": arg.select if arg.from_step else None}
                         for loc in ("body", "query", "path_parameters")
                         for name, arg in getattr(step, loc).items()]
            steps.append({"id": step.id, "operation_id": step.operation_id, "role": step.role,
                          "policy_id": step.policy_id, "arguments": arguments})
        responses[case.case_id] = {
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"name": "offline fixture", "steps": steps})}}]}
    path = tmp_path / "responses.json"
    path.write_text(json.dumps(responses), encoding="utf-8")
    return path


def test_public_contexts_differ_only_by_approved_candidates():
    tasks = suite("C004", "C005")
    for case in tasks.cases:
        case.labels.limitation = "HIDDEN_LABEL_SENTINEL"
    inputs = pair_inputs(tasks, DevelopmentConfig())
    for case in tasks.cases:
        key = case.case_id
        a, b = inputs["A"][1][key], inputs["B"][1][key]
        assert_no_labels(a)
        assert_no_labels(b)
        assert "HIDDEN_LABEL_SENTINEL" not in json.dumps(b)
        assert "candidate_discovery" not in a
        assert {k: v for k, v in b.items() if k != "candidate_discovery"} == a
        assert b["candidate_discovery"]["candidates"]
        for row in b["candidate_discovery"]["candidates"]:
            assert set(row["operation_ids"]) <= {op["id"] for op in a["operations"]}
            assert set(row["policy_refs"]) <= {p["id"] for p in a["policies"]}
            assert set(row["roles"]) <= set(a["roles"])
            assert "polarity" not in row and "expected_status" not in row


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fake", "mock_transport"])
async def test_pair_provenance_selector_archival_and_rescore(tmp_path, source, monkeypatch):
    tasks = suite("C004", "C005")
    path = fixtures(tasks, tmp_path, source)
    seen = []
    original = httpx.MockTransport.handle_async_request

    async def capture(self, request):
        seen.append(json.loads(request.content))
        return await original(self, request)

    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", capture)
    result = await run_development_ab(tasks, DevelopmentConfig(source=source),
        tmp_path / "run", root=PROJECT_ROOT, responses_path=path)
    assert result["formal_independent_evaluation"] is False
    assert result["batch_ledger"]["slots"] == 4
    assert result["batch_ledger"]["reserved_tokens"] == 88_192
    assert result["batch_ledger"]["real_calls"] == 0
    assert result["batch_ledger"]["calls"] == (4 if source == "mock_transport" else 0)
    assert len(result["paired_results"]) == 2
    for variant in ("A", "B"):
        group, frozen = verify_archive(tmp_path / "run" / variant)
        assert group["review_status"] == "DEVELOPMENT_ONLY"
        assert all(r["end_to_end_success"] for r in group["results"])
        assert frozen["evaluation_config"]["development"] is True
        assert frozen["review_lock_sha256"] is None
        for row in group["results"]:
            reads = [s for s in row["plan_evidence"]["steps"] if s["operation_id"] == "getCourse"]
            assert all(s["selectors"]["path_parameters.courseId"] == "$.courseId" for s in reads)
        audit = rescore(tasks, tmp_path / "run" / variant)
        assert all(r["end_to_end_success"] for r in audit["results"])
    if seen:
        # Both the system prompt and every base user field stay identical on the actual wire.
        assert len(seen) == 4
        assert all(p["max_tokens"] == 2048 for p in seen)
        for i in (0, 1):
            assert seen[i]["messages"][0] == seen[i + 2]["messages"][0]
            a, b = [json.loads(p["messages"][1]["content"]) for p in (seen[i], seen[i + 2])]
            assert b.pop("candidate_discovery")["candidates"]
            assert a == b
    assert all(b"offline-fixture-only" not in p.read_bytes()
               for p in (tmp_path / "run").rglob("*.json"))
    report_path = next((tmp_path / "run" / "B" / "reports").glob("*/report.json"))
    report_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        verify_archive(tmp_path / "run" / "B")


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["A", "B"])
async def test_candidates_never_expand_policy_or_intent_authority(tmp_path, variant):
    tasks = suite("C004")
    inputs = pair_inputs(tasks, DevelopmentConfig())[variant]
    for invalid in ("policy", "intent", "selector"):
        plan = oracle_plan(tasks.cases[0])
        if invalid == "policy":
            plan.steps[-1].policy_id = "invented_permissive_policy"
        elif invalid == "intent":
            plan.steps[1].body["name"].value = "Different name"
        else:
            plan.steps[-1].path_parameters["courseId"].select = "$.name"
        planner = ContextPlanner(FakeLLM(plan.model_dump_json()), inputs[0]["C004"],
                                 inputs[1]["C004"])
        report = await guarded_run(tasks.cases[0].task.user_task, planner,
            ReportStore(tmp_path / (variant + invalid)), WorkflowBudget(), lambda: None)
        assert report.outcome.value != "PASS"
        assert not report.steps and not report.task_satisfied
        assert report.diagnostic is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_usage", "invalid_usage", "auth", "quota",
                                     "protocol", "truncated", "timeout", "connection"])
async def test_failure_stops_the_entire_batch_without_retry(tmp_path, failure, monkeypatch):
    import agenttest.development_ab as driver

    tasks = suite("C004", "C005")
    path = fixtures(tasks, tmp_path)
    data = json.loads(path.read_text())
    if failure == "missing_usage":
        data["C004"].pop("usage")
    elif failure == "invalid_usage":
        data["C004"]["usage"]["total_tokens"] = 999
    elif failure in {"auth", "quota"}:
        data["C004"] = {"http_status": 401 if failure == "auth" else 429, "body": {}}
    elif failure == "protocol":
        data["C004"]["choices"][0]["message"]["content"] = "not JSON"
    elif failure == "truncated":
        data["C004"]["choices"][0]["finish_reason"] = "length"
    else:
        def transport_error(_):
            error = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
            raise error("synthetic")
        monkeypatch.setattr(driver, "_mock_response", transport_error)
    path.write_text(json.dumps(data), encoding="utf-8")
    result = await run_development_ab(tasks, DevelopmentConfig(), tmp_path / "stopped",
                                     root=PROJECT_ROOT, responses_path=path)
    assert result["batch_ledger"]["calls"] == result["batch_ledger"]["slots"] == 1
    assert result["batch_ledger"]["stop"]
    assert result["comparison_status"] == "STOPPED_NOT_COMPARABLE"
    a, _ = verify_archive(tmp_path / "stopped/A")
    b, _ = verify_archive(tmp_path / "stopped/B")
    assert not a["results"][1]["attempted"]
    assert all(not r["attempted"] for r in b["results"])
    assert a["summary"]["http_requests"] == b["summary"]["http_requests"] == 0
    if failure in {"missing_usage", "invalid_usage", "auth", "quota", "timeout", "connection"}:
        assert result["batch_ledger"]["observed_tokens"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("calls,tokens,expected", [(0, 88_192, 0), (4, 22_047, 0),
                                                  (1, 88_192, 1), (4, 22_048, 1)])
async def test_global_reservation_not_reset_between_groups(tmp_path, calls, tokens, expected):
    tasks = suite("C004")
    result = await run_development_ab(tasks,
        DevelopmentConfig(max_calls=calls, max_total_tokens=tokens), tmp_path / "budget",
        root=PROJECT_ROOT, responses_path=fixtures(tasks, tmp_path))
    assert result["batch_ledger"]["calls"] == expected
    assert result["batch_ledger"]["reserved_tokens"] == expected * 22_048
    assert result["batch_ledger"]["stop"] == "NOT_ATTEMPTED_BUDGET"


@pytest.mark.asyncio
async def test_source_drift_stops_before_business_execution(tmp_path, monkeypatch):
    import agenttest.development_ab as driver

    tasks = suite("C004")
    path = fixtures(tasks, tmp_path)
    original = driver._mock_response

    def drift(fixture):
        path.write_text("{}", encoding="utf-8")
        return original(fixture)

    monkeypatch.setattr(driver, "_mock_response", drift)
    result = await run_development_ab(tasks, DevelopmentConfig(), tmp_path / "drift",
                                     root=PROJECT_ROOT, responses_path=path)
    assert result["batch_ledger"]["calls"] == 1
    assert result["batch_ledger"]["stop"] == "EVALUATION_FREEZE_CHANGED"
    a, _ = verify_archive(tmp_path / "drift/A")
    assert a["summary"]["http_requests"] == 0
    assert a["freeze_valid"] is False


def test_real_optin_identity_and_formal_gate_are_separate():
    with pytest.raises(ValueError):
        DevelopmentConfig(source="real")
    with pytest.raises(ValueError):
        EvalConfig(source="real", development=True, real_opt_in=True, max_calls=4,
                   max_total_tokens=88_192, allowed_model="x", allowed_endpoint_sha256="x")
    with pytest.raises(ValueError):
        suite("C004").require_formal()
    tasks = suite("C004")
    tasks.cases[0].labels.faults = ["response_name"]
    with pytest.raises(ValueError):
        development_suite(tasks)
    with pytest.raises(ValueError):
        DevelopmentConfig(max_calls=5)
    with pytest.raises(ValueError):
        DevelopmentConfig(max_total_tokens=88_193)


@pytest.mark.asyncio
async def test_live_identity_mismatch_cannot_construct_transport(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No real transport allowed")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", forbidden)
    with pytest.raises(ValueError, match="identity"):
        await run_development_ab(suite("C004"), DevelopmentConfig(source="real", real_opt_in=True,
            allowed_model="other", allowed_endpoint="https://fixture.invalid"),
            tmp_path / "forbidden", root=PROJECT_ROOT,
            settings=ModelSettings(model="fixture", base_url="https://fixture.invalid",
                                   api_key="synthetic-only"))
    assert not (tmp_path / "forbidden").exists()


def test_cli_prepare_and_offline_credentials_rejection(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("development_cli",
                                                 PROJECT_ROOT / "scripts/development_ab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "private_output", lambda *a: None)
    args = ["--suite", str(PROJECT_ROOT / "examples/eval-v2/c-drafts.json"),
            "--case", "C004", "--output", str(tmp_path / "prepare")]
    assert module.main(["prepare", *args]) == 0
    assert json.loads((tmp_path / "prepare/preparation.json").read_text())["real_api_calls"] == 0
    assert module.main(["prepare", *args]) == 2  # exclusive output
    assert module.main(["run", *args, "--model-env", ".env"]) == 2
