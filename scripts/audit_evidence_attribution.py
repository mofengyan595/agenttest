"""Cold observation audit; never infer a proposal from the report's default finding."""

import argparse
import json
from pathlib import Path

from agenttest.evaluation import save_evaluation
from agenttest.evidence_attribution import audit_attribution

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_attribution(args.archive)
    save_evaluation(result, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2))
