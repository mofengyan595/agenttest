"""Offline metadata acceptance and cold verification; no real-call mode."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.planning_metadata_check import check_metadata, verify_metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="策略用途与鉴权元数据离线验收")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path)
    modes.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_metadata(args.verify) if args.verify else asyncio.run(
            check_metadata(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("successes") == 8) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_METADATA_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
