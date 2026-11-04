"""Explicit focused checks for development; the default full pytest suite stays intact."""

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
AREAS = {
    "contracts": ["test_contracts", "test_parser", "test_contract_import",
                  "test_contract_cache", "test_request_validation"],
    "tasks": ["test_task_store", "test_task_service", "test_project_intake",
              "test_product_tasks", "test_usage_reconciliation"],
    "product": ["test_contract_cache", "test_task_store", "test_generic_intake",
                "test_project_inventory", "test_product_*", "test_usage_reconciliation",
                "test_request_requirements", "test_requirement_ledger", "test_plan_construction",
                "test_workspace_*", "test_dev_checks"],
    "focus": ["test_product_focus", "test_product_inputs", "test_product_sources",
              "test_request_requirements", "test_requirement_ledger",
              "test_product_lengths"],
    "intake": ["test_generic_intake", "test_project_inventory", "test_product_schema",
               "test_workspace_identity", "test_workspace_mode"],
    "execution": ["test_product_tasks", "test_product_cancellation", "test_product_checks",
                  "test_plan_construction",
                  "test_product_scope", "test_negative_values", "test_task_store"],
}


def selected_files(area, root):
    files = set()
    for pattern in AREAS[area]:
        matches = [p for p in (root / "tests").glob(pattern + ".py") if p.is_file()]
        if not matches:
            raise ValueError(f"检查文件缺失：{pattern}")
        files.update(p.relative_to(root).as_posix() for p in matches)
    return sorted(files)


def duration_weights(files, output):
    """Previous durations schedule work only; every selected test still runs afresh."""
    weights, known = dict.fromkeys(files, 1.0), set()
    modules = {file[:-3].replace("/", "."): file for file in files}
    reports = [*output.glob("*.xml"), *output.glob("*/*.xml")]
    for report in sorted(reports, key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        totals = {}
        try:
            for case in ET.parse(report).getroot().iter("testcase"):
                module = case.get("classname", "")
                for prefix, file in modules.items():
                    if module == prefix or module.startswith(prefix + "."):
                        totals[file] = totals.get(file, 0) + max(0, float(case.get("time", 0)))
                        break
        except (OSError, ET.ParseError, ValueError):
            continue
        for file, seconds in totals.items():
            if file not in known:
                weights[file] = max(seconds, 0.01)
                known.add(file)
    return weights


def partition(files, jobs, weights):
    batches = [[] for _ in range(min(jobs, len(files)))]
    totals = [0.0 for _ in batches]
    for file in sorted(files, key=lambda path: (-weights[path], path)):
        index = min(range(len(batches)), key=lambda i: totals[i])
        batches[index].append(file)
        totals[index] += weights[file]
    return [sorted(batch) for batch in batches if batch]


def report_counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        raise ValueError("测试报告没有结果")
    return {key: sum(int(s.get(key, 0)) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")}


def main():
    parser = argparse.ArgumentParser(description="开发快检，仅检查明确列出的范围，不代替完整验收。")
    parser.add_argument("--area", choices=AREAS, default="product")
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3), default=1,
                        help="独立文件批次并行数；小范围保留1，大范围可用3")
    parser.add_argument("--list", action="store_true", help="只查看检查范围，不执行")
    args = parser.parse_args()
    tests = selected_files(args.area, ROOT)
    output = ROOT / "runs" / "dev-checks"
    batches = partition(tests, args.jobs, duration_weights(tests, output))
    print(f"开发快检：{args.area}；未运行范围不计作通过。", flush=True)
    for index, batch in enumerate(batches, 1):
        print(f"批次 {index}/{len(batches)}：", flush=True)
        for path in batch:
            print(f"  {path}", flush=True)
    if args.list:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    output = output / stamp
    output.mkdir(parents=True)
    receipt = dict(classification="FOCUSED_DEVELOPMENT_CHECK_ONLY", area=args.area,
                   selected_files=tests, batches=batches, jobs=len(batches), results=[],
                   historical_durations_used_for_scheduling_only=True, status="RUNNING")

    def save():
        (output / "result.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                                           encoding="utf-8")

    def run_batch(index, batch):
        name = f"batch-{index}"
        command = [sys.executable, "-m", "pytest", "-q", *batch, "--durations=10",
            "-p", "no:cacheprovider", "--basetemp", str(ROOT / ".venv/tmp" / f"{stamp}-{name}"),
            "--junitxml", str(output / f"{name}.xml")]
        started = perf_counter()
        code, counts = 1, None
        with (output / f"{name}.log").open("w", encoding="utf-8") as stream:
            try:
                result = subprocess.run(command, cwd=ROOT, check=False, stdout=stream,
                    stderr=subprocess.STDOUT, timeout=900,
                    env={**os.environ, "PYTHONUTF8": "1"})
                code = result.returncode
                counts = report_counts(output / f"{name}.xml")
                if not counts["tests"] or counts["failures"] or counts["errors"]:
                    code = code or 1
            except (OSError, subprocess.TimeoutExpired, ET.ParseError, ValueError) as exc:
                code = code or 1
                stream.write(f"\n检查进程或报告异常：{type(exc).__name__}\n")
        return dict(batch=index, exit_code=code, seconds=perf_counter() - started,
                    counts=counts, log=str(output / f"{name}.log"))

    start = perf_counter()
    save()
    lint = subprocess.run([sys.executable, "-m", "ruff", "check", "."], cwd=ROOT, check=False)
    receipt["ruff_exit_code"] = lint.returncode
    if lint.returncode:
        receipt.update(status="LINT_FAILED", wall_seconds=perf_counter() - start)
        save()
        return lint.returncode
    with ThreadPoolExecutor(max_workers=len(batches)) as workers:
        pending = [workers.submit(run_batch, index, batch)
                   for index, batch in enumerate(batches, 1)]
        for future in as_completed(pending):
            result = future.result()
            receipt["results"].append(result)
            save()
            print(f"批次 {result['batch']}/{len(batches)} 完成，退出码 {result['exit_code']}。",
                  flush=True)
    failed = any(r["exit_code"] for r in receipt["results"])
    counts = {key: sum((r["counts"] or {}).get(key, 0) for r in receipt["results"])
              for key in ("tests", "failures", "errors", "skipped")}
    receipt.update(status="FAILED" if failed else "PASSED", counts=counts,
                   wall_seconds=perf_counter() - start,
                   batch_seconds_sum=sum(r["seconds"] for r in receipt["results"]))
    save()
    print(f"本范围 {counts['tests']} 项，失败 {counts['failures']}，错误 {counts['errors']}，"
          f"跳过 {counts['skipped']}；墙钟 {receipt['wall_seconds']:.1f} 秒。", flush=True)
    print(f"报告：{output}", flush=True)
    print("尚未执行全量、网页或真实模型验收；各批耗时之和不作为总等待时间。",
          flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
