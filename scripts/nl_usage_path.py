"""Review a new demand, then make one CLI attempt; never substitutes a fixed plan."""

import argparse
import json
from pathlib import Path

from agenttest.nl_usage import execute, prepare

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "real"])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--model-env", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.case is None or args.model_env:
            parser.error("prepare requires --case and never reads model credentials")
        result = prepare(json.loads(args.case.read_bytes()), args.output.resolve())
    else:
        if args.model_env is None or args.case:
            parser.error("real requires --model-env and uses previously reviewed inputs")
        result = execute(args.output.resolve(), model_env=args.model_env.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
