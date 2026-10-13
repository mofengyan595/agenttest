"""Opt-in planning-reference prototype and same-corpus semantic retrieval comparison."""

import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

from agenttest.planner_input import validate_user_task

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLD = ROOT / "experiments/rag_necessity"
SPEC = importlib.util.spec_from_file_location("rag_baseline", OLD / "evaluate.py")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)
REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781"
WEIGHT_SHA = "15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc"
FILES = ["experiments/rag_semantic_trial/wordpiece.py",
         "experiments/rag_semantic_trial/encode.py", "experiments/rag_semantic_trial/trial.py",
         "experiments/rag_semantic_trial/replay.py",
         "experiments/rag_semantic_trial/demo-task.json",
         "experiments/rag_semantic_trial/model-manifest.json",
         "experiments/rag_necessity/corpus.json", "experiments/rag_necessity/suite.json",
         "experiments/rag_necessity/protocol.json", "experiments/rag_necessity/results.json"]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def freeze(assets: Path, output: Path) -> None:
    base.verify_freeze(base.read(OLD / "protocol.json"))
    manifest = base.read(assets / "model-manifest.json")
    if (manifest["revision"] != REVISION
            or manifest["files"]["onnx/model_quantized.onnx"]["sha256"] != WEIGHT_SHA):
        raise ValueError("Unexpected model selection")
    base.write_new(output, {
        "classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "independent_holdout": False, "reused_questions": 12,
        "files": {name: digest((ROOT / name).read_bytes()) for name in FILES},
        "model_manifest_sha256": digest((assets / "model-manifest.json").read_bytes()),
        "model_revision": REVISION, "weight_sha256": WEIGHT_SHA,
        "parameters": {"maximum_tokens": 512, "pooling": "CLS_L2_NORMALIZED",
                       "similarity": "cosine",
                       "query_prefix": "为这个句子生成表示以用于检索相关文章：",
                       "top_k": [3, 5], "note_budget_bytes": 12000, "default_enabled": False},
        "limitations": ["same twelve UNREVIEWED development questions; no new holdout",
                        "quantized Chinese model; not a claim about all embedding models",
                        "stdlib WordPiece subset; native tokenizer parity unverified",
                        "first 512 tokens only; record every truncation",
                        "no paid LLM or Java execution; no planning-quality claim"],
        "trust": "quoted planning references only; no new permissions or assertions",
    })


def verify(protocol: dict) -> None:
    if set(protocol["files"]) != set(FILES):
        raise ValueError("Incomplete trial freeze")
    for name, expected in protocol["files"].items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise ValueError("Frozen trial changed: " + name)
    base.verify_freeze(base.read(OLD / "protocol.json"))


class CosineIndex:
    """Pure-Python ranking of local normalized vectors; no labels or model/network calls."""

    def __init__(self, corpus: dict, queries: dict[str, str], rows: list[dict]):
        base.verify_citations(corpus)
        self.documents = copy.deepcopy(corpus["documents"])
        self.revisions = dict(corpus["revisions"])
        texts = {("document", d["id"]): d["text"] for d in self.documents}
        texts.update({("query", key): value for key, value in queries.items()})
        self.vectors, self.query_keys = {}, {}
        for row in rows:
            key = (row["kind"], row["id"])
            vector = row["vector"]
            if (key in self.vectors or key not in texts
                    or row["text_sha256"] != digest(texts[key].encode())
                    or len(vector) != 512 or any(type(v) not in {int, float}
                                               or not math.isfinite(v) for v in vector)
                    or abs(sum(v * v for v in vector) - 1) > 1e-4):
                raise ValueError("Invalid or mismatched embedding cache")
            self.vectors[key] = tuple(vector)
            if key[0] == "query":
                self.query_keys[texts[key]] = key
        if set(self.vectors) != set(texts):
            raise ValueError("Incomplete embedding cache")

    def rank(self, query: str, eligible=None):
        if query not in self.query_keys:
            raise ValueError("Query has no frozen local embedding")
        vector = self.vectors[self.query_keys[query]]
        ranked = [(doc, sum(a * b for a, b in zip(vector,
                  self.vectors[("document", doc["id"])]))) for doc in self.documents
                  if eligible is None or eligible(doc)]
        return sorted(ranked, key=lambda item: (-item[1], item[0]["id"]))


def load_index(cache_path: Path, protocol_path: Path):
    protocol, cache = base.read(protocol_path), base.read(cache_path)
    verify(protocol)
    parameters = protocol["parameters"]
    if (cache["backend"] != "REAL_LOCAL_ONNX_CPU" or cache["model_revision"] != REVISION
            or cache["model_weight_sha256"] != WEIGHT_SHA
            or cache["protocol_sha256"] != digest(protocol_path.read_bytes())
            or not cache["inference_network_disabled"] or cache["LLM_API_calls"] != 0
            or cache["maximum_tokens"] != parameters["maximum_tokens"]
            or cache["query_prefix"] != parameters["query_prefix"]
            or cache["pooling"] != parameters["pooling"]
            or digest((HERE / "model-manifest.json").read_bytes())
            != protocol["model_manifest_sha256"]
            or digest(base.encoded(cache["model_manifest"])) != digest(base.encoded(
                base.read(HERE / "model-manifest.json")))):
        raise ValueError("Embedding provenance mismatch")
    corpus, suite = base.read(OLD / "corpus.json"), base.read(OLD / "suite.json")
    queries = {case["id"]: case["query"] for case in suite["cases"]}
    queries["DEMO_ONLY"] = base.read(HERE / "demo-task.json")["user_task"]
    return CosineIndex(corpus, queries, cache["rows"]), cache, protocol, corpus, suite


class ReferencePlanner:
    """Experiment-only decorator, off by default; neither targets nor policies are editable."""

    def __init__(self, planner, index, *, system: str, enabled: bool = False,
                 top_k: int = 5, note_budget_bytes: int = 12000):
        if type(enabled) is not bool or top_k not in {3, 5} or note_budget_bytes != 12000:
            raise ValueError("Unsupported reference settings")
        if enabled and (index is None or system not in index.revisions):
            raise ValueError("Unknown retrieval system")
        self.planner, self.index, self.system = planner, index, system
        self.enabled, self.top_k, self.note_budget_bytes = enabled, top_k, note_budget_bytes
        self.last_notes, self.omitted = [], []

    @property
    def stats(self):
        return getattr(self.planner, "stats", None)

    @property
    def token_reservation(self):
        return getattr(self.planner, "token_reservation", 0)

    async def generate(self, context: dict) -> str:
        original = copy.deepcopy(context)
        self.last_notes, self.omitted = [], []
        if not self.enabled:
            return await self.planner.generate(original)
        query = validate_user_task(context["user_task"])
        case = {"system": self.system}
        hits = self.index.rank(query, lambda d: base.eligible(d, case, self.index.revisions))
        for doc, _ in hits[:self.top_k]:
            if not base.eligible(doc, case, self.index.revisions):
                raise ValueError("Retrieval scope mismatch")
        augmented, self.omitted = base.with_notes(original, hits[:self.top_k],
                                                 self.note_budget_bytes)
        self.last_notes = copy.deepcopy(augmented["retrieval_notes"])
        return await self.planner.generate(augmented)


def run(cache_path: Path, protocol_path: Path, output: Path) -> dict:
    index, cache, protocol, corpus, suite = load_index(cache_path, protocol_path)
    output.mkdir(parents=True, exist_ok=False)
    bm25 = base.BM25(corpus["documents"])
    original_results = base.read(OLD / "results.json")
    rows = []
    for case in suite["cases"]:
        context, profile = base.baseline(case["system"], case["query"])
        row = {"id": case["id"], "system": case["system"],
               "raw": {}, "scoped": {}, "requests": {}, "delivered": {}}
        for arm, retriever in (("B", bm25), ("C", index)):
            raw = retriever.rank(case["query"])
            scoped = retriever.rank(case["query"], lambda d: base.eligible(
                d, case, corpus["revisions"]))
            row["raw"][arm] = {str(k): base.measures(raw, case, corpus["revisions"], k)
                               for k in (3, 5)}
            row["scoped"][arm] = {str(k): base.measures(scoped, case, corpus["revisions"], k)
                                  for k in (3, 5)}
            for k in (3, 5):
                augmented, omitted = base.with_notes(context, scoped[:k])
                payload = base.request(augmented, profile)
                arm_key = arm + str(k)
                base.write_new(output / f"{case['id']}-{arm_key}-request.json", json.loads(payload))
                row["requests"][arm_key] = {**base.costs(payload), "omitted": omitted,
                                            "context_sha256": digest(base.encoded(augmented))}
                row["delivered"][arm_key] = {"ids": [n["id"] for n in augmented["retrieval_notes"]],
                    "recall": len({n["id"] for n in augmented["retrieval_notes"]}
                                  & set(case["relevant"])) / len(case["relevant"])}
        payload = base.request(context, profile)
        row["requests"]["A"] = base.costs(payload)
        previous = next(r for r in original_results["rows"] if r["id"] == case["id"])
        if any(row[mode]["B"] != previous[mode] for mode in ("raw", "scoped")):
            raise ValueError("BM25 baseline changed")
        rows.append(row)
    summary = {}
    for arm in ("B", "C"):
        summary[arm] = {}
        for mode in ("raw", "scoped"):
            summary[arm][mode] = {}
            for k in ("3", "5"):
                values = [r[mode][arm][k] for r in rows]
                summary[arm][mode][k] = {
                    "macro_recall": sum(v["recall"] for v in values) / len(values),
                    **{key: sum(v[key] for v in values) for key in
                       ("returned", "unrelated", "cross_system", "stale", "private",
                        "conflict_fixture")}}
    result = {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
              "independent_holdout": False, "same_corpus_and_tasks": True,
              "BM25_matches_archived_results": True, "summary": summary,
              "delivered_recall": {a: sum(r["delivered"][a]["recall"] for r in rows) / len(rows)
                                   for a in ("B3", "B5", "C3", "C5")},
              "request_bytes": {a: sum(r["requests"][a]["request_bytes"] for r in rows)
                                for a in ("A", "B3", "B5", "C3", "C5")},
              "embedding": {"backend": cache["backend"], "runtime": cache["runtime"],
                            "sequences": cache["local_embedding_sequences"],
                            "weight_sha256": WEIGHT_SHA, "revision": REVISION,
                            "truncated_documents": [r["id"] for r in cache["rows"]
                                                    if r["kind"] == "document" and r["truncated"]],
                            "encoding_ms_total": sum(r["elapsed_ms"] for r in cache["rows"]),
                            "model_loading_ms": cache["model_loading_ms"]},
              "protocol_sha256": digest(protocol_path.read_bytes()),
              "vector_cache_sha256": digest(cache_path.read_bytes()),
              "planning_accuracy": None, "real_end_to_end_success": None,
              "LLM_API_calls": 0, "java_requests": 0, "database_writes": 0, "rows": rows}
    verify(protocol)
    base.write_new(output / "results.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "run"))
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--vectors", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "freeze":
        if args.assets is None:
            parser.error("freeze requires --assets")
        freeze(args.assets, args.output)
        print("Semantic trial frozen; original experiment unchanged")
    else:
        if args.vectors is None or args.protocol is None:
            parser.error("run requires --vectors and --protocol")
        result = run(args.vectors, args.protocol, args.output)
        print(json.dumps({k: result[k] for k in ("summary", "delivered_recall", "request_bytes",
                                                "embedding", "LLM_API_calls")}, indent=2))
