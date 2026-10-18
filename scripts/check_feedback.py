"""Opt-in real development validation; all business requests remain disposable ASGI."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.feedback_check import check_feedback, verify_feedback
from agenttest.llm_planner import ModelSettings


def main(argv=None):
    parser = argparse.ArgumentParser(description="有界只读反馈闭环开发验收")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--typed-inputs", action="store_true", help="使用全新的字面量类型补验任务")
    args = parser.parse_args(argv)
    if args.verify and args.model_env:
        parser.error("Cold verification never calls models")
    if args.verify and args.typed_inputs:
        parser.error("Cold verification reads the frozen task set")
    try:
        result = verify_feedback(args.verify) if args.verify else asyncio.run(check_feedback(
            args.output, ModelSettings.from_env_file(args.model_env) if args.model_env else None,
            typed_inputs=args.typed_inputs))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("business_correct") == 3
                               and result.get("finding_correct") == 3) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_FEEDBACK_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
