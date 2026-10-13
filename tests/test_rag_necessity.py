"""Development retrieval evidence cannot acquire execution authority."""

import copy
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from agenttest.batch_demo import DEFAULT_BATCH_TASK, manual_batch_draft, run_batch_demo
from agenttest.dsl import ArgumentBinding
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import Outcome, ReportStore

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments/rag_necessity"
SPEC = importlib.util.spec_from_file_location("rag_experiment", EXPERIMENT / "evaluate.py")
rag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rag)


def corpus():
    return rag.read(EXPERIMENT / "corpus.json")


def test_frozen_sources_and_relevance_are_traceable():
    data = corpus()
    protocol = rag.read(EXPERIMENT / "protocol.json")
    assert set(protocol["files"]) == set(rag.FROZEN_FILES)
    # Historical experiments must not force future production edits to rewrite old scores.
    for name in ("corpus.json", "suite.json"):
        assert rag.digest((EXPERIMENT / name).read_bytes()) == protocol["files"][
            "experiments/rag_necessity/" + name]
    assert rag.verify_citations(data) == 27
    ids = {doc["id"] for doc in data["documents"]}
    suite = rag.read(EXPERIMENT / "suite.json")
    assert len(suite["cases"]) == 12
    assert suite["classification"] == "DEVELOPMENT_ONLY"
    assert suite["review_status"] == "UNREVIEWED" and not suite["independent_holdout"]
    assert all(set(case["relevant"]) <= ids for case in suite["cases"])


def test_source_drift_stops_experiment(tmp_path, monkeypatch):
    source = tmp_path / "input.txt"
    source.write_bytes(b"frozen")
    monkeypatch.setattr(rag, "ROOT", tmp_path)
    monkeypatch.setattr(rag, "FROZEN_FILES", ["input.txt"])
    protocol = {"files": {"input.txt": rag.digest(b"frozen")}}
    rag.verify_freeze(protocol)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Frozen experiment/source changed"):
        rag.verify_freeze(protocol)


@pytest.mark.parametrize("field", ["text", "source_version", "start", "sha256", "visibility"])
def test_forged_citation_is_rejected(field):
    data = corpus()
    doc = data["documents"][0]
    doc[field] = doc[field] + 1 if field == "start" else "forged"
    with pytest.raises(ValueError, match="Citation mismatch"):
        rag.verify_citations(data)


def test_source_snapshot_mutation_is_rejected():
    data = corpus()
    next(iter(data["sources"].values()))["text"] += "forged"
    with pytest.raises(ValueError, match="snapshot digest"):
        rag.verify_citations(data)


def test_stale_private_cross_system_filters_and_conflict_limit():
    data = corpus()
    case = {"system": "courseinsight"}
    old = next(d for d in data["documents"] if d["id"] == "ci.progress.old")
    query = "AnalysisBatchStatus resolveStatus total success failed COMPLETED PARTIAL_FAILED"
    ranked = rag.BM25(data["documents"]).rank(query)
    assert old["id"] in [d["id"] for d, _ in ranked[:5]]
    assert not rag.eligible(old, case, data["revisions"])
    for doc in data["documents"]:
        if doc["system"] == "mini" or doc["visibility"] == "PRIVATE":
            assert not rag.eligible(doc, case, data["revisions"])
    conflict = next(d for d in data["documents"] if d["id"] == "negative.conflicting-current")
    assert rag.eligible(conflict, case, data["revisions"])
    # Honest limitation: provenance filters cannot establish semantic correctness.


def test_ranking_never_uses_gold_metadata():
    data = corpus()
    documents = copy.deepcopy(data["documents"])
    before = [d["id"] for d, _ in rag.BM25(documents).rank("CSV 表头")]
    for document in documents:
        document["answer"] = "CSV 表头 content rating " * 100
        document["relevant"] = True
        document["kind"] = "magic-gold"
    after = [d["id"] for d, _ in rag.BM25(documents).rank("CSV 表头")]
    assert before == after


def test_notes_keep_complete_a_input_and_exact_schema_prompt():
    data = corpus()
    case = {"system": "mini"}
    base, profile = rag.baseline("mini", DEFAULT_BATCH_TASK)
    before = copy.deepcopy(base)
    hits = rag.BM25(data["documents"]).rank("批次 CSV", lambda d: rag.eligible(
        d, case, data["revisions"]))[:5]
    augmented, omitted = rag.with_notes(base, hits)
    assert base == before
    assert {k: v for k, v in augmented.items() if k != "retrieval_notes"} == before
    a, b = [json.loads(rag.request(ctx, profile)) for ctx in (base, augmented)]
    assert a["messages"][0] == b["messages"][0]
    assert a["response_format"] == b["response_format"]
    assert a["max_completion_tokens"] == b["max_completion_tokens"]
    assert "PRIVATE" not in json.dumps(augmented)
    assert len(rag.encoded(augmented["retrieval_notes"])) <= 12000
    small, omitted = rag.with_notes(base, hits, max_bytes=2)
    assert small["retrieval_notes"] == [] and len(omitted) == len(hits)


@pytest.mark.parametrize("text", ["token=synthetic_secret", "https://unapproved.invalid"])
def test_sensitive_literals_cannot_be_added(text):
    doc = copy.deepcopy(corpus()["documents"][0])
    doc["text"] = text
    with pytest.raises(ValueError, match="sensitive"):
        rag.with_notes({}, [(doc, 1.0)])


def test_original_namespace_cannot_be_overwritten():
    with pytest.raises(ValueError, match="namespace collision"):
        rag.with_notes({"retrieval_notes": []}, [])


class NotesPlanner:
    """Only adds quotes before forwarding to a Fake/Mock planner; cannot access Executor."""

    def __init__(self, planner):
        self.planner = planner
        self.base = self.augmented = None

    async def generate(self, context):
        self.base = copy.deepcopy(context)
        data = corpus()
        doc = copy.deepcopy(next(d for d in data["documents"]
                                 if d["id"] == "negative.conflicting-current"))
        # Deliberate same-system contamination, not claimed as a real mini business document.
        doc["system"], doc["target_revision"] = "mini", data["revisions"]["mini"]
        self.augmented, _ = rag.with_notes(context, [(doc, 1.0)])
        return await self.planner.generate(self.augmented)


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["role", "operation", "assertion", "resource", "csv"])
async def test_retrieved_instructions_cannot_authorize_dsl(tmp_path, attack):
    pytest.importorskip("langgraph")
    from agenttest.workflow import FakeLLM

    plan = manual_batch_draft().scenario()
    raw = plan.model_dump()
    if attack == "role":
        raw["steps"][2]["role"] = "admin"
    elif attack == "operation":
        raw["steps"][2]["operation_id"] = "retryFailed"
    elif attack == "assertion":
        raw["steps"][3]["assertions"] = {
            "fields": [{"select": "$.state", "operator": "equals", "expected": "FAILED"}]}
    elif attack == "resource":
        raw["steps"][2]["path_parameters"]["courseId"] = ArgumentBinding(value=999).model_dump()
    else:
        raw["steps"][2]["upload"]["content"] += "third synthetic row\n"
    planner = NotesPlanner(FakeLLM(json.dumps(raw)))
    report = await run_batch_demo(DEFAULT_BATCH_TASK, planner, ReportStore(tmp_path))
    assert report.outcome in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}
    assert report.tool_calls == 0 and report.steps == [] and not report.task_satisfied
    assert {k: v for k, v in planner.augmented.items() if k != "retrieval_notes"} == planner.base


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,outcome", [
    ("none", Outcome.PASS), ("terminal", Outcome.ASSERTION_FAILED),
    ("result_count", Outcome.ASSERTION_FAILED),
])
async def test_mock_planner_notes_do_not_change_business_results(tmp_path, fault, outcome):
    pytest.importorskip("langgraph")
    draft = manual_batch_draft().model_dump_json()

    def handler(req):
        data = json.loads(req.content)
        notes = json.loads(data["messages"][1]["content"])["retrieval_notes"]
        assert notes[0]["authority"] == "UNTRUSTED_PLANNING_REFERENCE_ONLY"
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": draft}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })

    mock = OpenAICompatiblePlanner(ModelSettings(model="offline-fixture",
        base_url="https://model.invalid/v1", api_key="synthetic-key"),
        httpx.MockTransport(handler), profile="mini_batch")
    planner = NotesPlanner(mock)
    report = await run_batch_demo(DEFAULT_BATCH_TASK, planner, ReportStore(tmp_path), fault=fault)
    assert mock.stats.calls == 1 and report.outcome == outcome
    assert report.workflow_completed and report.intent_verified
    assert report.task_satisfied == (fault == "none")
    if fault == "terminal":
        assert len(report.steps) == 4
    if fault == "result_count":
        assert any(c.kind == "count" and not c.passed for c in report.steps[-1].checks)
