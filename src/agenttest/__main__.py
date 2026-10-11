"""Run with python -m agenttest; no API key or model is involved."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.contracts import MAX_DOCUMENT_BYTES, load_openapi
from agenttest.demo import run_demo
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig


def bounded_read(path: Path, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Input file exceeds limit")
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentTest 确定性 API 测试")
    subcommands = parser.add_subparsers(dest="command", required=True)
    demo = subcommands.add_parser("demo", help="运行进程内离线模拟场景")
    demo.add_argument("--fault", choices=["none", "ownership", "terminal"], default="none")
    execute = subcommands.add_parser("run", help="执行人工计划和显式授权的目标配置")
    for name in ("contract", "plan", "target", "policies"):
        execute.add_argument(f"--{name}", type=Path, required=True)
    report = subcommands.add_parser("report", help="查询或导出脱敏 JSON 报告")
    report.add_argument("run_id")
    report.add_argument("--output", type=Path)
    for command in (demo, execute, report):
        command.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    store = ReportStore(args.runs_dir)
    try:
        if args.command == "report":
            result = store.read(args.run_id)
            if args.output:
                args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            else:
                print(result.model_dump_json(indent=2))
            return 0
        if args.command == "demo":
            result = asyncio.run(run_demo(args.fault, store))
        else:
            config = TargetConfig.model_validate_json(bounded_read(args.target))
            contract = load_openapi(bounded_read(args.contract))
            scenario = Scenario.model_validate_json(bounded_read(args.plan))
            registry = PolicyRegistry.from_json(bounded_read(args.policies))
            result = asyncio.run(Executor(config, contract, store, registry=registry).run(scenario))
        print(json.dumps({
            "run_id": result.run_id, "outcome": result.outcome.value,
            "steps": len(result.steps),
            "report": str(store.root / result.run_id / "report.json"),
        }, ensure_ascii=False))
        return 0 if result.outcome == Outcome.PASS else 1
    except (ValueError, OSError):
        print(json.dumps({"outcome": "TOOL_ERROR", "reason": "INVALID_INPUT_OR_REPORT_IO"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
