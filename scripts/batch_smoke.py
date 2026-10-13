"""Prepare a single approved batch smoke offline; --real requires operator authorization."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.batch_smoke import execute_smoke, prepare_smoke


def main(argv=None):
    parser = argparse.ArgumentParser(description='DEVELOPMENT_ONLY single batch smoke')
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare', help='No model/HTTP calls; freeze task and budget')
    run = sub.add_parser('run', help='One paid call, only after separate operator approval')
    run.add_argument('--real', action='store_true', required=True)
    run.add_argument('--prepared', type=Path, required=True)
    for command in (prepare, run):
        command.add_argument('--model-env', type=Path, default=Path('.env'))
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (prepare_smoke(args.model_env, args.output) if args.command == 'prepare' else
                  asyncio.run(execute_smoke(args.model_env, args.prepared, args.output,
                                           real_opt_in=args.real)))
    except (ValueError, OSError):
        print('BATCH_SMOKE_PREFLIGHT_OR_OUTPUT_REJECTED')
        return 2
    public = {key: result[key] for key in ('classification', 'reserved_tokens',
              'real_model_calls', 'java_requests', 'database_writes', 'eval_v2_intent_score')}
    if args.command == 'prepare':
        public.update(input_token_upper_bound=result['input_token_upper_bound'],
                      model=result['model_config']['model'], protocol='chatecnu',
                      endpoint='https://chat.ecnu.edu.cn/open/api/v1/chat/completions')
    else:
        public.update(runtime_outcome=result['runtime_outcome'],
                      task_satisfied=result['task_satisfied'])
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0 if args.command == 'prepare' or (
        result['task_satisfied'] and result['freeze_valid']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
