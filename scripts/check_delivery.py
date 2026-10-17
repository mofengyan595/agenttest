"""One fixed offline acceptance entry; no model, target or command overrides."""

import argparse
import json
from pathlib import Path

from agenttest.delivery import check_delivery


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="统一离线验收：预检、演示、跨项目、JSON 与固定回归")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = check_delivery(args.output)
    except (ValueError, OSError):
        print(json.dumps({"verified": False, "reason": "INVALID_DELIVERY_INPUT_OR_OUTPUT"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
