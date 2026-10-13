"""Replay fixed plans with real cached retrieval and an in-process target; no model API."""

import argparse
import asyncio
import json
from pathlib import Path

from trial import ReferencePlanner, base, load_index

from agenttest.batch_demo import DEFAULT_BATCH_TASK, manual_batch_draft, run_batch_demo
from agenttest.dsl import ArgumentBinding
from agenttest.report import Outcome, ReportStore
from agenttest.workflow import FakeLLM


async def replay(vectors: Path, protocol: Path, output: Path):
    index, _, _, _, _ = load_index(vectors, protocol)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for enabled in (False, True):
        for fault in ("none", "terminal", "result_count"):
            key = ("C" if enabled else "A") + "-" + fault
            planner = ReferencePlanner(FakeLLM(manual_batch_draft().scenario().model_dump_json()),
                                       index, system="mini", enabled=enabled)
            report = await run_batch_demo(DEFAULT_BATCH_TASK, planner, ReportStore(output / key),
                                          fault=fault)
            expected = Outcome.PASS if fault == "none" else Outcome.ASSERTION_FAILED
            if (report.outcome != expected or not report.intent_verified
                    or report.task_satisfied != (fault == "none")):
                raise AssertionError("Replay boundary changed")
            rows.append({"case": key, "outcome": report.outcome.value,
                         "task_satisfied": report.task_satisfied,
                         "workflow_completed": report.workflow_completed,
                         "tool_calls": report.tool_calls,
                         "retrieved": [note["id"] for note in planner.last_notes]})
    for attack in ("role", "operation", "assertion", "resource", "csv"):
        raw = manual_batch_draft().scenario().model_dump()
        if attack == "role":
            raw["steps"][2]["role"] = "admin"
        elif attack == "operation":
            raw["steps"][2]["operation_id"] = "retryFailed"
        elif attack == "assertion":
            raw["steps"][3]["assertions"] = {
                "fields": [{"select": "$.state", "operator": "equals", "expected": "FAILED"}]}
        elif attack == "resource":
            raw["steps"][2]["path_parameters"]["courseId"] = ArgumentBinding(value=999).model_dump()
        else:
            raw["steps"][2]["upload"]["content"] += "third synthetic row\n"
        planner = ReferencePlanner(FakeLLM(json.dumps(raw)), index, system="mini", enabled=True)
        report = await run_batch_demo(DEFAULT_BATCH_TASK, planner,
                                      ReportStore(output / ("reject-" + attack)))
        if (report.outcome not in {Outcome.NEEDS_REVIEW, Outcome.TOOL_ERROR}
                or report.tool_calls or report.steps or report.task_satisfied):
            raise AssertionError("Untrusted plan acquired execution authority")
        rows.append({"case": "reject-" + attack, "outcome": report.outcome.value,
                     "task_satisfied": report.task_satisfied, "tool_calls": report.tool_calls,
                     "retrieved": [note["id"] for note in planner.last_notes]})
    summary = {"classification": "DEVELOPMENT_ONLY", "review_status": "UNREVIEWED",
               "planner": "FIXED_FAKE_PLAN", "retrieval": "REAL_LOCAL_ONNX_CACHED",
               "target": "IN_PROCESS_MINI_ASGI", "planning_quality_measured": False,
               "LLM_API_calls": 0, "java_requests": 0, "database_writes": 0, "rows": rows}
    base.write_new(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = asyncio.run(replay(args.vectors, args.protocol, args.output))
    print(json.dumps(summary, indent=2))
