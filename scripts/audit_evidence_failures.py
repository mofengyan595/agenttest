"""Cold attribution of sealed historical records, with no target/model invocation."""

import argparse
import json
from pathlib import Path

from agenttest.evaluation import save_evaluation
from agenttest.evidence_audit import audit_failures

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a fresh output file; preserve prior evidence")
    result = audit_failures(args.archive)
    save_evaluation(result, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2))
