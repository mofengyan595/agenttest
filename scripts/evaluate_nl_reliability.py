"""Run the frozen new-task comparison, or cold-verify its original sealed artifacts."""

import argparse
import json
from pathlib import Path

from agenttest.nl_evaluation import run_nl, verify_nl

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--split", choices=["development", "validation"])
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--mock-acceptance", type=Path)
    parser.add_argument("--prior-phase", type=Path)
    args = parser.parse_args()
    if args.output and not args.split:
        parser.error("--output requires --split")
    result = verify_nl(args.verify) if args.verify else run_nl(args.output, args.split,
        model_env=args.model_env, mock_acceptance=args.mock_acceptance,
        prior_phase=args.prior_phase)
    print(json.dumps(result, ensure_ascii=False, indent=2))
