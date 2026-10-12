"""Agent Eval v2: offline by default, independent holdout required for live calls."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.audit import rescore
from agenttest.eval_v2.comparison import compare
from agenttest.eval_v2.models import Suite
from agenttest.eval_v2.protocol import prepare_protocol
from agenttest.eval_v2.review import freeze_review, review_template, verify_review_lock
from agenttest.eval_v2.runner import EvalConfig, evaluate, prepare_inputs
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import ModelSettings
from agenttest.report import fingerprint


def run(args):
    if args.command == "review-template":
        args.output.mkdir(parents=True, exist_ok=False)
        save_evaluation(review_template(), args.output / "enrollment.json")
        return {"review_status": "UNREVIEWED", "output": str(args.output)}
    if args.command == "compare":
        if args.a is None or args.b is None:
            raise ValueError("A/B directories required")
        result = compare(args.a, args.b)
        args.output.mkdir(parents=True, exist_ok=False)
        save_evaluation(result, args.output / "comparison.json")
        return result
    suite = Suite.load(args.suite)
    if args.command == "review-freeze":
        return freeze_review(suite, args.output)
    if args.command == "protocol":
        result = prepare_protocol(
            suite, PROJECT_ROOT, review_lock=args.review_lock, model=args.allow_model,
            endpoint=args.allow_endpoint, max_calls=args.max_calls,
            max_total_tokens=args.max_total_tokens, max_input_tokens=args.max_input_tokens,
            max_output_tokens=args.max_output_tokens, timeout=args.timeout,
            max_http_requests=args.max_http_requests,
            max_workflow_seconds=args.max_workflow_seconds,
            candidate_max_bytes=args.candidate_max_bytes,
        )
        args.output.mkdir(parents=True, exist_ok=False)
        save_evaluation(result, args.output / "protocol.json")
        return result
    if args.command == "rescore":
        if args.run_dir is None:
            raise ValueError("Archived run directory required")
        result = rescore(suite, args.run_dir)
        args.output.mkdir(parents=True, exist_ok=False)
        save_evaluation(result, args.output / "rescored.json")
        return result
    if args.command == "validate":
        if args.formal:
            verify_review_lock(suite, args.review_lock)
        return {
            "valid": True,
            "dataset_version": suite.dataset_version,
            "cases": len(suite.cases),
            "human_reviewed": sum(c.labels.review_status == "HUMAN_REVIEWED" for c in suite.cases),
            "supported": sum(c.labels.expected_behavior == "execute" for c in suite.cases),
            "rejection_drafts": sum(c.labels.expected_behavior == "reject" for c in suite.cases),
            "review_or_future": sum(c.labels.expected_behavior == "review" for c in suite.cases),
        }
    if args.command == "contexts":
        # Separate A/B construction without running a planner or Executor.
        config = EvalConfig(
            development=True, variant="B", candidate_max_bytes=args.candidate_max_bytes
        )
        base, candidates, inputs, _ = prepare_inputs(suite, config)
        args.output.mkdir(parents=True, exist_ok=False)
        save_evaluation(
            {
                "A": base,
                "B": candidates,
                "inputs": inputs,
                "model_calls": 0,
                "business_http_requests": 0,
            },
            args.output / "contexts.json",
        )
        return {"cases": len(suite.cases), "model_calls": 0, "output": str(args.output)}
    config = EvalConfig(
        source=args.source,
        variant=args.variant,
        development=args.development,
        candidate_max_bytes=args.candidate_max_bytes,
        max_cases_attempted=args.max_cases_attempted,
        real_opt_in=args.real,
        max_calls=args.max_calls,
        max_total_tokens=args.max_total_tokens,
        allowed_model=args.allow_model,
        allowed_endpoint_sha256=fingerprint(args.allow_endpoint.rstrip("/"))
        if args.allow_endpoint
        else None,
        max_http_requests=args.max_http_requests,
        max_workflow_seconds=args.max_workflow_seconds,
        max_tool_calls=args.max_tool_calls,
    )
    settings = None
    if args.source == "real":
        settings = (
            ModelSettings.from_env_file(args.model_env)
            if args.model_env
            else ModelSettings.from_environment()
        )
        settings = ModelSettings.model_validate(
            {
                **settings.model_dump(),
                "max_input_tokens": args.max_input_tokens,
                "max_output_tokens": args.max_output_tokens,
                "max_total_tokens": args.max_input_tokens + args.max_output_tokens,
                "timeout": args.timeout,
            }
        )
    result = asyncio.run(
        evaluate(
            suite,
            config,
            args.output,
            root=PROJECT_ROOT,
            responses_path=args.responses,
            suite_path=args.suite,
            settings=settings,
            review_lock_path=args.review_lock,
        )
    )
    return {key: value for key, value in result.items() if key not in {"results", "pairs"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "contexts", "run", "compare", "rescore",
                                          "review-template", "review-freeze", "protocol"])
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--a", type=Path)
    parser.add_argument("--b", type=Path)
    parser.add_argument(
        "--suite", type=Path, default=PROJECT_ROOT / "examples/eval-v2/c-drafts.json"
    )
    parser.add_argument("--formal", action="store_true", help="校验独立人工审核留出集")
    parser.add_argument("--review-lock", type=Path)
    parser.add_argument("--development", action="store_true", help="仅开发验证，不能称为独立评测")
    parser.add_argument(
        "--source", choices=["manual", "fake", "mock_transport", "real"], default="manual"
    )
    parser.add_argument("--variant", choices=["A", "B"], default="A")
    parser.add_argument("--candidate-max-bytes", type=int, default=6000)
    parser.add_argument("--max-cases-attempted", type=int, default=200)
    parser.add_argument("--responses", type=Path, help="固定 Fake DSL 或 Mock 模型响应映射")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--allow-model")
    parser.add_argument("--allow-endpoint")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--max-total-tokens", type=int, default=0)
    parser.add_argument("--max-input-tokens", type=int, default=20000)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-http-requests", type=int, default=40)
    parser.add_argument("--max-workflow-seconds", type=float, default=90.0)
    parser.add_argument("--max-tool-calls", type=int, default=1)
    args = parser.parse_args()
    if args.command != "validate" and args.output is None:
        parser.error("--output is required")
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("stop_reason") or result.get("audit_status") == (
            "UNVERIFIABLE_LEGACY_OR_INCOMPLETE_ARCHIVE"
        ) else 0
    except (ValueError, OSError, ImportError):
        print(
            json.dumps(
                {
                    "classification": "EVALUATION_CONFIGURATION_ERROR",
                    "code": "EVAL_PREFLIGHT_OR_IO_FAILED",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
