"""Requires operator permission for a fresh, local PocketBase SQLite environment."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.pocketbase_live import execute


def main(argv=None):
    parser = argparse.ArgumentParser(description="经批准的 PocketBase 隔离写入与真实模型验收")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--model-env", type=Path, default=Path(".env"))
    parser.add_argument("--isolated-approved", action="store_true",
                        help="仅在操作员已明确批准本轮隔离数据库写入后使用")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(execute(args.output.absolute(), args.archive, args.model_env,
                                    isolated_approved=args.isolated_approved))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if (result["baseline_successes"] == 6 and result["trial_successes"] == 18
                     and result["native_exit_confirmed"] and result["stop_reason"] is None) else 1
    except (ValueError, OSError):
        print(json.dumps({"reason": "POCKETBASE_LIVE_PROPOSAL_REJECTED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
