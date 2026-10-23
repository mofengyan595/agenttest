"""Developer portability evidence on a new synthetic graph, using the frozen core unchanged."""

import argparse
import asyncio
import json
import runpy
import subprocess
from pathlib import Path

import httpx

from agenttest.demo import PROJECT_ROOT
from agenttest.goal_suite import build_suite, run_suite
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.project_goals import load_goal_project
from agenttest.report import ReportStore

ROOT = PROJECT_ROOT / "examples/trusted-testing/laboratory"


def core_identity():
    frozen = json.loads((ROOT / "core-freeze.json").read_bytes())
    actual_tree = subprocess.check_output(["git", "rev-parse",
        frozen["core_sha"] + ":src/agenttest"], cwd=PROJECT_ROOT, text=True).strip()
    if actual_tree != frozen["core_tree"]:
        raise ValueError("CORE_FREEZE_TREE_MISMATCH")
    changed = subprocess.run(["git", "diff", "--quiet", frozen["core_sha"], "--", "src/agenttest"],
                             cwd=PROJECT_ROOT, check=False).returncode
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard",
                                        "src/agenttest"], cwd=PROJECT_ROOT, text=True).strip()
    return dict(core_sha=frozen["core_sha"], core_tree=frozen["core_tree"],
                core_unchanged=changed == 0 and not untracked)


def runtime(fault="none"):
    # Exact operator-reviewed fixture, outside product core; never an LLM-selected import path.
    return runpy.run_path(str(ROOT / "fixture.py"))["runtime"](fault)


async def check(output):
    identity = core_identity()
    if not identity["core_unchanged"]:
        raise ValueError("CORE_CHANGED_AFTER_PORTABILITY_FREEZE")
    project = load_goal_project(ROOT / "registered.project.json")
    results = []
    for scope, text in [("single", "测试登记样本接口"),
        ("flow", "先登记容器，再登记样本，然后装载、查验、跨角色拒绝"),
        ("project", "测试这个项目已授权的核心接口")]:
        preview = build_suite(project, text).preview()
        app, env = runtime()
        result = await run_suite(project, text, CatalogFixturePlanner(),
            ReportStore(output / scope), environ=env, transport=httpx.ASGITransport(app=app))
        if not result["complete"]:
            raise AssertionError((scope, [(c["kinds"], c.get("reason"), c.get("outcome"))
                                         for c in result["cases"]]))
        if app.state.requests != result["http_requests"]:
            raise AssertionError("SYNTHETIC_REQUEST_LEDGER_MISMATCH")
        row = dict(scope=scope, complete=result["complete"], coverage=result["coverage"],
                   usage=result["usage"], budget_preview=preview["budget_preview"])
        (output / (scope + ".json")).write_text(json.dumps(result, indent=2) + "\n",
                                               encoding="utf-8")
        results.append(row)
    final = core_identity()
    if final != identity:
        raise ValueError("CORE_CHANGED_DURING_PORTABILITY_CHECK")
    receipt = dict(identity, classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED",
        independent_human_reviewed=False, real_model_calls=0, external_business_requests=0,
        model_contribution="CatalogFixturePlanner is deterministic, not real-model evidence",
        configuration_files=6, goals=5, approved_negative_rules=15,
        human_time_saved=None, results=results)
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    print(json.dumps(asyncio.run(check(args.output)), ensure_ascii=False))
