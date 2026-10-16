"""Complete repeated-trial denominators, independent labels and offline evidence."""

import copy
import json
from uuid import uuid4

import pytest

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.stability_check import (
    CASE_IDS,
    case_inputs,
    case_snapshot,
    check_stability,
    schedule,
    summarize,
    verify_stability,
)


def unattempted():
    return [{**slot, "run_id": None, "outcome": "NOT_ATTEMPTED", "grade": {"verified": False},
             "model_calls": 0, "total_tokens": None, "workflow_elapsed_ms": None}
            for slot in schedule()]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_four_new_tasks_are_bound_valid_and_prepare_no_label_or_secret_context(case_id):
    from agenttest.contracts import load_openapi

    snapshot = case_snapshot(case_id)
    document, target, registry, environ, _, label, _, profile, _ = case_inputs(case_id)
    context = planning_context(load_openapi(document), target, registry, label.user_task, {})
    adapter = OpenAICompatiblePlanner(ModelSettings(model="offline-stability-input",
        base_url="https://model.invalid/v1", api_key="synthetic-stability-key"), profile=profile)
    payload = json.loads(adapter.prepare_request(context))
    public = json.loads(payload["messages"][1]["content"])
    assert not {"requirements", "expected", "rubric", "goals"} & public.keys()
    assert snapshot["case_id"] == case_id
    for value in environ.values():
        if value not in {"alice", "bob"}:
            assert value not in json.dumps(payload)
    assert adapter.stats.input_token_upper_bound <= 20_000


def test_same_four_tasks_rotate_and_never_drop_failed_or_unattempted_slots():
    rows = unattempted()
    assert len(schedule()) == 12 and len({(r["round"], r["case_id"]) for r in rows}) == 12
    rows[0].update(run_id="a" * 32, outcome="PASS", grade={"verified": True},
                   model_calls=1, total_tokens=17, workflow_elapsed_ms=12.5)
    result = summarize(rows, "real")
    assert result["scheduled"] == 12 and result["attempted"] == result["successes"] == 1
    task = next(c for c in result["cases"] if c["case_id"] == rows[0]["case_id"])
    assert task["scheduled"] == 3 and task["successes"] == 1 and not task["all_three_succeeded"]
    assert result["total_tokens"] == 17 and result["real_model_calls"] == 1
    assert summarize(rows, "mock_transport")["real_model_calls"] == 0
    rows[1].update(run_id="b" * 32, outcome="TEST_INFRA_ERROR", model_calls=1)
    result = summarize(rows, "real")
    assert result["total_tokens"] is None and result["known_tokens"] == 17
    assert result["unknown_usage_calls"] == 1 and result["successes"] == 1


def test_petclinic_tasks_share_the_same_entire_approved_catalog():
    from agenttest.contracts import load_openapi

    contexts = []
    for case_id in ("F201", "F202"):
        document, target, registry, _, _, label, _, _, _ = case_inputs(case_id)
        context = planning_context(load_openapi(document), target, registry, label.user_task, {})
        context.pop("user_task")
        contexts.append(context)
    assert contexts[0] == contexts[1]
    assert len(contexts[0]["policies"]) == 3


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "reordered", "nan", "negative_tokens",
])
def test_invalid_or_incomplete_accounting_is_rejected(mutation):
    rows = unattempted()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1].update(rows[0])
    elif mutation == "reordered":
        rows.reverse()
    elif mutation == "nan":
        rows[0]["workflow_elapsed_ms"] = float("nan")
    else:
        rows[0]["total_tokens"] = -1
    with pytest.raises(ValueError):
        summarize(rows, "real")


@pytest.mark.asyncio
async def test_three_rounds_fixed_replies_archive_and_semantic_tamper_detection():
    pytest.importorskip("langgraph", reason="Stability workflow uses the agent extra")
    output = PROJECT_ROOT / "runs" / ("pytest-stability-" + uuid4().hex)
    result = await check_stability(output)
    assert result["scheduled"] == result["attempted"] == result["successes"] == 12
    assert result["real_model_calls"] == result["human_reviewed"] == 0
    assert result["model_calls"] == 12 and result["usage_kind"] == "SIMULATED"
    assert result["formal_eval_v2_score"] is None
    assert all(c["all_three_succeeded"] for c in result["cases"])
    assert verify_stability(output)["rescored_trials"] == 12
    saved = read_json(output / "results.json")
    changed = copy.deepcopy(saved)
    changed["results"][0]["grade"]["verified"] = False
    changed["summary"] = summarize(changed["results"], "mock_transport")
    key = f"R{changed['results'][0]['round']}-{changed['results'][0]['case_id']}"
    (output / (key + ".json")).write_bytes(json.dumps(changed["results"][0]).encode())
    (output / "results.json").write_bytes(json.dumps(changed).encode())
    with pytest.raises(ValueError, match="archive changed"):
        verify_stability(output)
    (output / "archive.json").write_bytes(json.dumps({
        "schema_version": "agenttest.stability.archive.v1", "files": inventory(output)}).encode())
    with pytest.raises(ValueError, match="regrading mismatch"):
        verify_stability(output)
