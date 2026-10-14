"""Run/verify the separate keyless composition development protocol."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.composed_check import check_composition, verify_composition


def main() -> int:
    parser = argparse.ArgumentParser(description="受控任务组合离线机制验收；固定计划，非模型成绩")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="创建新的归档目录，拒绝覆盖")
    action.add_argument("--verify", type=Path, help="只校验既有归档并重新评分，不执行 HTTP")
    args = parser.parse_args()
    try:
        result = (verify_composition(args.verify) if args.verify else
                  asyncio.run(check_composition(args.output)))
    except (ValueError, OSError, KeyError):
        print("组合验收失败；检查输入、输出目录和归档完整性。")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    count = result.get("cases", result.get("rescored_cases"))
    return 0 if result["mechanism_verified"] == count else 1


if __name__ == "__main__":
    raise SystemExit(main())
