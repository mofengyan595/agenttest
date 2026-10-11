"""Explicit bounded evaluation entry point; never called by keyless CI."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.demo import PROJECT_ROOT
from agenttest.evaluation import TaskSuite, evaluate, save_evaluation
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import ReportStore
from agenttest.workflow import WorkflowBudget


async def run(args) -> dict:
    suite = TaskSuite.load(args.suite)
    if args.task_ids:
        selected = set(args.task_ids.split(","))
        suite.cases = [case for case in suite.cases if case.id in selected]
        if {case.id for case in suite.cases} != selected:
            raise ValueError("Unknown task id")
    planner, budget = None, WorkflowBudget()
    if args.baseline == "real":
        settings = (ModelSettings.from_env_file(args.model_env) if args.model_env
                    else ModelSettings.from_environment())
        settings = ModelSettings.model_validate({**settings.model_dump(),
            "max_output_tokens": args.max_output_tokens, "timeout": args.model_timeout})
        planner = OpenAICompatiblePlanner(settings)
        budget = WorkflowBudget(model_timeout=settings.timeout + 1,
                                workflow_timeout=min(300, settings.timeout + 90))
    result = await evaluate(suite, args.baseline, ReportStore(args.runs_dir), planner=planner,
                            max_live_calls=args.max_live_calls,
                            max_total_tokens=args.max_total_tokens, budget=budget,
                            progress=lambda item: print(json.dumps(item), flush=True))
    save_evaluation(result, args.output)
    return {key: value for key, value in result.items() if key != "results"}


def main() -> int:
    parser = argparse.ArgumentParser(description="受控三基线评测，真实调用必须显式设置预算")
    parser.add_argument("--baseline", required=True, choices=["manual", "fake", "real"])
    parser.add_argument("--suite", type=Path, default=PROJECT_ROOT / "examples/nl-tasks.json")
    parser.add_argument("--task-ids", help="可选逗号分隔 ID；未选择任务不计入分母")
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--max-live-calls", type=int, default=0)
    parser.add_argument("--max-total-tokens", type=int, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--model-timeout", type=float, default=30)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs/evaluation"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["attempted"] == result["requested"] else 1
    except (ValueError, OSError, ImportError):
        print(json.dumps({"outcome": "TOOL_ERROR", "reason": "INVALID_EVALUATION_CONFIGURATION"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
