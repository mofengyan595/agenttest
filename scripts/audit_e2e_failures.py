"""Audit sealed natural-language failures without models or target execution."""

import argparse
import json
from pathlib import Path

from agenttest.e2e_audit import audit_e2e

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_e2e(args.archive)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)
    print(json.dumps(result["verification"]))
