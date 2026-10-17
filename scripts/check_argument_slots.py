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
    args = parser.parse_args(argv)
    if args.verify and args.model_env:
        parser.error("Cold verification never calls a model")
    try:
        settings = (ModelSettings.from_env_file(args.model_env).model_copy(update={"timeout": 60})
                    if args.model_env else None)
        result = verify_slots(args.verify) if args.verify else asyncio.run(
            check_slots(args.output, settings))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("generation_successes") == 9) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_SLOT_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
