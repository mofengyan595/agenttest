"""Freeze new public demands or cold-audit their separate labels and artifacts."""

import argparse
import json
from pathlib import Path

from agenttest.nl_entry_evaluation import run_requests, verify_requests

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--mock-archive", type=Path)
    args = parser.parse_args()
    result = (
        verify_requests(args.verify)
        if args.verify
        else run_requests(
            args.output,
            Path("examples/nl-entry/new-requests.json"),
            Path("examples/nl-entry/grading-labels.json"),
            model_env=args.model_env,
            mock_archive=args.mock_archive,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
