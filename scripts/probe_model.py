"""Explicit zero-generation model discovery; prints only a redacted readiness result."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.llm_planner import ModelSettings
from agenttest.model_probe import check_connection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-env", type=Path)
    args = parser.parse_args()
    try:
        settings = (ModelSettings.from_env_file(args.model_env) if args.model_env
                    else ModelSettings.from_environment())
        result = asyncio.run(check_connection(settings))
        print(json.dumps(result))
        return 0 if result["reason"] == "READY" else 1
    except (ValueError, OSError):
        print(json.dumps({"reason": "INVALID_MODEL_CONFIGURATION"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
