"""No credentials or live switches: cross-project acceptance with fixed responses."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.portability_demo import run_portability, verify_portability


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线跨项目验收；不调用真实模型")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path)
    modes.add_argument("--verify", type=Path)
    parser.add_argument("--source", choices=["fake", "mock_transport", "both"], default="both")
    args = parser.parse_args(argv)
    try:
        result = (verify_portability(args.verify) if args.verify else asyncio.run(
            run_portability(args.output, ("fake", "mock_transport") if args.source == "both"
                            else (args.source,))))
        print(json.dumps({k: v for k, v in result.items() if k != "results"},
                         ensure_ascii=False, indent=2))
        return 0 if result.get("verified", result.get("archive_verified", False)) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
