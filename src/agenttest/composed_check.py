"""Offline development archive and independent rescore; no live-model entry point."""

import hashlib
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from agenttest.composed_demo import ComposedDraftPlanner, composed_inputs, run_composed_demo
from agenttest.composed_evaluation import (
    CompositionGoals,
    CompositionPlanEvidence,
    CompositionSuite,
    capture_composition,
    composition_summary,
    reference_draft,
    score_composed_report,
)
from agenttest.contracts import summarize_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.evaluation import save_evaluation
from agenttest.policies import PolicyRegistry, RegisteredPolicy
from agenttest.report import ReportStore, RunReport, fingerprint

SUITE_PATH = PROJECT_ROOT / "examples/composed-development.json"


def source_snapshot() -> dict[str, str]:
    paths = [*sorted((PROJECT_ROOT / "src/agenttest").rglob("*.py")), SUITE_PATH,
             PROJECT_ROOT / "scripts/check_composed.py",
             *[PROJECT_ROOT / "examples" / name for name in (
                 "demo-target.json", "nl-policies.json", "batch-policies.json",
                 "batch-csv-fixture.json")]]
    return {p.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths}


async def check_composition(directory: Path) -> dict:
    """Run fixed fixtures through all real offline gates; archive exclusive evidence."""
    from agenttest.workflow import FakeLLM

    suite = CompositionSuite.model_validate(read_json(SUITE_PATH))
    target, _, registry, _ = composed_inputs()
    contract = summarize_openapi(target.openapi())
    sources = source_snapshot()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
                          capture_output=True, text=True, timeout=10).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, check=True,
                           capture_output=True, text=True, timeout=10).stdout.strip()
    directory.mkdir(parents=True, exist_ok=False)
    save_evaluation({
        "schema_version": suite.schema_version, "source": "fixed_plan_fixture",
        "suite": suite.model_dump(), "suite_sha256": fingerprint(suite.model_dump()),
        "code_commit": head, "worktree_dirty": bool(dirty), "sources": sources,
        "python": sys.version.split()[0],
        "dependencies": {name: version(name) for name in ("langgraph", "httpx", "pydantic")},
        "registry": [p.model_dump(mode="json") for p in registry.definitions()],
        "registry_sha256": registry.digest(), "contract_sha256": fingerprint(contract.model_dump()),
    }, directory / "freeze.json")
    store, rows = ReportStore(directory / "reports"), []
    for case in suite.cases:
        if source_snapshot() != sources:
            raise ValueError("Development inputs changed during execution")
        labels = case.expected or CompositionGoals(
            creator="alice", course_name="安全拒绝夹具", goals=["consistency"])
        draft = reference_draft(labels)
        evidence = capture_composition(draft.scenario(), registry)
        report = await run_composed_demo(case.user_task,
            ComposedDraftPlanner(FakeLLM(draft.model_dump_json())), store)
        row = score_composed_report(case, report, evidence, registry)
        save_evaluation(evidence.model_dump(), directory / (case.case_id + ".plan.json"))
        save_evaluation(row, directory / (case.case_id + ".json"))
        rows.append(row)
    if source_snapshot() != sources:
        raise ValueError("Development inputs changed during execution")
    result = composition_summary(rows)
    save_evaluation(result, directory / "results.json")
    with (directory / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write(f"# 组合任务离线机制验收\n\n"
                     f"{result['mechanism_verified']}/{len(rows)} 项机制符合开发标签。\n\n"
                     "DEVELOPMENT_ONLY / UNREVIEWED，固定计划夹具；真实模型调用为 0。\n"
                     "不代表模型规划能力、独立 holdout 或正式 Eval v2 成绩。\n")
    seal(directory)
    verify_composition(directory)
    return {k: v for k, v in result.items() if k != "results"}


def verify_composition(directory: Path) -> dict:
    """Rescore sealed evidence with frozen labels/rules, without rerunning any HTTP."""
    manifest = read_json(directory / "archive.json")
    if (manifest.get("schema_version") != "agenttest.eval.archive.v1"
            or manifest.get("files") != inventory(directory)):
        raise ValueError("Composition archive integrity mismatch")
    frozen = read_json(directory / "freeze.json")
    suite = CompositionSuite.model_validate(frozen["suite"])
    registry = PolicyRegistry([RegisteredPolicy.model_validate(p) for p in frozen["registry"]])
    if (frozen["schema_version"] != suite.schema_version
            or frozen["source"] != "fixed_plan_fixture"
            or fingerprint(suite.model_dump()) != frozen["suite_sha256"]
            or registry.digest() != frozen["registry_sha256"]):
        raise ValueError("Composition freeze identity mismatch")
    result, rows, run_ids = read_json(directory / "results.json"), [], set()
    for case in suite.cases:
        row = read_json(directory / (case.case_id + ".json"))
        ReportStore.validate_id(row["run_id"])
        if row["run_id"] in run_ids:
            raise ValueError("Duplicate composition report identity")
        run_ids.add(row["run_id"])
        report = RunReport.model_validate(read_json(
            directory / "reports" / row["run_id"] / "report.json"))
        if report.run_id != row["run_id"] or report.contract_sha256 != frozen["contract_sha256"]:
            raise ValueError("Composition report/freeze identity mismatch")
        if report.model_call is not None and report.model_call.calls:
            raise ValueError("Fixed-fixture archive cannot claim model-call provenance")
        evidence = CompositionPlanEvidence.model_validate(read_json(
            directory / (case.case_id + ".plan.json")))
        rescored = score_composed_report(case, report, evidence, registry)
        if rescored != row:
            raise ValueError("Composition case/rescore mismatch")
        rows.append(rescored)
    if composition_summary(rows) != result:
        raise ValueError("Composition summary/rescore mismatch")
    return {"archive_verified": True, "rescored_cases": len(rows),
            "mechanism_verified": result["mechanism_verified"], "real_model_calls": 0,
            "review_status": "UNREVIEWED", "formal_eval_v2_score": None}
