"""Explicit real loopback HTTP fault injection, never a school/LLM API call."""

import argparse
import asyncio
import json
from pathlib import Path

from smoke_local_http import serve

from agenttest.investigation import FakeAnalyzer
from agenttest.investigation_demo import DEMO_TASK, investigation_inputs
from agenttest.natural_demo import natural_inputs
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig
from agenttest.workflow import FakeLLM, Workflow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, default=Path("runs/investigation-http"))
    args = parser.parse_args()
    summaries = []
    for fault in ("none", "ownership"):
        target, config, registry, environ = natural_inputs(fault)
        calls = []

        @target.middleware("http")
        async def count_calls(request, next_handler):
            calls.append((request.method, request.url.path))
            return await next_handler(request)

        plan, investigation, capabilities = investigation_inputs()
        store = ReportStore(args.runs_dir)
        with serve(target) as url:
            config = TargetConfig.model_validate({**config.model_dump(), "base_url": url})
            report = asyncio.run(Workflow(json.dumps(target.openapi()).encode(), config, registry,
                FakeLLM(plan.model_dump_json()), store, environ=environ, user_task=DEMO_TASK,
                investigation=investigation, capabilities=capabilities,
                analyzer=FakeAnalyzer()).run())
        creates = sum(m == "POST" and p == "/api/courses" for m, p in calls)
        logins = sum(p.endswith("login") for _, p in calls)
        assert creates == 1 and logins == 2
        assert report == store.read(report.run_id) and report.workflow_completed
        assert report.outcome == (Outcome.PASS if fault == "none" else Outcome.ASSERTION_FAILED)
        assert len(calls) == (5 if fault == "none" else 6)
        assert len(report.investigations) == (0 if fault == "none" else 1)
        investigation_result = report.investigations[0] if report.investigations else None
        if investigation_result:
            assert investigation_result.outcome == Outcome.PASS
        summaries.append({"fault": fault, "run_id": report.run_id,
            "original_outcome": report.outcome.value, "creates": creates, "logins": logins,
            "total_requests": len(calls), "investigation_requests": sum(
                len(i.attempts) for i in report.investigations),
            "capability_id": investigation_result.capability_id if investigation_result else None,
            "attempt": (investigation_result.attempts[0].model_dump()
                        if investigation_result else None)})
    print(json.dumps({"results": summaries, "services_stopped": True, "real_model_calls": 0}))


if __name__ == "__main__":
    main()
