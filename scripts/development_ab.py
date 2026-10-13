"""Private DEVELOPMENT_ONLY A/B entrypoint; prepare is offline and never authorizes run."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import (
    DevelopmentConfig,
    development_suite,
    model_settings,
    pair_inputs,
    private_output,
    run_development_ab,
)
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.models import Suite
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.llm_planner import ModelSettings


def main(argv=None):
    parser = argparse.ArgumentParser(description="DEVELOPMENT_ONLY mini-target A/B diagnostics")
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", choices=["fake", "mock_transport", "real"],
                        default="mock_transport")
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--allow-model")
    parser.add_argument("--allow-endpoint")
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--max-total-tokens", type=int, default=88_192)
    args = parser.parse_args(argv)
    try:
        suite = Suite.load(args.suite)
        if len(set(args.case)) != len(args.case) or not set(args.case) <= {
            c.case_id for c in suite.cases
        }:
            raise ValueError("Duplicate or unknown selected Case")
        suite = development_suite(Suite.model_validate({**suite.model_dump(),
            "cases": [next(c.model_dump() for c in suite.cases if c.case_id == key)
                      for key in args.case]}))
        private_output(PROJECT_ROOT, args.output)
        if args.command == "prepare":
            if args.real:
                raise ValueError("Preparation cannot authorize live execution")
            config = DevelopmentConfig(max_calls=args.max_calls,
                                       max_total_tokens=args.max_total_tokens)
            settings = ModelSettings.from_env_file(args.model_env) if args.model_env else None
            if settings is not None:
                settings = model_settings(DevelopmentConfig(source="real", real_opt_in=True,
                    allowed_model=settings.model, allowed_endpoint=settings.base_url), settings)
            inputs = pair_inputs(suite, config)
            args.output.mkdir(parents=True, exist_ok=False)
            result = {"classification": "DEVELOPMENT_ONLY", "operator_authorized": False,
                "ready_for_paid_run": False, "real_api_calls": 0,
                "cases": args.case, "git": checkout_identity(PROJECT_ROOT),
                "model_config": public_model_config(settings) if settings else None,
                "endpoint": settings.base_url if settings else None,
                "config": config.model_dump(mode="json"),
                "per_call": {"input_tokens": 20_000, "output_tokens": 2048,
                    "token_reservation": 22_048, "model_timeout_seconds": 30,
                    "workflow_timeout_seconds": 120, "retries": 0},
                "combined_ceiling": {"model_calls": config.max_calls,
                    "token_reservation": config.max_total_tokens,
                    "business_http_requests": 2 * len(suite.cases) * 40},
                "remaining_conditions": ["Explicit operator authorization for new A+B calls",
                    "New output directory", "Recheck source/configuration before execution"],
                "inputs": {v: inputs[v][2] for v in inputs}}
            for variant in inputs:
                save_evaluation(inputs[variant][1], args.output / (variant + "-contexts.json"))
            save_evaluation(suite.model_dump(mode="json"), args.output / "suite.json")
            save_evaluation(result, args.output / "preparation.json")
        else:
            if args.source != "real" and (args.real or args.model_env is not None):
                raise ValueError("Offline mode cannot accept live opt-in or credentials")
            config = DevelopmentConfig(source=args.source, real_opt_in=args.real,
                allowed_model=args.allow_model, allowed_endpoint=args.allow_endpoint,
                max_calls=args.max_calls, max_total_tokens=args.max_total_tokens)
            settings = None
            if args.source == "real":
                if args.model_env is None:
                    raise ValueError("Explicit local model environment required")
                settings = ModelSettings.from_env_file(args.model_env)
            result = asyncio.run(run_development_ab(suite, config, args.output,
                root=PROJECT_ROOT, suite_path=args.suite, responses_path=args.responses,
                settings=settings))
        print(json.dumps({"classification": "DEVELOPMENT_ONLY", "output": str(args.output),
                          "batch_ledger": result.get("batch_ledger"),
                          "ready_for_paid_run": False if args.command == "prepare" else None}))
        return 1 if result.get("batch_ledger", {}).get("stop") else 0
    except (ValueError, OSError, ImportError):
        print(json.dumps({"classification": "DEVELOPMENT_ONLY", "reason": "INVALID_CONFIGURATION"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
