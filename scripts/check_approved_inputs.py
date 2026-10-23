"""Offline stage-one demonstration; fixed Fake proposal, ASGI target, original export template."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import Scenario
from agenttest.goal_runtime import run_goal
from agenttest.goal_suite import build_suite
from agenttest.negative_demo import create_negative_target
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionRuntime, export_regression
from agenttest.report import Outcome, ReportStore
from agenttest.workflow import FakeLLM


async def check(output, export):
    project = load_goal_project(PROJECT_ROOT /
        "examples/trusted-testing/development/validation/registered.project.json")
    text = "测试创建接口"
    suite = build_suite(project, text, environ={})
    rows = []
    for name, policy, fault in [("normal", "validation_normal", "none"),
        ("negative", "missing_message", "none"),
        ("acceptance_fault", "missing_message", "accept_invalid"),
        ("error_fault", "missing_message", "wrong_error")]:
        goal = next(c.goal for c in suite.cases if c.goal and c.goal.task
                    and c.goal.task.requirements.steps[-1].policy_id == policy)
        # Operator-authored development proposal, never a product planner or evaluation answer.
        values = dict(message="aa", category="a", count=1)
        plan = Scenario.model_validate(dict(name="fixed development proposal", steps=[dict(
            id="create", operation_id="validateRecord", policy_id=policy,
            body={k: dict(value=v) for k, v in values.items()})]))
        # The group may choose a distinct valid scalar witness; use its approved defaults.
        for item in goal.input_defaults:
            plan.steps[0].body[item["name"]].value = item["value"]
        store = ReportStore(output / name)
        transport = httpx.ASGITransport(app=create_negative_target(fault))
        _, report = await run_goal(project, text, FakeLLM(plan.model_dump_json()),
            store, environ={}, transport=transport, derived=goal)
        expected = Outcome.PASS if fault == "none" else Outcome.ASSERTION_FAILED
        if report.outcome != expected or not report.intent_verified:
            raise AssertionError((name, report.outcome, report.reason, report.diagnostic))
        if export and fault == "none":
            export_regression(export / name, RegressionRuntime(project.document, project.target,
                project.registry, goal.task, {}, None, store), plan, report)
        rows.append(dict(case=name, outcome=report.outcome.value, run_id=report.run_id,
                         input_validation=report.steps[0].input_validation,
                         http_requests=sum(len(s.attempts) for s in report.steps)))
    summary = dict(classification="DEVELOPMENT_ONLY", independent_human_reviewed=False,
        model="FIXED_FAKE", real_model_calls=0, external_business_requests=0, rows=rows)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
    print(json.dumps(asyncio.run(check(args.output, args.export)), ensure_ascii=False))
