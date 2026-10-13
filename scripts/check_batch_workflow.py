"""Offline batch diagnostics, exclusive output, and read-only archive verification."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.batch_diagnostics import run_batch_diagnostics, verify_batch_archive


def main():
    parser = argparse.ArgumentParser(description='DEVELOPMENT_ONLY batch offline verification')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--output', type=Path, help='New directory; existing output is refused')
    group.add_argument('--verify', type=Path, help='Verify existing archive without executing')
    args = parser.parse_args()
    result = (verify_batch_archive(args.verify) if args.verify
              else asyncio.run(run_batch_diagnostics(args.output)))
    print(json.dumps({key: result[key] for key in (
        'classification', 'review_status', 'checks_verified', 'eval_v2_intent_score',
        'real_model_calls', 'java_requests', 'database_writes')}, ensure_ascii=False, indent=2))
    return 0 if result['checks_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
