"""Complete offline pytest collection, balanced file batches, and exact terminal-ID audit."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from uuid import uuid4

from check_dev import duration_weights, partition
from run_colleague_acceptance import source_snapshot

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exact_coverage(expected, collected, executed):
    """Collection alone is insufficient: every ID needs a terminal setup/call report."""
    return (bool(expected) and len(set(expected)) == len(expected)
            and Counter(expected) == Counter(collected) == Counter(executed))


class Checkpoint:
    def __init__(self, folder, name):
        self.folder, self.name, self.collected, self.executed = folder, name, [], []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]
        write(self.folder / f"{self.name}-collected.json", self.collected)

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.when == "setup" and (report.failed or report.skipped):
            self.executed.append(report.nodeid)
            with (self.folder / f"{self.name}-events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(dict(nodeid=report.nodeid, outcome=report.outcome)) + "\n")

    def pytest_sessionfinish(self, session, exitstatus):
        write(self.folder / f"{self.name}-terminal.json", dict(exit_code=int(exitstatus),
              collected=self.collected, executed=self.executed, finished_at=time.time()))


def worker(folder, name, files):
    import pytest

    args = ["-q", "-p", "no:cacheprovider", *files]
    if name == "collection":
        args.append("--collect-only")
    else:
        args += ["--durations=10", "--junitxml=" + str(folder / f"{name}.xml"),
                 "--basetemp=" + str(ROOT / ".venv/tmp" / (folder.name + "-" + name))]
    return pytest.main(args, plugins=[Checkpoint(folder, name)])


def audit(folder, expected, names, snapshot, wall_seconds, process_codes=None):
    collected, executed, suites, errors, terminal_codes = [], [], [], [], {}
    for name in names:
        try:
            terminal = json.loads((folder / f"{name}-terminal.json").read_text(encoding="utf-8"))
            terminal_codes[name] = terminal["exit_code"]
            collected += terminal["collected"]
            executed += terminal["executed"]
            suites += ET.parse(folder / f"{name}.xml").getroot().findall("testsuite")
            if terminal["exit_code"] not in (0, 1):
                errors.append(name + ":nonterminal-exit")
        except (OSError, ValueError, KeyError, ET.ParseError):
            errors.append(name + ":missing-or-invalid-report")
    counts = {key: sum(int(s.get(key, 0)) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    unchanged = source_snapshot(ROOT) == snapshot
    coverage = exact_coverage(expected, collected, executed) and counts["tests"] == len(expected)
    result = dict(classification="FULL_OFFLINE_ALL_COLLECTED_TESTS", source_unchanged=unchanged,
        source_sha256=snapshot["sha256"], exact_test_id_coverage=coverage,
        collected=len(expected), executed=len(executed),
        missing=sorted(set(expected)-set(executed)),
        unexpected=sorted(set(executed)-set(expected)), audit_errors=errors, **counts,
        process_exit_codes=process_codes or terminal_codes,
        passed=counts["tests"]-counts["failures"]-counts["errors"]-counts["skipped"],
        wall_seconds=wall_seconds, suite_seconds_sum=sum(float(s.get("time", 0)) for s in suites))
    result["complete"] = unchanged and coverage and not errors
    result["successful"] = (result["complete"] and not counts["failures"] and not counts["errors"]
                            and all(c == 0 for c in result["process_exit_codes"].values()))
    combined = ET.Element("testsuites")
    combined.extend(suites)
    ET.ElementTree(combined).write(folder / "combined.xml", encoding="utf-8", xml_declaration=True)
    write(folder / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, choices=range(1, 7), default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("files", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args()
    folder = (args.output or ROOT / "runs/full-checks" / uuid4().hex).resolve()
    if not folder.is_relative_to((ROOT / "runs").resolve()):
        raise ValueError("FULL_CHECK_OUTPUT_MUST_STAY_IN_RUNS")
    if args.worker:
        if args.worker != "collection" and not args.worker.startswith("batch-"):
            raise ValueError("INVALID_FULL_CHECK_WORKER")
        return worker(folder, args.worker, args.files)
    if args.files or os.environ.get("PYTEST_ADDOPTS"):
        raise ValueError("FULL_CHECK_DOES_NOT_ACCEPT_SELECTION_FILTERS")
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    snapshot = source_snapshot(ROOT)
    write(folder / "source.json", snapshot)
    with zipfile.ZipFile(folder / "source.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, digest in snapshot["files"].items():
            content = (ROOT / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("FULL_CHECK_SOURCE_CHANGED_DURING_ARCHIVE")
            archive.writestr(name, content)
    write(folder / "source-archive.json", dict(source_sha256=snapshot["sha256"],
          archive_sha256=hashlib.sha256((folder / "source.zip").read_bytes()).hexdigest()))

    def verify():
        if source_snapshot(ROOT) != snapshot:
            raise ValueError("FULL_CHECK_SOURCE_CHANGED")

    def run(name, files):
        verify()
        with (folder / f"{name}.log").open("w", encoding="utf-8") as stream:
            try:
                process = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                    "--output", str(folder), "--worker", name, *files], cwd=ROOT,
                    env={**os.environ, "PYTHONUTF8": "1"}, stdout=stream,
                    stderr=subprocess.STDOUT, timeout=10800, check=False)
                return process.returncode
            except (OSError, subprocess.TimeoutExpired):
                return 124

    print(f"完整离线验证开始，{args.jobs} 个独立进程；报告 {folder}", flush=True)
    if run("collection", []):
        write(folder / "result.json", dict(successful=False, reason="COLLECTION_FAILED"))
        return 1
    expected = json.loads((folder / "collection-collected.json").read_text(encoding="utf-8"))
    files = sorted({node.split("::")[0] for node in expected})
    if not files:
        raise ValueError("EMPTY_FULL_TEST_COLLECTION")
    weights = duration_weights(files, ROOT / "runs/testing-colleague")
    batches = partition(files, args.jobs, weights)
    names = [f"batch-{index}" for index in range(1, len(batches) + 1)]
    write(folder / "schedule.json", dict(expected_ids=expected, batches=batches,
        historical_durations_for_scheduling_only=True, retries=0, jobs=len(batches)))
    verify()
    process_codes = {}
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        pending = {pool.submit(run, name, batch): name for name, batch in zip(names, batches)}
        for future in as_completed(pending):
            try:
                code = future.result()
            except ValueError:
                code = 1
            process_codes[pending[future]] = code
            print(f"{pending[future]} 终态：{code}；等待其他批次，保留全部首次结果。", flush=True)
    result = audit(folder, expected, names, snapshot, time.monotonic() - started, process_codes)
    print(json.dumps({k: result[k] for k in ("successful", "exact_test_id_coverage", "tests",
        "passed", "failures", "errors", "skipped", "wall_seconds")}), flush=True)
    return int(not result["successful"])


if __name__ == "__main__":
    raise SystemExit(main())
