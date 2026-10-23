"""Use old development requests only, before defining any new final evaluation request."""

import argparse
import asyncio
import json
from pathlib import Path

from trusted_validation_support import PROFILES, ObservedASGI, mock_models, target_runtime

from agenttest.goal_suite import build_suite, run_suite
from agenttest.project_goals import load_goal_project
from agenttest.report import ReportStore
from agenttest.workflow import WorkflowBudget


async def check(output):
    rows = []
    for profile, text, expected in [
        ("courses", "测试创建课程接口", True),
        ("tickets", "测试创建工单接口", True),
        ("courses", "测试这个项目已授权的核心接口", True),
        ("tickets", "测试这个项目已授权的核心接口", True),
        ("tickets_limited", "测试这个项目已授权的核心接口", False),
    ]:
        project = load_goal_project(PROFILES[profile] / "registered.project.json")
        app, env = target_runtime(project)
        transport = ObservedASGI(app)
        planner, analyzer, semantic = mock_models()
        result = await run_suite(project, text, planner, ReportStore(output / str(len(rows))),
            environ=env, transport=transport, analyzer=analyzer, semantic_model=semantic,
            budget=WorkflowBudget(model_timeout=61, workflow_timeout=300))
        if result["complete"] != expected or transport.requests != result["http_requests"]:
            raise AssertionError((profile, result["complete"], result["cases"]))
        rows.append(dict(profile=profile, scope=result["scope"], complete=result["complete"],
            usage=result["usage"], scenarios=len(result["cases"]),
            budget_preview=build_suite(project, text).preview()["budget_preview"]))
        (output / f"case-{len(rows)}.json").write_text(json.dumps(result, indent=2) + "\n",
                                                      encoding="utf-8")
    summary = dict(classification="DEVELOPMENT_ONLY", source="mock", real_model_calls=0, rows=rows)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    print(json.dumps(asyncio.run(check(args.output))))
