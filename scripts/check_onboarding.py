"""Offline two-domain review-draft and configuration-reuse measurements."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.onboarding_check import check_onboarding, verify_onboarding


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线两域接入与任务复用核对，不测人工工时")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_onboarding(args.verify) if args.verify else asyncio.run(
            check_onboarding(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("verified")) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_ONBOARDING_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
