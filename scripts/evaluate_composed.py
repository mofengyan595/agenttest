"""Offline preparation first; paid execution needs a new reviewed suite and exact proposal."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.composed_planning import run_planning, verify_planning, write_planning_fixtures
from agenttest.composed_planning_protocol import PlanningConfig, prepare_planning
from agenttest.composed_review import (
    PlanningSuite,
    freeze_planning_review,
    planning_review_packet,
    planning_review_template,
)
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import ModelSettings, PlannerFailure


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="组合任务规划评测；默认仅 Fake/Mock")
    parser.add_argument(
        "command",
        choices=["fixtures", "prepare", "run", "verify", "review-template", "review-freeze"],
    )
    parser.add_argument("--suite", type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument(
        "--source", choices=["fake", "mock_transport", "real"], default="mock_transport"
    )
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--review-lock", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--model-env", type=Path)
    parser.add_argument("--model", default="ecnu-plus")
    parser.add_argument("--endpoint", default="https://chat.ecnu.edu.cn/open/api/v1")
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--max-total-tokens", type=int, default=192_768)
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            if args.run_dir is None:
                raise ValueError("Run directory required")
            result = verify_planning(args.run_dir)
        else:
            if args.output is None:
                raise ValueError("Exclusive private output required")
            private_output(PROJECT_ROOT, args.output)
            if args.command == "review-template":
                result = (
                    planning_review_packet(PlanningSuite.load(args.suite))
                    if args.suite
                    else planning_review_template()
                )
                save_evaluation(result, args.output)
            else:
                if args.suite is None:
                    raise ValueError("Explicit suite required")
                suite = PlanningSuite.load(args.suite)
                if args.case:
                    if len(set(args.case)) != len(args.case) or not set(args.case) <= {
                        c.case_id for c in suite.cases
                    }:
                        raise ValueError("Duplicate or unknown selected cases")
                    suite = PlanningSuite.model_validate(
                        {
                            **suite.model_dump(),
                            "cases": [
                                next(c.model_dump() for c in suite.cases if c.case_id == key)
                                for key in args.case
                            ],
                        }
                    )
                config = PlanningConfig(
                    source=args.source,
                    real_opt_in=args.real,
                    model=args.model,
                    endpoint=args.endpoint,
                    max_calls=args.max_calls,
                    max_total_tokens=args.max_total_tokens,
                )
                if args.command == "fixtures":
                    write_planning_fixtures(suite, args.source, args.output)
                    result = {
                        "source": args.source,
                        "classification": "EXPLICIT_FIXED_FIXTURES",
                        "real_model_calls": 0,
                    }
                elif args.command == "review-freeze":
                    result = freeze_planning_review(suite, args.output)
                elif args.command == "prepare":
                    if args.real or args.model_env is not None:
                        raise ValueError("Preparation never accepts live opt-in or credentials")
                    result = prepare_planning(
                        suite, config, args.output, root=PROJECT_ROOT, review_lock=args.review_lock
                    )
                else:
                    if args.source == "real" and (not args.real or args.model_env is None):
                        raise ValueError("Explicit live opt-in and environment file required")
                    if args.source != "real" and args.model_env is not None:
                        raise ValueError("Offline runs do not accept credentials")
                    settings = (
                        ModelSettings.from_env_file(args.model_env) if args.model_env else None
                    )
                    result = asyncio.run(
                        run_planning(
                            suite,
                            config,
                            args.output,
                            root=PROJECT_ROOT,
                            responses_path=args.responses,
                            suite_path=args.suite,
                            settings=settings,
                            review_lock=args.review_lock,
                            proposal_path=args.proposal,
                        )
                    )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in result.items()
                    if k
                    not in {
                        "results",
                        "requests",
                        "inputs",
                        "git",
                        "review_records_sha256",
                        "case",
                        "used_task_identity_sha256",
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if args.command == "run" and (
            result["ledger"]["stop"]
            or result["supported_success"] != result["supported"]
            or result["planner_safe_rejections"] + result["gate_safe_rejections"]
            != result["rejection_cases"]
        ):
            return 1
        return 0
    except FileExistsError:
        print(json.dumps({"reason": "OUTPUT_EXISTS"}))
        return 2
    except PlannerFailure as exc:
        print(json.dumps({"reason": exc.reason, "diagnostic": exc.diagnostic.code}))
        return 2
    except (ValueError, OSError, KeyError, ImportError):
        # Never expose validation inputs, credential values or raw provider errors.
        print(json.dumps({"reason": "INVALID_CONFIGURATION_OR_ARCHIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
