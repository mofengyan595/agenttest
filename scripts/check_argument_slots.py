"""Offline by default; explicit model settings opt into the frozen nine-call development run."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.argument_slot_check import check_slots, verify_slots
from agenttest.llm_planner import ModelSettings


def main(argv=None):
    parser = argparse.ArgumentParser(description="新任务参数名约束验收，业务请求始终为 Mock")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path)
    modes.add_argument("--verify", type=Path)
    parser.add_argument("--model-env", type=Path, help="显式启用最多九次 ecnu-plus，零重试")
    parser.add_argument("--references", action="store_true", help="使用全新的引用字段开发任务")
    parser.add_argument("--diagnostics", action="store_true", help="使用新的引用诊断开发任务")
    args = parser.parse_args(argv)
    if args.verify and args.model_env:
        parser.error("Cold verification never calls a model")
    if args.references and args.diagnostics:
        parser.error("Choose one development set")
    if args.verify and (args.references or args.diagnostics):
        parser.error("Cold verification reads its frozen task set")
    try:
        settings = (ModelSettings.from_env_file(args.model_env).model_copy(update={"timeout": 60})
                    if args.model_env else None)
        result = verify_slots(args.verify) if args.verify else asyncio.run(
            check_slots(args.output, settings, references=args.references,
                        diagnostics=args.diagnostics))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("generation_successes") == 9) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_SLOT_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
