"""Offline checks and cold verification of the new PocketBase acceptance scope."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.pocketbase_acceptance import check_offline, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description="PocketBase 六条新任务的离线协议验收／归档核对")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--output", type=Path, help="仅运行合成协议与固定回复，不启动 PocketBase")
    modes.add_argument("--verify", type=Path, help="只核对既有归档，不新增调用")
    args = parser.parse_args(argv)
    try:
        result = verify(args.verify) if args.verify else asyncio.run(check_offline(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("archive_verified", result.get("trial_successes") == 18
                               and result.get("baseline_successes") == 6) else 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({"reason": "INVALID_POCKETBASE_INPUT_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
