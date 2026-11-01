"""Freeze source and run every new three-round web acceptance position exactly once."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from uuid import uuid4

from agenttest.task_store import TaskStore


def source_snapshot(root):
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
    files = {}
    for raw in names.split(b"\0"):
        if raw:
            name = raw.decode("utf-8")
            path = root / name
            if path.is_file():
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return dict(sha256=hashlib.sha256(encoded).hexdigest(), files=files)


def claim_version(path):
    """A recorded first-attempt schedule is never silently started again."""
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError("ACCEPTANCE_VERSION_ALREADY_RECORDED_USE_A_NEW_VERSION")
    try:
        with (path / "first-attempt.claim").open("x", encoding="utf-8") as claim:
            claim.write("First-attempt schedule claimed; never replay this version.\n")
    except FileExistsError:
        raise ValueError("ACCEPTANCE_VERSION_ALREADY_RECORDED_USE_A_NEW_VERSION") from None


def acknowledge_unknowns(database, authorization):
    """Only the operator's explicit Goal continuation authority clears later task barriers."""
    if authorization is None:
        return
    raw = authorization.read_bytes()
    proof = json.loads(raw)
    if (proof.get("source") != "HUMAN_USER_IN_CURRENT_CHAT"
            or proof.get("reply") != "以后未知用量也可以继续"
            or proof.get("scope") != "THIS_GOAL_FUTURE_UNKNOWN_USAGE_CONTINUATION"):
        raise ValueError("EXPLICIT_GOAL_CONTINUATION_AUTHORITY_REQUIRED")
    db = TaskStore(database)
    with db.connect() as connection:
        calls = [dict(row) for row in connection.execute(
            "SELECT * FROM model_usage_journal WHERE state='UNKNOWN' AND NOT EXISTS "
            "(SELECT 1 FROM model_usage_exceptions e WHERE e.call_id=model_usage_journal.id)")]
    for call in calls:
        stats = json.loads(call["stats"])
        db.record_usage_exception(dict(call_id=call["id"], task_id=call["task_id"],
            request_sha256=stats["request_sha256"], source="EXPLICIT_OPERATOR_EXCEPTION",
            authorization_sha256=hashlib.sha256(raw).hexdigest(),
            scope="THIS_CALL_ONLY_PRESERVE_UNKNOWN"))


def project_lanes(manifest, jobs):
    """Only new explicitly isolated schedules may overlap independent projects."""
    order = manifest["order"]
    projects = {task["project"] for task in manifest["tasks"]}
    if not order or len(order) != len(set(order)):
        raise ValueError("UNIQUE_NONEMPTY_ACCEPTANCE_ORDER_REQUIRED")
    lanes = {}
    for position in order:
        if not re.fullmatch(r"[1-9][0-9]*:(parcels|reservations|directory)", position):
            raise ValueError("INVALID_ACCEPTANCE_POSITION")
        project = position.split(":")[1]
        if project not in projects:
            raise ValueError("ACCEPTANCE_PROJECT_MISSING")
        lanes.setdefault(project, []).append(position)
    if jobs == 1:
        return {"serial": order}
    if manifest.get("execution_mode") != "PARALLEL_PROJECTS_ISOLATED":
        raise ValueError("NEW_ISOLATED_PARALLEL_SCHEDULE_REQUIRED")
    return lanes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--continuation-authorization", type=Path)
    parser.add_argument("--manifest", type=Path,
                        default=Path("docs/testing-colleague-acceptance-v1.json"))
    parser.add_argument("--project-jobs", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--plan", action="store_true", help="Only show scheduling; no model calls")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest_path = (root / args.manifest).resolve()
    if not manifest_path.is_relative_to(root):
        raise ValueError("MANIFEST_OUTSIDE_WORKSPACE")
    manifest = json.loads(manifest_path.read_bytes())
    from check_colleague_reference import check_reference

    reference_check = check_reference(manifest)
    lanes = project_lanes(manifest, args.project_jobs)
    if args.project_jobs > 1 and args.continuation_authorization is None:
        raise ValueError("PARALLEL_REQUIRES_EXPLICIT_UNKNOWN_USAGE_AUTHORITY")
    if args.plan:
        print(json.dumps(dict(lanes=lanes, reference=reference_check, model_calls=0),
                         ensure_ascii=False, indent=2))
        return 0
    folder = manifest.get("evidence_folder", "acceptance-v1")
    if not re.fullmatch(r"(?:acceptance|speedcheck)-v[1-9][0-9]*", folder):
        raise ValueError("ACCEPTANCE_VERSION_REQUIRED")
    version_root = root / "runs/testing-colleague" / folder
    claim_version(version_root)
    evidence = version_root / uuid4().hex
    evidence.mkdir(parents=True)
    (evidence / "reference-preflight.json").write_text(
        json.dumps(reference_check, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot = source_snapshot(root)
    (evidence / "frozen-source.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    archive = evidence / "frozen-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, digest in snapshot["files"].items():
            content = (root / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise RuntimeError("SOURCE_CHANGED_DURING_ARCHIVE")
            output.writestr(name, content)
    (evidence / "source-archive.json").write_text(json.dumps(dict(
        source_sha256=snapshot["sha256"], files=len(snapshot["files"]),
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        archive_bytes=archive.stat().st_size), indent=2), encoding="utf-8")
    schedule = evidence / "schedule.json"
    schedule.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not npx:
        raise ValueError("Existing frontend runtime is required")
    results = []
    workspace = evidence / "workspace"
    workspace.mkdir()
    workspaces = {lane: workspace if lane == "serial" else workspace / lane for lane in lanes}
    for directory in workspaces.values():
        directory.mkdir(exist_ok=True)
    (evidence / "workspace-provenance.json").write_text(json.dumps(dict(
        workspace=str(workspace), prior_workspaces_preserved=True,
        execution_mode=manifest.get("execution_mode", "SERIAL"),
        project_jobs=args.project_jobs, lanes=lanes,
        isolated_workspaces={lane: str(path) for lane, path in workspaces.items()},
        reason="Fresh acceptance batch; preserve old projects and the existing 100-project limit",
        unknown_usage_authority_sha256=(hashlib.sha256(args.continuation_authorization.read_bytes())
            .hexdigest() if args.continuation_authorization else None)), indent=2),
        encoding="utf-8")
    lock = Lock()
    def run_position(position, lane):
        if source_snapshot(root)["sha256"] != snapshot["sha256"]:
            raise RuntimeError("SOURCE_CHANGED_NEW_ACCEPTANCE_VERSION_REQUIRED")
        acknowledge_unknowns(workspaces[lane] / "tasks.sqlite3", args.continuation_authorization)
        round_number, project = position.split(":")
        environment = dict(os.environ, CONSOLE_BASE_URL="http://127.0.0.1:1",
            AGENTTEST_COLLEAGUE_DEV="1", AGENTTEST_COLLEAGUE_ACCEPTANCE="1",
            AGENTTEST_COLLEAGUE_PROJECT=project, AGENTTEST_COLLEAGUE_ROUND=round_number,
            AGENTTEST_COLLEAGUE_MANIFEST=str(schedule),
            AGENTTEST_COLLEAGUE_WORKSPACE=str(workspaces[lane]),
            AGENTTEST_COLLEAGUE_CANCEL="0", AGENTTEST_COLLEAGUE_FAULT_STATUS="200")
        environment["TEMP"] = environment["TMP"] = str(root / ".venv/tmp")
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_CHECKS", None)
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_QUESTIONS", None)
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_READY_COVERAGE", None)
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_SOURCE_CONFLICT", None)
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_LITERAL_CHECKS", None)
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_HELD_VALUES", None)
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_PARTIAL_CHECKS", None)
        environment.pop("AGENTTEST_COLLEAGUE_INPUT_CHOICE", None)
        environment.pop("AGENTTEST_COLLEAGUE_EXPECT_INITIAL_BODY", None)
        environment.pop("AGENTTEST_COLLEAGUE_FIXED_NEGATIVES_ONLY", None)
        started = time.time()
        print(f"开始 {position}；保留首次结果，不重试。", flush=True)
        with (evidence / f"{round_number}-{project}.log").open("w", encoding="utf-8") as output:
            result = subprocess.run([npx, "playwright", "test", "e2e/colleague-live.spec.ts",
                "--workers=1", "--retries=0",
                "--output=" + str(evidence / f"{round_number}-{project}-browser")],
                cwd=root / "web", env=environment, stdout=output, stderr=subprocess.STDOUT,
                check=False)
        record = dict(position=position, lane=lane, started_at=started, finished_at=time.time(),
                      exit_code=result.returncode, seconds=round(time.time() - started, 2))
        with lock:
            results.append(record)
            results.sort(key=lambda row: manifest["order"].index(row["position"]))
            (evidence / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"结束 {position}，退出码 {result.returncode}。", flush=True)

    def run_lane(lane, positions):
        for position in positions:
            run_position(position, lane)

    started = time.monotonic()
    errors = []
    with ThreadPoolExecutor(max_workers=args.project_jobs) as workers:
        pending = [workers.submit(run_lane, lane, positions) for lane, positions in lanes.items()]
        for future in pending:
            try:
                future.result()
            except Exception as exc:
                errors.append(type(exc).__name__)
    unchanged = source_snapshot(root)["sha256"] == snapshot["sha256"]
    unexecuted = [p for p in manifest["order"] if p not in {r["position"] for r in results}]
    summary = dict(classification=manifest.get("classification", "FROZEN_ACCEPTANCE_UNREVIEWED"),
        source_unchanged=unchanged, project_jobs=args.project_jobs, errors=errors,
        scheduled=len(manifest["order"]), finished=len(results), unexecuted=unexecuted,
        failed=sum(r["exit_code"] != 0 for r in results), wall_seconds=time.monotonic() - started,
        position_seconds_sum=sum(r["seconds"] for r in results))
    (evidence / "scheduler-result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"完整首次验收记录：{evidence}", flush=True)
    return int(bool(errors or unexecuted or summary["failed"] or not unchanged))


if __name__ == "__main__":
    raise SystemExit(main())
