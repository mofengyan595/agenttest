"""Reproducible three-scope experience, using existing synthetic targets and mock models."""

import argparse
import asyncio
import json
from pathlib import Path

from trusted_validation_support import PROFILES, mock_models, target_runtime

from agenttest.goal_suite import build_suite, run_suite
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionRuntime, export_regression
from agenttest.report import ReportStore, fingerprint

TEXTS = {"single": "请验证创建工单接口", "flow": "请验证创建、关闭、读回、权限拒绝流程",
         "project": "请验证这个项目已授权的核心接口"}


async def demo(output, scope):
    project = load_goal_project(PROFILES["tickets"] / "registered.project.json")
    text = TEXTS[scope]
    app, env = target_runtime(project)
    import httpx

    store, assets, reports = ReportStore(output / "reports"), {}, {}

    def plan(goal, scenario):
        assets[fingerprint(goal.task.model_dump())] = scenario

    def report(goal, result):
        reports[result.run_id] = (goal, assets.get(fingerprint(goal.task.model_dump())), result)

    planner, analyzer, semantic = mock_models()
    result = await run_suite(project, text, planner, store, environ=env,
        transport=httpx.ASGITransport(app=app), analyzer=analyzer, semantic_model=semantic,
        on_validated_plan=plan, on_report=report)
    exports = []
    if scope == "single":
        for label, negative in (("normal", False), ("negative", True)):
            case = next(c for c in result["cases"] if c.get("plan_derivation")
                and bool(c["approved_negative_steps"]) == negative and c["task_satisfied"])
            goal, scenario, source = reports[case["run_id"]]
            destination = output / ("regression-" + label)
            export_regression(destination, RegressionRuntime(project.document, project.target,
                project.registry, goal.task, env, None, store), scenario, source)
            exports.append(dict(kind=label, path=str(destination), case_id=case["case_id"],
                                source_run_id=source.run_id))
    summary = dict(classification="DEVELOPMENT_ONLY/UNREVIEWED", source="SIMULATED",
        original_request=text, real_model_calls=0, result=result, exports=exports,
        independent_human_review=False, human_time_seconds=None,
        preview=build_suite(project, text).preview())
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=TEXTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(demo(args.output, args.scope))
    print(json.dumps(dict(scope=args.scope, complete=result["result"]["complete"],
        usage=result["result"]["usage"], exports=result["exports"], real_model_calls=0)))
