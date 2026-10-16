"""Run published pytest assets on disposable normal/fault targets; no model or live switch."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.evaluation import save_evaluation
from agenttest.regression import load_regression
from agenttest.report import Outcome, ReportStore
from agenttest.ticket_fault_check import CASE_IDS, case_inputs


def check(output: Path) -> dict:
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    assets = {}
    for key in CASE_IDS:
        manifest, _ = load_regression(PROJECT_ROOT / "examples/regressions" / key.lower())
        assets[manifest.scenario_sha256] = key
    rows = []
    for phase in ("normal", "faults"):
        command = [sys.executable, "-m", "pytest", "examples/regressions", "-q",
                   "--basetemp=" + str(output / phase)]
        if phase == "faults":
            command.append("--agenttest-ticket-faults")
        result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
            env={**os.environ, "PYTEST_ADDOPTS": "", "LANGSMITH_TRACING": "false"})
        (output / (phase + ".txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode != (1 if phase == "faults" else 0):
            raise ValueError("REGRESSION_PYTEST_EXIT_MISMATCH")
        reports = sorted((output / phase).glob("*/reports/*/report.json"))
        if len(reports) != 3:
            raise ValueError("REGRESSION_RUN_COUNT_MISMATCH")
        seen = set()
        for path in reports:
            report = ReportStore(path.parent.parent).read(path.parent.name)
            key = assets[report.scenario_sha256]
            if key in seen:
                raise ValueError("REGRESSION_DUPLICATE_CASE")
            seen.add(key)
            expected = Outcome.ASSERTION_FAILED if phase == "faults" else Outcome.PASS
            if (report.outcome != expected or report.task_mode != "operator_plan"
                    or report.task_satisfied is not None or report.model_call is not None):
                raise ValueError("REGRESSION_RESULT_MISMATCH")
            if phase == "faults":
                expected_check = case_inputs(key)[-1]["failed_check"]
                if not any([step.operation_id, c.kind, c.selector] == expected_check
                           and not c.passed
                           for step in report.steps for c in step.checks):
                    raise ValueError("REGRESSION_WRONG_FAILURE_MECHANISM")
            rows.append({"case_id": key, "phase": phase, "run_id": report.run_id,
                         "outcome": report.outcome.value,
                         "requests": sum(len(s.attempts) for s in report.steps)})
    result = {"verified": True, "normal_pytest_passed": 3, "fault_pytest_failed": 3,
              "real_model_calls": 0, "real_java_requests": 0, "results": rows}
    save_evaluation(result, output / "results.json")
    return {k: v for k, v in result.items() if k != "results"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="固定回归包正常／故障 pytest 验收")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(check(args.output), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
        print(json.dumps({"verified": False, "reason": "REGRESSION_EXPORT_ACCEPTANCE_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
