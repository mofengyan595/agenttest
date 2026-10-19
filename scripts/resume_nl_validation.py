"""Operator-authorized evaluation suffix, with immutable original engines and scoring."""

import argparse
import json
from pathlib import Path

from agenttest.nl_resumption import run_resumption, verify_resumption

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--mock-acceptance", type=Path)
    args = parser.parse_args()
    if args.verify:
        if any((args.prior, args.authorization, args.model_env, args.real, args.mock_acceptance)):
            parser.error("Cold verification never starts a model or target")
        result = verify_resumption(args.verify)
    else:
        if not all((args.prior, args.authorization, args.model_env)):
            parser.error("Continuation needs original archive, instruction record and model config")
        result = run_resumption(args.output, args.prior, args.authorization, args.model_env,
            real=args.real, mock_acceptance=args.mock_acceptance)
    print(json.dumps(result, ensure_ascii=False, indent=2))
