"""Fixed 24-task three-round development schedule, optional operator-configured real models."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.llm_planner import ModelSettings
from agenttest.paired_evaluation import run_paired, verify_paired


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--unattempted-from", type=Path,
                        help="仅从封印且用量已知的结构失败归档安排从未执行的位置")
    args = parser.parse_args()
    if args.verify and (args.model_env or args.unattempted_from):
        parser.error("Cold verification never calls models")
    result = verify_paired(args.verify) if args.verify else asyncio.run(run_paired(args.output,
        ModelSettings.from_env_file(args.model_env) if args.model_env else None,
        unattempted_from=args.unattempted_from))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
