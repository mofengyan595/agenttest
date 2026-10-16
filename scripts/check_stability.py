"""Three-round fixed-reply acceptance and cold verification; never makes real calls."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.stability_check import check_stability, verify_stability


def main(argv=None):
    parser = argparse.ArgumentParser(description="三轮离线稳定性验收；不调用真实模型或 Java")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path)
    modes.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (verify_stability(args.verify) if args.verify else
                  asyncio.run(check_stability(args.output)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("successes") == 12) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_STABILITY_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
