"""Offline structured literal checks and cold verification; no live-model switch."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.json_literal_check import check_literals, verify_literals


def main(argv=None):
    parser = argparse.ArgumentParser(description="嵌套 JSON、数组和小数字面量离线验证")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path)
    modes.add_argument("--verify", type=Path)
    parser.add_argument("--repair", action="store_true", help="使用两条新的来源约束开发任务")
    args = parser.parse_args(argv)
    if args.verify and args.repair:
        parser.error("--verify reads its frozen task scope; --repair is for new offline runs")
    try:
        result = verify_literals(args.verify) if args.verify else asyncio.run(
            check_literals(args.output, args.repair))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("paired_detections") ==
                               result.get("scheduled_tasks")) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_JSON_LITERAL_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
