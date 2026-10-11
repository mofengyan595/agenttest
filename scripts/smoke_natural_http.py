"""Two annotated paths over real loopback HTTP, with FakeLLM and no external model."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke_local_http import serve

from agenttest.demo import PROJECT_ROOT
from agenttest.evaluation import TaskSuite, intent_satisfied, manual_plan
from agenttest.natural_demo import natural_inputs
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, default=Path("runs/natural-http"))
    args = parser.parse_args()
    store = ReportStore(args.runs_dir)
    store.root.mkdir(parents=True, exist_ok=True)
    suite = TaskSuite.load(PROJECT_ROOT / "examples/nl-tasks.json")
    summaries = []
    for case in (suite.cases[0], suite.cases[9]):
        target, config, _, environ = natural_inputs()
        with serve(target) as url, tempfile.TemporaryDirectory(dir=store.root) as temporary:
            directory = Path(temporary)
            config = TargetConfig.model_validate({**config.model_dump(), "base_url": url})
            files = {"target.json": config.model_dump_json(),
                     "plan.json": manual_plan(case).model_dump_json(),
                     "contract.json": json.dumps(target.openapi())}
            for name, content in files.items():
                (directory / name).write_text(content, encoding="utf-8")
            result = subprocess.run([
                sys.executable, "-m", "agenttest", "agent", "--user-task", case.user_task,
                "--fake-plan", str(directory / "plan.json"),
                "--contract", str(directory / "contract.json"),
                "--target", str(directory / "target.json"),
                "--policies", str(PROJECT_ROOT / "examples/nl-policies.json"),
                "--runs-dir", str(store.root),
            ], env={**os.environ, **environ}, capture_output=True, text=True, timeout=70)
            assert result.returncode == 0, "Natural-language CLI HTTP smoke failed"
            report = store.read(json.loads(result.stdout)["run_id"])
            assert report.outcome == Outcome.PASS and intent_satisfied(case, report)
            summaries.append({"task_id": case.id, "outcome": report.outcome.value,
                              "steps": len(report.steps), "requests": sum(
                                  len(s.attempts) for s in report.steps), "run_id": report.run_id})
    print(json.dumps({"results": summaries, "services_stopped": True, "real_model_calls": 0}))


if __name__ == "__main__":
    main()
