"""CPU-only local ONNX inference worker; dependencies stay outside the project environment."""

import argparse
import hashlib
import json
import platform
import socket
import time
from pathlib import Path

from wordpiece import FrozenWordPiece

REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781"
WEIGHT_SHA = "15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
ROOT = Path(__file__).resolve().parents[2]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path):
    return json.loads(path.read_bytes())


def block_network(*args, **kwargs):
    raise RuntimeError("Embedding inference is strictly offline")


def encode_assets(assets: Path, protocol: Path, output: Path) -> dict:
    """Check every pinned input before loading the non-executable model graph."""
    socket.getaddrinfo = block_network
    socket.socket.connect = block_network
    socket.socket.connect_ex = block_network
    import numpy as np
    import onnxruntime as ort

    frozen = read(protocol)
    for name, expected in frozen["files"].items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise ValueError("Frozen trial input changed: " + name)
    manifest = read(assets / "model-manifest.json")
    if (digest((assets / "model-manifest.json").read_bytes()) != frozen["model_manifest_sha256"]
            or manifest["revision"] != REVISION
            or manifest["files"]["onnx/model_quantized.onnx"]["sha256"] != WEIGHT_SHA):
        raise ValueError("Pinned model identity changed")
    expected_assets = {"config.json", "tokenizer.json", "tokenizer_config.json",
                       "special_tokens_map.json", "vocab.txt", "README.md",
                       "onnx/model_quantized.onnx"}
    if set(manifest["files"]) != expected_assets:
        raise ValueError("Unexpected model assets")
    for name, expected in manifest["files"].items():
        raw = (assets / "model" / name).read_bytes()
        if len(raw) != expected["bytes"] or digest(raw) != expected["sha256"]:
            raise ValueError("Model asset digest mismatch")
    tokenizer = FrozenWordPiece(read(assets / "model/tokenizer.json"))
    tokenizer.verify_vocabulary((assets / "model/vocab.txt").read_text(encoding="utf-8"))
    config = read(assets / "model/config.json")
    if config["max_position_embeddings"] != 512 or config["hidden_size"] != 512:
        raise ValueError("Unexpected model dimensions")
    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    started = time.perf_counter()
    session = ort.InferenceSession(str(assets / "model/onnx/model_quantized.onnx"),
                                  sess_options=options, providers=["CPUExecutionProvider"])
    loading_ms = (time.perf_counter() - started) * 1000
    if {v.name for v in session.get_inputs()} != {
            "input_ids", "attention_mask", "token_type_ids"}:
        raise ValueError("Unexpected ONNX input contract")
    output_name = session.get_outputs()[0].name
    corpus = read(ROOT / "experiments/rag_necessity/corpus.json")
    suite = read(ROOT / "experiments/rag_necessity/suite.json")
    items = [("document", d["id"], d["text"]) for d in corpus["documents"]]
    items += [("query", c["id"], c["query"]) for c in suite["cases"]]
    demo = read(ROOT / "experiments/rag_semantic_trial/demo-task.json")
    items.append(("query", "DEMO_ONLY", demo["user_task"]))
    rows = []
    for kind, key, text in items:
        value = QUERY_PREFIX + text if kind == "query" else text
        ids, statistics = tokenizer.encode(value)
        started = time.perf_counter()
        ids = np.asarray([ids], dtype=np.int64)
        output_values = session.run([output_name], {"input_ids": ids,
            "attention_mask": np.ones_like(ids), "token_type_ids": np.zeros_like(ids)})[0]
        if output_values.shape != (1, ids.shape[1], 512):
            raise ValueError("Unexpected ONNX output shape")
        vector = output_values[0, 0].astype(np.float32)
        norm = np.linalg.norm(vector)
        if not np.isfinite(vector).all() or not np.isfinite(norm) or norm <= 0:
            raise ValueError("Invalid embedding")
        vector /= norm
        rows.append({"kind": kind, "id": key, "text_sha256": digest(text.encode()),
                     **statistics, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                     "vector": vector.tolist()})
    result = {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
              "backend": "REAL_LOCAL_ONNX_CPU", "model_revision": REVISION,
              "model_weight_sha256": WEIGHT_SHA, "model_manifest": manifest,
              "protocol_sha256": digest(protocol.read_bytes()),
              "runtime": {"python": platform.python_version(), "onnxruntime": ort.__version__,
                          "numpy": np.__version__, "providers": session.get_providers()},
              "model_loading_ms": round(loading_ms, 3), "pooling": "CLS_L2_NORMALIZED",
              "query_prefix": QUERY_PREFIX, "maximum_tokens": 512,
              "inference_network_disabled": True, "LLM_API_calls": 0,
              "local_embedding_sequences": len(rows), "rows": rows}
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = encode_assets(args.assets, args.protocol, args.output)
    print(json.dumps({k: result[k] for k in ("backend", "runtime", "local_embedding_sequences",
                                            "model_loading_ms", "LLM_API_calls")}, indent=2))
