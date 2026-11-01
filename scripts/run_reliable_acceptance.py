"""Claim and preserve nine first-attempt positions; UNKNOWN stops the remaining schedule."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import zipfile
from pathlib import Path

from check_reliable_reference import preflight
from run_colleague_acceptance import source_snapshot

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def usage(path):
    if not path.is_file():
        return dict(model_calls=None, known_tokens=0, total_tokens=None, usage_known=False,
                    state="DATABASE_MISSING", calls=[])
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id,task_id,state,stats FROM model_usage_journal").fetchall()
    calls = [dict(id=row[0], task_id=row[1], state=row[2], stats=json.loads(row[3]))
             for row in rows]
    unknown = any(c["state"] in {"PENDING", "UNKNOWN"} for c in calls)
    tokens = sum(c["stats"].get("total_tokens") or 0 for c in calls
                 if c["stats"].get("total_tokens") is not None)
    # Only a known reported zero is zero. Missing database or PENDING dispatch stays unknown.
    count = sum(c["stats"].get("calls", 0) for c in calls)
    return dict(model_calls=count if not any(c["state"] == "PENDING" for c in calls) else None,
                known_tokens=tokens, total_tokens=None if unknown else tokens,
                usage_known=not unknown, calls=calls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path("docs/reliable-colleague-acceptance-v1.json"))
    parser.add_argument("--output", type=Path,
                        default=Path("runs/reliable-colleague/acceptance-v1"))
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    manifest_path = (ROOT / args.manifest).resolve()
    output = (ROOT / args.output).resolve()
    if (not manifest_path.is_relative_to(ROOT)
            or not output.is_relative_to(ROOT / "runs/reliable-colleague")):
        raise ValueError("ACCEPTANCE_PATH_OUTSIDE_WORKSPACE")
    manifest = json.loads(manifest_path.read_bytes())
    checked = preflight(manifest)
    schedule = [dict(position=f"{round_number}:{task['id']}", round=round_number, task=task["id"])
                for round_number in range(1, 4) for task in manifest["tasks"]]
    if args.plan:
        print(json.dumps(dict(schedule=schedule, preflight=checked, model_calls=0),
                         ensure_ascii=False, indent=2))
        return 0
    if not args.real:
        parser.error("--real is required to send configured model requests")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "first-attempt-claim.json", dict(manifest_sha256=hashlib.sha256(
        manifest_path.read_bytes()).hexdigest(), schedule=schedule,
        preserve_all_first_results=True))
    write(output / "reference-preflight.json", checked)
    write(output / "schedule.json", manifest)
    snapshot = source_snapshot(ROOT)
    write(output / "frozen-source.json", snapshot)
    with zipfile.ZipFile(output / "frozen-source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name, expected in snapshot["files"].items():
            raw = (ROOT / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("SOURCE_CHANGED_DURING_ARCHIVE")
            archive.writestr(name, raw)
    runner = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not runner:
        raise ValueError("EXISTING_FRONTEND_RUNTIME_REQUIRED")
    started, records, stop_reason = time.perf_counter(), [], None
    for item in schedule:
        if source_snapshot(ROOT) != snapshot:
            stop_reason = "SOURCE_CHANGED"
            break
        directory = output / item["position"].replace(":", "-")
        directory.mkdir()
        environment = dict(os.environ, PYTHONUTF8="1", CONSOLE_BASE_URL="http://127.0.0.1:1",
            AGENTTEST_RELIABLE_REAL="1", AGENTTEST_RELIABLE_TASK=item["task"],
            AGENTTEST_RELIABLE_MANIFEST=str(output / "schedule.json"),
            AGENTTEST_RELIABLE_EVIDENCE=str(directory))
        environment["TEMP"] = environment["TMP"] = str(ROOT / ".venv/tmp")
        position_started = time.perf_counter()
        print("开始 " + item["position"] + "；只记录首次，不重试。", flush=True)
        try:
            with (directory / "browser.log").open("w", encoding="utf-8") as log:
                process = subprocess.run([runner, "playwright", "test",
                    "e2e/reliable-acceptance.spec.ts", "--workers=1", "--retries=0",
                    "--output=" + str(directory / "browser")], cwd=ROOT / "web", env=environment,
                    stdout=log, stderr=subprocess.STDOUT, check=False, timeout=960)
        except subprocess.TimeoutExpired:
            # Request only this position's launcher stop, never kill a service by port/name.
            try:
                (directory / "stop.request").write_text("Acceptance timeout; no replay.\n",
                                                       encoding="utf-8")
            except OSError:
                pass
            process = subprocess.CompletedProcess([], 124)
        try:
            result = json.loads((directory / "position-result.json").read_bytes())
        except (OSError, ValueError):
            result = dict(passed=False, error="POSITION_REPORT_MISSING", cleanup_confirmed=False)
        ledger = usage(directory / "lab/workspace/tasks.sqlite3")
        record = dict(**item, exit_code=process.returncode, result=result, usage=ledger,
                      wall_seconds=round(time.perf_counter() - position_started, 3))
        records.append(record)
        write(directory / "audited-position.json", record)
        print(f"结束 {item['position']}：退出{process.returncode}，"
              f"已知Token {ledger['known_tokens']}，用量已知{ledger['usage_known']}。", flush=True)
        if not ledger["usage_known"]:
            stop_reason = "UNKNOWN_USAGE_REQUIRES_THIS_GOAL_AUTHORITY"
            break
        if not result.get("cleanup_confirmed"):
            stop_reason = "CHILD_SERVICE_CLEANUP_UNCONFIRMED"
            break
    known = all(r["usage"]["usage_known"] for r in records)
    known_tokens = sum(r["usage"]["known_tokens"] for r in records)
    counts = [r["result"].get("task_target_requests") for r in records]
    summary = dict(classification=manifest["classification"], scheduled=len(schedule),
        executed=len(records), passed=sum(r["exit_code"] == 0 and r["result"].get("passed", False)
                                          for r in records),
        failed=sum(r["exit_code"] != 0 or not r["result"].get("passed", False) for r in records),
        unexecuted=[p for p in schedule if p["position"] not in {r["position"] for r in records}],
        stop_reason=stop_reason, source_unchanged=source_snapshot(ROOT) == snapshot,
        wall_seconds=round(time.perf_counter() - started, 3),
        model_calls=sum(r["usage"]["model_calls"] or 0 for r in records)
            if all(r["usage"]["model_calls"] is not None for r in records) else None,
        known_tokens=known_tokens, total_tokens=known_tokens if known else None, usage_known=known,
        task_target_requests=sum(counts) if all(c is not None for c in counts) else None,
        external_checker_requests=sum(r["result"].get("external_checker_requests", 0)
                                      for r in records))
    write(output / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(bool(summary["failed"] or summary["unexecuted"] or not summary["source_unchanged"]))


if __name__ == "__main__":
    raise SystemExit(main())
