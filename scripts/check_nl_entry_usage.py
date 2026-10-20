"""Execute the single frozen first demand, or cold verify its original evidence."""

import argparse
import json
from pathlib import Path

from agenttest.nl_entry_usage import run_novel, verify_novel

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--evaluation-archive", type=Path)
    parser.add_argument("--model-env", type=Path)
    args = parser.parse_args()
    result = (verify_novel(args.verify) if args.verify else run_novel(args.output,
        args.evaluation_archive, args.model_env, Path("examples/nl-entry/new-demand.json")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
