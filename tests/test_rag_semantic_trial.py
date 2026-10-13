"""Offline boundary tests use synthetic vectors, never claim semantic retrieval quality."""

import copy
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "experiments/rag_semantic_trial"


def module(name):
    spec = importlib.util.spec_from_file_location("semantic_" + name, HERE / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


trial, wordpiece = module("trial"), module("wordpiece")


def tokenizer():
    definition = {
        "normalizer": {"type": "BertNormalizer", "clean_text": True,
                       "handle_chinese_chars": True, "strip_accents": None, "lowercase": False},
        "pre_tokenizer": {"type": "BertPreTokenizer"},
        "model": {"type": "WordPiece", "unk_token": "[UNK]",
                  "continuing_subword_prefix": "##", "max_input_chars_per_word": 100,
                  "vocab": {**wordpiece.SPECIAL, "课": 200, "程": 201,
                            "API": 202, "##s": 203, ",": 204}},
        "post_processor": {"type": "TemplateProcessing", "single": [
            {"SpecialToken": {"id": "[CLS]", "type_id": 0}},
            {"Sequence": {"id": "A", "type_id": 0}},
            {"SpecialToken": {"id": "[SEP]", "type_id": 0}}]},
        "added_tokens": [{"content": text, "id": value, "normalized": False, "special": True,
                          "single_word": False, "lstrip": False, "rstrip": False}
                         for text, value in wordpiece.SPECIAL.items()]}
    return definition


def test_wordpiece_case_specials_chinese_cleaning_and_whole_word_unknown():
    encoder = wordpiece.FrozenWordPiece(tokenizer())
    ids, stats = encoder.encode("课程\x00 APIs,api\t[MASK]")
    assert ids == [101, 200, 201, 202, 203, 204, 100, 103, 102]
    assert stats == {"full_tokens": 9, "encoded_tokens": 9,
                     "truncated": False, "unknown_tokens": 1}
    assert encoder.word("APIx") == [100]
    assert encoder.word("课" * 101) == [100]
    ids, stats = encoder.encode("课程课程", maximum=4)
    assert ids == [101, 200, 201, 102] and stats["truncated"]
    assert stats["full_tokens"] == 6


def test_vocabulary_unicode_line_separator_is_a_token_not_a_record_boundary():
    definition = tokenizer()
    words = ["word" + str(i) for i in range(205)]
    for token, index in definition["model"]["vocab"].items():
        words[index] = token
    words[150] = "\u2028"
    definition["model"]["vocab"] = {word: i for i, word in enumerate(words)}
    encoder = wordpiece.FrozenWordPiece(definition)
    encoder.verify_vocabulary("\n".join(words) + "\n")
    with pytest.raises(ValueError, match="vocabulary disagree"):
        encoder.verify_vocabulary("\n".join(words).replace("\u2028", "wrong"))


@pytest.mark.parametrize("field", ["normalizer", "model", "added_tokens", "post_processor"])
def test_unknown_tokenizer_configuration_fails_closed(field):
    definition = tokenizer()
    if field == "normalizer":
        definition[field]["lowercase"] = True
    elif field == "model":
        definition[field]["max_input_chars_per_word"] = 200
    elif field == "added_tokens":
        definition[field][0]["normalized"] = True
    else:
        definition[field]["single"] = []
    with pytest.raises(ValueError):
        wordpiece.FrozenWordPiece(definition)


def synthetic_index_rows():
    corpus = trial.base.read(trial.OLD / "corpus.json")
    rows = [{"kind": "document", "id": d["id"],
             "text_sha256": trial.digest(d["text"].encode()), "vector": [1.0] + [0.0] * 511}
            for d in corpus["documents"]]
    rows.append({"kind": "query", "id": "TEST_ONLY", "text_sha256": trial.digest(b"query"),
                 "vector": [1.0] + [0.0] * 511})
    return corpus, rows


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "text", "nonfinite", "norm"])
def test_vector_cache_rejects_drift_and_invalid_values(mutation):
    corpus, rows = synthetic_index_rows()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "text":
        rows[0]["text_sha256"] = "forged"
    elif mutation == "nonfinite":
        rows[0]["vector"][0] = float("nan")
    else:
        rows[0]["vector"][0] = 2.0
    with pytest.raises(ValueError, match="embedding cache"):
        trial.CosineIndex(corpus, {"TEST_ONLY": "query"}, rows)


def test_index_only_uses_frozen_queries_and_pinned_visibility():
    corpus, rows = synthetic_index_rows()
    index = trial.CosineIndex(corpus, {"TEST_ONLY": "query"}, rows)
    with pytest.raises(ValueError, match="frozen local embedding"):
        index.rank("new query")
    ranked = index.rank("query", lambda d: trial.base.eligible(
        d, {"system": "mini"}, index.revisions))
    assert ranked and all(d["system"] == "mini" and d["visibility"] == "PUBLIC"
                          and d["target_revision"] == index.revisions["mini"] for d, _ in ranked)
    assert [d["id"] for d, _ in ranked] == sorted(d["id"] for d, _ in ranked)


def test_public_results_are_consistent_without_rewriting_historical_scores():
    result = trial.base.read(HERE / "results.json")
    previous = trial.base.read(trial.OLD / "results.json")
    assert result["classification"] == "DEVELOPMENT_ONLY"
    assert result["review_status"] == "UNREVIEWED" and not result["independent_holdout"]
    assert result["planning_accuracy"] is None and result["real_end_to_end_success"] is None
    assert len(result["rows"]) == 12 and result["embedding"]["sequences"] == 40
    for row, old in zip(result["rows"], previous["rows"]):
        assert row["id"] == old["id"]
        assert all(row[mode]["B"] == old[mode] for mode in ("raw", "scoped"))
    for arm in ("B", "C"):
        for mode in ("raw", "scoped"):
            for k in ("3", "5"):
                actual = result["summary"][arm][mode][k]
                values = [row[mode][arm][k] for row in result["rows"]]
                assert actual["macro_recall"] == sum(v["recall"] for v in values) / 12
                for field in ("returned", "unrelated", "cross_system", "private", "stale",
                              "conflict_fixture"):
                    assert actual[field] == sum(v[field] for v in values)
    for arm in ("A", "B3", "B5", "C3", "C5"):
        assert result["request_bytes"][arm] == sum(
            row["requests"][arm]["request_bytes"] for row in result["rows"])


class CapturePlanner:
    async def generate(self, context):
        self.context = copy.deepcopy(context)
        return "fixed plan"


@pytest.mark.asyncio
async def test_trial_is_default_off_and_keeps_original_input():
    delegate = CapturePlanner()
    wrapper = trial.ReferencePlanner(delegate, None, system="mini")
    context = {"user_task": "query", "trusted": {"operation": "approved"}}
    assert await wrapper.generate(context) == "fixed plan"
    assert delegate.context == context and wrapper.last_notes == []


@pytest.mark.asyncio
async def test_enabled_trial_only_adds_bounded_untrusted_quotes():
    corpus, rows = synthetic_index_rows()
    index = trial.CosineIndex(corpus, {"TEST_ONLY": "query"}, rows)
    delegate = CapturePlanner()
    wrapper = trial.ReferencePlanner(delegate, index, system="mini", enabled=True)
    context = {"user_task": "query", "trusted": {"operation": "approved"}}
    before = copy.deepcopy(context)
    assert await wrapper.generate(context) == "fixed plan"
    assert context == before
    assert {k: v for k, v in delegate.context.items() if k != "retrieval_notes"} == before
    assert wrapper.last_notes
    assert all(note["authority"] == "UNTRUSTED_PLANNING_REFERENCE_ONLY"
               for note in wrapper.last_notes)
    assert len(trial.base.encoded(wrapper.last_notes)) <= 12000
    with pytest.raises(ValueError, match="namespace collision"):
        await wrapper.generate({**context, "retrieval_notes": []})
    with pytest.raises(ValueError, match="Unknown retrieval system"):
        trial.ReferencePlanner(delegate, index, system="unknown", enabled=True)
