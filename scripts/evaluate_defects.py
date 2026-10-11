"""Explicit serial model evaluation on disposable in-process targets only."""

import argparse
import asyncio
import json
from pathlib import Path

from agenttest.defect_evaluation import TASKS, evaluate
from agenttest.demo import PROJECT_ROOT
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import _git, public_model_config
from agenttest.llm_planner import SYSTEM_PROMPT, ModelSettings, OpenAICompatiblePlanner
from agenttest.report import ReportStore, fingerprint


async def run(args):
    directory = args.output
    # Exclusive evidence directory claims this whole suite before any model request.
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"baseline": args.baseline, "suite_sha256": fingerprint(TASKS.read_bytes().hex()),
                "prompt_sha256": fingerprint(SYSTEM_PROMPT), "serial": True, "retries": 0,
                "code_commit": _git(PROJECT_ROOT, "rev-parse", "HEAD"),
                "school_calls_budget": args.max_calls if args.baseline == "real" else 0,
                "total_tokens_budget": args.max_tokens if args.baseline == "real" else 0,
                "java_requests": 0, "target": "fresh in-process mini target per run"}
    planner = None
    if args.baseline == "real":
        if not args.real:
            raise ValueError("Live calls require --real")
        if _git(PROJECT_ROOT, "status", "--porcelain", "--untracked-files=normal"):
            raise ValueError("Live evaluation requires clean committed code")
        settings = ModelSettings.from_env_file(args.model_env).model_copy(update={
            "timeout": 60.0, "max_output_tokens": 2048, "max_total_tokens": 22048})
        if settings.protocol != "chatecnu":
            raise ValueError("School endpoint required")
        manifest["model_config"] = public_model_config(settings)
        planner = OpenAICompatiblePlanner(settings)
    save_evaluation(manifest, directory / "manifest.json")

    def check_freeze():
        if (_git(PROJECT_ROOT, "rev-parse", "HEAD") != manifest["code_commit"]
                or _git(PROJECT_ROOT, "status", "--porcelain", "--untracked-files=normal")
                or fingerprint(SYSTEM_PROMPT) != manifest["prompt_sha256"]
                or fingerprint(TASKS.read_bytes().hex()) != manifest["suite_sha256"]):
            raise ValueError("Evaluation freeze changed")

    def record(row):
        # Each redacted row survives later suite failure; never persist the generated DSL.
        with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        if row["kind"] == "task":
            print(json.dumps({k: row[k] for k in ("task_id", "outcome", "reason",
                                                 "requests", "intent_verified")}), flush=True)

    result = await evaluate(args.baseline, ReportStore(directory / "reports"), planner=planner,
                            max_calls=args.max_calls, max_tokens=args.max_tokens,
                            before_call=check_freeze, record=record)
    save_evaluation(result, directory / "results.json")
    print(json.dumps({k: v for k, v in result.items() if k not in
                      {"tasks", "pairs", "async_pairs"}}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", choices=["manual", "fake", "real"], default="manual")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--model-env", type=Path, default=Path(".env"))
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=90000)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
        return 0
    except (ValueError, OSError):
        print(json.dumps({"outcome": "BLOCKED", "code": "EVALUATION_PREFLIGHT_OR_IO_ERROR"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
