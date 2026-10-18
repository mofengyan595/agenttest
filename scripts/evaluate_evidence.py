"""Fixed development/validation/E2E schedule; --verify is always cold and offline."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.evidence_evaluation import run_evidence, verify_evidence
from agenttest.llm_planner import ModelSettings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--split", choices=["development", "validation", "end_to_end"])
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--mock-acceptance", type=Path)
    parser.add_argument("--prior-phase", type=Path)
    args = parser.parse_args()
    if args.verify and (args.model_env or args.split or args.mock_acceptance or args.prior_phase):
        parser.error("Cold verification has no model or task selection")
    if args.output and not args.split:
        parser.error("A frozen split is required")
    result = verify_evidence(args.verify) if args.verify else asyncio.run(run_evidence(
        args.output, args.split, ModelSettings.from_env_file(args.model_env)
        if args.model_env else None, mock_acceptance=args.mock_acceptance,
        prior_phase=args.prior_phase))
    print(json.dumps(result, ensure_ascii=False, indent=2))
