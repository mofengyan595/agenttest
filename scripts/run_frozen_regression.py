"""Run ordinary pytest with a frozen checkout and an audit of collected test IDs."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from agenttest.eval_v2.freeze import checkout_identity

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = "AGENTTEST_FROZEN_TEST_AUDIT"
reports = {}


def pytest_runtest_logreport(report):
    if report.when == "call" or report.failed or report.skipped:
        outcome = ("xfailed" if getattr(report, "wasxfail", None) and report.skipped
                   else report.outcome)
        reports.setdefault(report.nodeid, []).append((report.when, outcome))


def pytest_sessionfinish(session):
    from collections import Counter

    collected = Counter(item.nodeid for item in session.items)
    nonunique = sorted(node for node, count in collected.items() if count != 1)
    missing = sorted(collected.keys() - reports.keys())
    extra = sorted(reports.keys() - collected.keys())
    repeated = sorted(node for node, stages in reports.items()
                      if len({stage for stage, _ in stages}) != len(stages))
    Path(os.environ[AUDIT_PATH]).write_text(json.dumps(dict(
        collected=len(session.items), nonunique=nonunique, missing=missing, extra=extra,
        repeated=repeated, reports=reports), ensure_ascii=False), encoding="utf-8")
    if nonunique or missing or extra or repeated:
        session.exitstatus = 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--exclude-local-environment", action="store_true",
                        help="Exclude the existing untracked .venv-lock directory for this run")
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.exclude_local_environment:
        previous = subprocess.run(["git", "config", "--path", "--get", "core.excludesFile"],
            cwd=ROOT, capture_output=True, encoding="utf-8", check=False).stdout.strip()
        original = Path(previous).expanduser() if previous else None
        exclusions = output / "git-excludes.txt"
        rules = original.read_text(encoding="utf-8") if original and original.is_file() else ""
        exclusions.write_text(rules + "\n/.venv-lock/\n", encoding="utf-8")
        index = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
        os.environ[f"GIT_CONFIG_KEY_{index}"] = "core.excludesFile"
        os.environ[f"GIT_CONFIG_VALUE_{index}"] = str(exclusions)
        os.environ["GIT_CONFIG_COUNT"] = str(index + 1)
    before = checkout_identity(ROOT)
    (output / "before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    env = dict(os.environ, LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    env[AUDIT_PATH] = str(output / "tests.json")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(ROOT / "scripts"), env.get("PYTHONPATH"))))
    with (output / "pytest.log").open("wb") as log:
        child = subprocess.run([sys.executable, "-X", "utf8", "-u", "-m", "pytest", "-q",
            "-p", "run_frozen_regression", "--tb=short", "--durations=20"], cwd=ROOT, env=env,
            stdout=log, stderr=subprocess.STDOUT, check=False)
    after = checkout_identity(ROOT)
    (output / "after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
    result = dict(exit_code=child.returncode, same_checkout=before == after,
                  audit_exists=(output / "tests.json").is_file())
    (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps(result))
    return int(child.returncode != 0 or before != after or not result["audit_exists"])


if __name__ == "__main__":
    raise SystemExit(main())
