"""Capture selected, non-secret evidence; no target/model requests or Git writes."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
JAVA_SHA = "fbaf08f585de7839050899065ada3c36de5f3fd1"
OLD_SHA = "430c23eec8bc31023d8fb43d767bf9d8de164591"
JAVA = "courseinsight-server/src/main/java/com/courseinsight/server/"
JAVA_TEST = "courseinsight-server/src/test/java/com/courseinsight/server/"

# Fixed source ranges, not selected by a query or a relevance label.
SELECTION = [
    ("ci.csv.doc", "courseinsight", "README.md", 164, 169, "business_document"),
    ("ci.csv.code", "courseinsight", JAVA + "service/AnalysisBatchCsvParser.java",
     25, 156, "source"),
    ("ci.csv.tests", "courseinsight", JAVA_TEST + "service/AnalysisBatchCsvParserTests.java",
     15, 72, "test_specification"),
    ("ci.progress.doc", "courseinsight", "docs/development.md", 204, 209,
     "business_document"),
    ("ci.progress.code", "courseinsight", JAVA + "dto/AnalysisBatchProgressResponse.java",
     27, 91, "source"),
    ("ci.progress.mapper", "courseinsight", JAVA + "mapper/AnalysisBatchProgressMapper.java",
     26, 60, "source"),
    ("ci.results.mapper", "courseinsight", JAVA + "mapper/AnalysisBatchResultMapper.java",
     10, 45, "source"),
    ("ci.results.service", "courseinsight", JAVA + "service/AnalysisBatchResultService.java",
     60, 110, "source"),
    ("ci.batch.routes", "courseinsight", JAVA + "controller/AnalysisBatchController.java",
     53, 160, "source"),
    ("ci.batch.create", "courseinsight", JAVA + "service/AnalysisBatchCreationService.java",
     52, 100, "source"),
    ("ci.batch.response", "courseinsight", JAVA + "dto/AnalysisBatchCreateResponse.java",
     1, 40, "source"),
    ("ci.analysis.doc", "courseinsight", "docs/teacher-comment-analysis.md",
     1, 25, "business_document"),
    ("ci.analysis.code", "courseinsight", JAVA + "service/CommentAnalysisService.java",
     48, 90, "source"),
    ("ci.task.code", "courseinsight", JAVA + "service/AnalysisTaskService.java",
     40, 80, "source"),
    ("ci.task.route", "courseinsight", JAVA + "controller/AnalysisTaskController.java",
     1, 60, "source"),
    ("ci.manage.code", "courseinsight", JAVA + "service/CourseManagementAccessService.java",
     23, 55, "source"),
    ("ci.retry.code", "courseinsight", JAVA + "service/AnalysisBatchRecoveryService.java",
     45, 111, "source"),
    ("ci.retry.tests", "courseinsight",
     JAVA_TEST + "service/AnalysisBatchRecoveryServiceTests.java",
     57, 107, "test_specification"),
    ("mini.batch.doc", "mini", "docs/demo-policy.md", 24, 32, "test_specification"),
    ("mini.batch.source", "mini", "src/agenttest/mini_target.py", 91, 143, "source"),
    ("mini.batch.prompt", "mini", "src/agenttest/batch_plan.py", 66, 86,
     "existing_planner_prompt"),
    ("mini.batch.rules", "mini", "docs/batch-natural-language.md", 9, 34,
     "test_specification"),
    ("mini.fault.tests", "mini", "tests/test_batch_workflow.py", 28, 74,
     "test_specification"),
    ("mini.fault.history", "mini", "docs/demo-evidence/batch-smoke.json", 1, 10000,
     "historical_case"),
]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def capture(java_root: Path, output: Path) -> dict:
    if git(java_root, "rev-parse", "HEAD").decode().strip() != JAVA_SHA:
        raise ValueError("CourseInsight source revision differs from reviewed remote main")
    agent_sha = git(ROOT, "rev-parse", "HEAD").decode().strip()
    sources, documents = {}, []
    for doc_id, system, name, start, end, kind in SELECTION:
        root, revision = (java_root, JAVA_SHA) if system == "courseinsight" else (ROOT, agent_sha)
        raw = (root / name).read_bytes()
        decoded = raw.decode("utf-8-sig").replace("\r\n", "\n")
        try:
            committed = git(root, "show", f"{revision}:{name}")
        except subprocess.CalledProcessError:
            committed = None
        clean = (committed is not None
                 and committed.decode("utf-8-sig").replace("\r\n", "\n") == decoded)
        if system == "courseinsight" and not clean:
            raise ValueError(f"Java source differs from verified commit: {name}")
        source_id = f"{system}:{name}"
        source_version = revision if clean else f"WORKTREE:{digest(raw)}"
        sources[source_id] = {
            "system": system, "path": name, "version": source_version,
            "base_commit": revision, "sha256": digest(raw), "text": decoded,
            "matches_commit": clean, "visibility": "PUBLIC",
        }
        lines = decoded.splitlines(keepends=True)
        end = min(end, len(lines))
        text = "".join(lines[start - 1:end])
        if not text:
            raise ValueError(f"Empty source range: {doc_id}")
        documents.append({
            "id": doc_id, "system": system, "target_revision": revision,
            "source_id": source_id, "source_version": source_version,
            "start": start, "end": end, "text": text, "sha256": digest(text.encode()),
            "visibility": "PUBLIC", "kind": kind, "conflict_fixture": False,
        })
    # Authentic old version, even if its rules still agree. Freshness is not semantic correctness.
    name = JAVA + "dto/AnalysisBatchProgressResponse.java"
    raw = git(java_root, "show", f"{OLD_SHA}:{name}")
    decoded = raw.decode().replace("\r\n", "\n")
    source_id = "courseinsight:old-progress"
    sources[source_id] = {
        "system": "courseinsight", "path": name, "version": OLD_SHA, "base_commit": OLD_SHA,
        "sha256": digest(raw), "text": decoded, "matches_commit": True, "visibility": "PUBLIC",
    }
    documents.append({
        "id": "ci.progress.old", "system": "courseinsight", "target_revision": OLD_SHA,
        "source_id": source_id, "source_version": OLD_SHA, "start": 27,
        "end": len(decoded.splitlines()), "text": "".join(decoded.splitlines(keepends=True)[26:]),
        "visibility": "PUBLIC", "kind": "historical_version", "conflict_fixture": False,
    })
    documents[-1]["sha256"] = digest(documents[-1]["text"].encode())
    # Explicit counterfactual fixtures, never presented as real business facts or authorization.
    for name, visibility in (("conflicting-current.md", "PUBLIC"), ("private-note.md", "PRIVATE")):
        raw = (HERE / name).read_bytes()
        text = raw.decode()
        source_id = "experiment:" + name
        sources[source_id] = {
            "system": "experiment", "path": "experiments/rag_necessity/" + name,
            "version": "WORKTREE:" + digest(raw), "base_commit": agent_sha,
            "sha256": digest(raw), "text": text, "matches_commit": False,
            "visibility": visibility,
        }
        documents.append({
            "id": "negative." + name.split(".")[0], "system": "courseinsight",
            "target_revision": JAVA_SHA, "source_id": source_id,
            "source_version": sources[source_id]["version"], "start": 1,
            "end": len(text.splitlines()), "text": text, "sha256": digest(raw),
            "visibility": visibility, "kind": "SYNTHETIC_ADVERSARIAL",
            "conflict_fixture": True,
        })
    result = {
        "classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
        "revisions": {"courseinsight": JAVA_SHA, "mini": agent_sha},
        "remote_verification": {
            "checked_date": "2026-10-13", "url":
            "https://api.github.com/repos/mofengyan595/courseinsight-cloud/branches/main",
            "sha": JAVA_SHA, "method": "read-only GitHub connector; checked before capture",
            "not_rechecked_by_offline_runner": True,
        },
        "sources": sources, "documents": documents,
    }
    for source in sources.values():
        source["text_sha256"] = digest(source["text"].encode())
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--courseinsight-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = capture(args.courseinsight_root, args.output)
    print(f"Captured {len(data['documents'])} documents; no target/model calls")
