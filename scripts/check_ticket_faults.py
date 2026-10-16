"""Offline paired fault checks and cold verification; never calls a real model."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.ticket_fault_check import check_faults, verify_faults


def main(argv=None):
    parser = argparse.ArgumentParser(description="工单同计划配对故障离线验证")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path)
    modes.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_faults(args.verify) if args.verify else asyncio.run(
            check_faults(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("paired_detections") == 3) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_TICKET_FAULT_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
