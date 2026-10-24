"""Bounded development comparison; all requests use the original trusted executor.

Schemathesis supplies data, never a transport, credentials, URLs or execution rights.
The shared subset uses the same nine approved policies; mutation is rule work in
every arm. Native negative fuzz data is recorded separately, never silently given
permission by the generator. No scoring labels or fixture implementation enter arms.
"""

import argparse
import asyncio
import importlib.metadata
import json
import time
from pathlib import Path

import httpx

from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.goal_runtime import run_goal
from agenttest.goal_suite import BudgetTransport, SuiteLedger, build_suite
from agenttest.negative_demo import create_negative_target
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.project_goals import load_goal_project
from agenttest.report import ReportStore, fingerprint

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples/trusted-testing/development/validation/registered.project.json"
POLICIES = ("validation_normal", "missing_message", "wrong_message_type",
            "wrong_category_type", "outside_category", "below_count", "above_count",
            "short_message", "long_message")


def data_evidence(value):
    """Retain non-JSON generator values explicitly, without repairing test inputs."""
    if isinstance(value, bytes):
        return dict(python_type="bytes", hex=value.hex())
    if isinstance(value, dict):
        return {k: data_evidence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [data_evidence(v) for v in value]
    return value


def fixed_plan(policy, body=None):
    """Handwritten baseline fixture, including all known required input values."""
    body = dict(message="manual", category="a", count=2) if body is None else body
    return Scenario.model_validate(dict(name="common approved contract checks", steps=[dict(
        id="create", operation_id="validateRecord", policy_id=policy,
        body={k: dict(value=v) for k, v in body.items()})]))


def generated_data(document, count=9, *, operation_path="/validation-records", method="POST"):
    """Pure bounded generation: no calls, feedback, shrinking, state machine or replay."""
    import schemathesis
    from hypothesis import Phase, given, settings
    from schemathesis import GenerationMode

    schema = schemathesis.openapi.from_dict(json.loads(document))
    operation = schema[operation_path][method]
    collected = {}
    for label, mode in (("positive", GenerationMode.POSITIVE),
                        ("negative", GenerationMode.NEGATIVE)):
        rows = []

        @settings(max_examples=count, phases=[Phase.generate], database=None,
                  derandomize=True, deadline=None)
        @given(operation.as_strategy(generation_mode=mode))
        def collect(case):
            rows.append(case.body)

        collect()
        if len(rows) != count:
            raise ValueError("GENERATION_DID_NOT_FILL_FROZEN_SLOTS")
        collected[label] = rows
    return collected


async def compare(output, *, faults=("none", "accept_invalid", "wrong_error"),
                  include_schemathesis=True):
    project = load_goal_project(PROJECT)
    text = "测试创建记录接口"
    suite = build_suite(project, text)
    by_policy = {c.goal.task.requirements.steps[-1].policy_id: c
                 for c in suite.cases if c.goal and c.goal.task}
    bodies = generated_data(project.document) if include_schemathesis else None
    rows = []
    arms = ("fixed_pytest", "agenttest_mock") + (("schemathesis",) if bodies else ())
    for fault in faults:
        for arm in arms:
            started = time.perf_counter()
            # Harness selects/reset target outside arm inputs. Every arm gets a fresh app.
            ledger = SuiteLedger(max_requests=9, max_model_calls=9, max_total_tokens=500000)
            transport = BudgetTransport(httpx.ASGITransport(app=create_negative_target(fault)),
                                        ledger)
            store = ReportStore(output / fault / arm)
            planner = CatalogFixturePlanner()
            cases = []
            for index, policy in enumerate(POLICIES):
                if arm == "agenttest_mock":
                    _, report = await run_goal(project, text, planner, store, environ={},
                        transport=transport, derived=by_policy[policy].goal)
                else:
                    body = bodies["positive"][index] if arm == "schemathesis" else None
                    report = await Executor(project.target, project.contract, store, transport,
                        {}, project.registry).run(fixed_plan(policy, body))
                status = (report.steps[0].attempts[0].status_code
                          if report.steps and report.steps[0].attempts else None)
                cases.append(dict(policy=policy, run_id=report.run_id,
                    executed=status is not None,
                    diagnostic=report.diagnostic.code if report.diagnostic else report.reason,
                    schema_check_passed=(status == (201 if index == 0 else 422)),
                    business_check_passed=report.outcome.value == "PASS",
                    outcome=report.outcome.value,
                    model_calls=report.model_call.calls if report.model_call else 0))
            rows.append(dict(arm=arm, fault=fault, request_budget=9,
                http_requests=ledger.requests, cases=cases,
                schema_checks_passed=sum(c["schema_check_passed"] for c in cases),
                business_checks_passed=sum(c["business_check_passed"] for c in cases),
                model_calls=sum(c["model_calls"] for c in cases),
                elapsed_seconds=time.perf_counter() - started,
                natural_language_denominator=9 if arm == "agenttest_mock" else None,
                natural_language_result="SIMULATED_ONLY" if arm == "agenttest_mock" else
                    "OUTSIDE_DECLARED_CAPABILITY"))
    return dict(schema_version="agenttest.mature-tools.development.v1",
        classification="DEVELOPMENT_ONLY/UNREVIEWED", real_model_calls=0,
        project_identity=project.identity(), contract_sha256=fingerprint(project.document.hex()),
        common_policy_denominator=9, rows=rows,
        schemathesis_version=importlib.metadata.version("schemathesis") if bodies else None,
        raw_generation=data_evidence(bodies),
        raw_negative_execution_count=0,
        negative_generation_scope="Exploratory only; broad fuzz data is not fixed-policy approval",
        mutation_contribution="AgentTest approved deterministic rules in ALL arms",
        reset="fresh stateless ASGI app per arm/fault; no real DB/network",
        preparation=dict(fixed_pytest="9 explicit cases, values and assertions; one fixture",
            schemathesis="public schema + generation settings + approved-policy adapter; "
                "no steps for this stateless endpoint",
            agenttest="approved project files + original text; fixture model is simulated"),
        human_time_seconds=None, independent_human_review=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--without-schemathesis", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(compare(args.output,
        include_schemathesis=not args.without_schemathesis))
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n",
                                            encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"rows", "raw_generation"}}))
