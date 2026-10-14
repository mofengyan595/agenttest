"""Explicit local HTTP acceptance through the generic CLI; no real model access."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from smoke_local_http import serve

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.evaluation import save_evaluation
from agenttest.portability_demo import EXAMPLES, project_inputs, task_inputs
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.safety import TargetConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description="显式隔离回环 HTTP 验收；固定规划，无模型 Key")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        private_output(PROJECT_ROOT, args.output)
        args.output.mkdir(parents=True, exist_ok=False)
        summaries = []
        for project, name in (("courses", "ownership"), ("tickets", "close")):
            app, _, config, _, environ = project_inputs(project)
            task, draft = task_inputs(project, name)
            directory = args.output / project
            store = ReportStore(directory / "reports")
            with serve(app) as url:
                config = TargetConfig.model_validate({**config.model_dump(), "base_url": url})
                task.target_config_sha256 = fingerprint(config.model_dump())
                save_evaluation(config.model_dump(), directory / "target.json")
                save_evaluation(task.model_dump(mode="json"), directory / "task.json")
                save_evaluation(draft.scenario().model_dump(), directory / "plan.json")
                result = subprocess.run([
                    sys.executable, "-m", "agenttest", "agent", "--user-task", task.user_task,
                    "--fake-plan", str(directory / "plan.json"),
                    "--contract", str(EXAMPLES / project / "openapi.json"),
                    "--policies", str(EXAMPLES / project / "policies.json"),
                    "--target", str(directory / "target.json"),
                    "--task-contract", str(directory / "task.json"),
                    "--runs-dir", str(store.root),
                ], env={**os.environ, **environ}, capture_output=True, text=True, timeout=70,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if result.returncode != 0:
                    raise ValueError("Local generic CLI failed")
                report = store.read(json.loads(result.stdout)["run_id"])
                if not (report.outcome == Outcome.PASS and report.task_satisfied is True
                        and report.workflow_completed is True and report.intent_verified
                        and len(report.steps) == len(draft.steps)
                        and all(s.attempts and s.checks and all(c.passed for c in s.checks)
                                for s in report.steps)):
                    raise ValueError("Incomplete local generic workflow")
                summaries.append({"project": project, "run_id": report.run_id,
                                  "outcome": report.outcome.value, "steps": len(report.steps),
                                  "requests": sum(len(s.attempts) for s in report.steps)})
        summary = {"classification": "FIXED_REPLY_LOCAL_HTTP_ACCEPTANCE", "results": summaries,
                   "services_stopped": True, "real_model_calls": 0,
                   "real_java_requests": 0, "real_database_writes": 0}
        save_evaluation(summary, args.output / "summary.json")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, subprocess.SubprocessError):
        print(json.dumps({"reason": "LOCAL_HTTP_ACCEPTANCE_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
