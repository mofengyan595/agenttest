"""Offline commands by default; real model access requires explicit --real."""

import argparse
import asyncio
import json
import os
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
    discovery = subcommands.add_parser("discover", help="离线发现 OpenAPI 测试候选；不执行测试")
    discovery.add_argument("--contract", type=Path, required=True)
    discovery.add_argument("--policies", type=Path, required=True,
                           help="操作员选择的已批准 Registry；不会从契约或模型接受策略")
    discovery.add_argument("--target", type=Path, help="可选的操作员目标配置；只读元数据")
    discovery.add_argument("--business-scenario", default="default",
                           help="未提供 target 时使用的策略作用域")
    discovery.add_argument("--exclude-operation", action="append", default=[])
    discovery.add_argument("--output", type=Path, required=True,
                           help="新的 *.discovery.json 文件；拒绝覆盖已有文件")
    showcase = subcommands.add_parser("showcase", help="统一离线演示；故障检出也视为演示成功")
    showcase.add_argument("--case", choices=["normal", "fault", "investigation", "java-mock",
                                           "mcp", "all"], default="normal")
    showcase.add_argument("--runs-dir", type=Path, default=Path("runs/showcase"))
    demo = subcommands.add_parser("demo", help="运行进程内离线模拟场景")
    demo.add_argument("--fault", choices=["none", "ownership", "terminal"], default="none")
    workflow_demo = subcommands.add_parser("workflow-demo", help="运行 Fake LLM 离线状态图")
    workflow_demo.add_argument("--fault", choices=["none", "ownership", "terminal"], default="none")
    investigation_demo = subcommands.add_parser(
        "investigation-demo", help="显式启用单轮 FakeAnalyzer，只读调查隔离 mini target")
    investigation_demo.add_argument("--fault", choices=["none", "ownership"], default="ownership")
    execute = subcommands.add_parser("run", help="执行人工计划和显式授权的目标配置")
    workflow = subcommands.add_parser("workflow", help="通过 Fake LLM 规划样本运行状态图")
    workflow.add_argument("--user-task", help="提供需求时必须通过意图校验；省略则执行固定人工计划")
    agent = subcommands.add_parser("agent", help="自然语言规划显式授权的目标")
    agent_demo = subcommands.add_parser("agent-demo", help="自然语言规划一次性 mini target")
    agent_demo.add_argument("--fault", choices=["none", "ownership", "terminal"], default="none")
    for command in (agent, agent_demo):
        command.add_argument("--user-task", required=True)
        model = command.add_mutually_exclusive_group(required=True)
        model.add_argument("--real", action="store_true")
        model.add_argument("--fake-plan", type=Path)
        command.add_argument("--model-env", type=Path)
        command.add_argument("--max-output-tokens", type=int, default=2048)
        command.add_argument("--max-total-tokens", type=int, default=12_000)
        command.add_argument("--model-timeout", type=float, default=30.0)
    for name in ("contract", "target", "policies"):
        agent.add_argument(f"--{name}", type=Path, required=True)
    agent.add_argument("--task-contract", type=Path,
                       help="操作员提供的任务约束；启用通用规划并保留确定性意图校验")
    agent.add_argument("--export-regression", type=Path,
                       help="通用任务完整通过后导出固定 pytest 包；保存明文合成测试参数")
    export = subcommands.add_parser("export-pytest", help="从完整计划和已通过报告导出固定回归包")
    for name in ("contract", "target", "policies", "task-contract", "plan", "output"):
        export.add_argument(f"--{name}", type=Path, required=True)
    export.add_argument("--source-run", required=True)
    for command in (execute, workflow):
        for name in ("contract", "plan", "target", "policies"):
            command.add_argument(f"--{name}", type=Path, required=True)
    report = subcommands.add_parser("report", help="查询或导出脱敏 JSON 报告")
    report.add_argument("run_id")
    report.add_argument("--output", type=Path)
    for command in (demo, workflow_demo, investigation_demo, execute, workflow,
                    agent, agent_demo, report, export):
        command.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            from agenttest.discovery import discover, discovery_json

            if not args.output.name.endswith(".discovery.json"):
                raise ValueError("Discovery output must use .discovery.json suffix")
            result = discover(load_openapi(bounded_read(args.contract)),
                PolicyRegistry.from_json(bounded_read(args.policies)),
                (TargetConfig.model_validate_json(bounded_read(args.target))
                 if args.target else None),
                business_scenario=args.business_scenario,
                excluded_operation_ids=args.exclude_operation)
            content = discovery_json(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(content)
            print(json.dumps({"format_version": result.format_version,
                "candidates": len(result.candidates),
                "operation_coverage": result.coverage.operation.ratio.model_dump(),
                "scenario_coverage": result.coverage.scenario.model_dump(),
                "needs_review": len(result.candidates), "tests_executed": 0,
                "http_requests": 0, "model_calls": 0}, ensure_ascii=False))
            return 0
        store = ReportStore(args.runs_dir)
        regression_result = None
        regression_error = False
        if args.command == "export-pytest":
            from agenttest.regression import RegressionRuntime, export_regression
            from agenttest.task_contract import TaskContract

            runtime = RegressionRuntime(bounded_read(args.contract),
                TargetConfig.model_validate_json(bounded_read(args.target)),
                PolicyRegistry.from_json(bounded_read(args.policies)),
                TaskContract.model_validate_json(bounded_read(args.task_contract)),
                dict(os.environ), None, store)
            manifest = export_regression(args.output, runtime,
                Scenario.model_validate_json(bounded_read(args.plan)), store.read(args.source_run))
            print(json.dumps({"exported": True, "source_run_id": manifest.source_run_id,
                              "directory": str(args.output.absolute()), "model_calls": 0,
                              "http_requests": 0}, ensure_ascii=False))
            return 0
        if args.command == "showcase":
            from agenttest.showcase import showcase

            result = asyncio.run(showcase(args.case, store))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["verified"] else 1
        if args.command == "report":
            result = store.read(args.run_id)
            if args.output:
                args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            else:
                print(result.model_dump_json(indent=2))
            return 0
        if args.command == "investigation-demo":
            from agenttest.investigation_demo import run_investigation_demo

            result = asyncio.run(run_investigation_demo(store, fault=args.fault))
        elif args.command in {"demo", "workflow-demo"}:
            result = asyncio.run(run_demo(args.fault, store,
                                         use_workflow=args.command == "workflow-demo"))
        elif args.command in {"agent", "agent-demo"}:
            from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
            from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

            budget = WorkflowBudget()
            validator = None
            capture = None
            generic = args.command == "agent" and args.task_contract is not None
            if args.command == "agent" and args.export_regression:
                from agenttest.regression import validate_destination

                if not generic:
                    raise ValueError("Regression export requires an operator task contract")
                validate_destination(args.export_regression)
            if args.command == "agent":
                document = bounded_read(args.contract)
                config = TargetConfig.model_validate_json(bounded_read(args.target))
                registry = PolicyRegistry.from_json(bounded_read(args.policies))
                if generic:
                    from agenttest.task_contract import TaskContract, task_validator

                    task = TaskContract.model_validate_json(bounded_read(args.task_contract))
                    if task.user_task != args.user_task:
                        raise ValueError("Task text differs from operator task contract")
                    validator = task_validator(task, load_openapi(document), config, registry)
            if args.real:
                settings = (ModelSettings.from_env_file(args.model_env) if args.model_env
                            else ModelSettings.from_environment())
                settings = ModelSettings.model_validate({**settings.model_dump(),
                    "timeout": args.model_timeout, "max_output_tokens": args.max_output_tokens,
                    "max_total_tokens": args.max_total_tokens})
                planner = OpenAICompatiblePlanner(
                    settings, profile="generic" if generic else "mini")
                budget = WorkflowBudget(model_timeout=settings.timeout + 1,
                                        workflow_timeout=min(settings.timeout + 90, 300))
            else:
                planner = FakeLLM(bounded_read(args.fake_plan).decode("utf-8"))
            if args.command == "agent" and args.export_regression:
                from agenttest.regression import RegressionCapture

                capture = RegressionCapture(planner)
                planner = capture
            if args.command == "agent-demo":
                from agenttest.natural_demo import run_natural_demo

                result = asyncio.run(run_natural_demo(args.user_task, planner, store,
                                                     fault=args.fault, budget=budget))
            else:
                result = asyncio.run(Workflow(
                    document, config, registry, planner, store,
                    budget=budget, user_task=args.user_task, intent_validator=validator).run())
                if capture is not None:
                    if result.outcome != Outcome.PASS:
                        regression_result = {"exported": False, "reason": "SOURCE_NOT_PASSED"}
                    else:
                        from agenttest.regression import RegressionRuntime, export_regression

                        try:
                            runtime = RegressionRuntime(document, config, registry, task,
                                                        dict(os.environ), None, store)
                            export_regression(args.export_regression, runtime,
                                              capture.scenario, result)
                            regression_result = {"exported": True,
                                "directory": str(args.export_regression.absolute())}
                        except (ValueError, OSError, AttributeError):
                            regression_result = {"exported": False,
                                                 "reason": "REGRESSION_EXPORT_REJECTED"}
                            regression_error = True
                    capture.scenario = None
        else:
            config = TargetConfig.model_validate_json(bounded_read(args.target))
            registry = PolicyRegistry.from_json(bounded_read(args.policies))
            if args.command == "workflow":
                from agenttest.workflow import FakeLLM, Workflow

                result = asyncio.run(Workflow(
                    bounded_read(args.contract), config, registry,
                    FakeLLM(bounded_read(args.plan).decode("utf-8")), store,
                    user_task=("执行操作员提供的测试计划" if args.user_task is None
                               else args.user_task),
                    task_mode="operator_plan" if args.user_task is None
                    else "natural_language").run())
            else:
                contract = load_openapi(bounded_read(args.contract))
                scenario = Scenario.model_validate_json(bounded_read(args.plan))
                result = asyncio.run(Executor(config, contract, store,
                                              registry=registry).run(scenario))
        print(json.dumps({
            "run_id": result.run_id, "outcome": result.outcome.value,
            "steps": len(result.steps),
            "investigations": len(result.investigations),
            "report": str(store.root / result.run_id / "report.json"),
            **({"regression": regression_result} if regression_result is not None else {}),
        }, ensure_ascii=False))
        if regression_error:
            return 2
        return 0 if result.outcome == Outcome.PASS else 1
    except ImportError:
        print(json.dumps({"outcome": "TOOL_ERROR", "reason": "AGENT_EXTRA_REQUIRED"}))
        return 2
    except (ValueError, OSError):
        print(json.dumps({"outcome": "TOOL_ERROR", "reason": "INVALID_INPUT_OR_REPORT_IO"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
