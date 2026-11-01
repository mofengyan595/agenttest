"""Explicit real-model development smoke; never a new formal quality score."""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from serve_colleague_targets import document

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, analyze_contract
from agenttest.goal_server import configured_task_service
from agenttest.project_intake import IntakeInput
from agenttest.safety import ResourceLifecycle
from agenttest.task_store import TERMINAL

ROOT = Path(__file__).resolve().parents[1]


async def terminal(service, row):
    async with asyncio.timeout(610):
        while (row := service.db.get(row["id"], "operator"))["status"] not in TERMINAL:
            await asyncio.sleep(0.1)
    return row


async def check(args):
    from run_reliable_acceptance import unresolved_usage

    if unresolved_usage(ROOT / "runs/reliable-colleague"):
        raise ValueError("THIS_GOAL_UNKNOWN_USAGE_REQUIRES_RECONCILIATION_OR_EXPLICIT_AUTHORITY")
    started = perf_counter()
    args.output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    with (args.output / "target.log").open("wb") as log:
        child = subprocess.Popen([sys.executable, "scripts/serve_colleague_targets.py",
            "--project", "parcels", "--port", str(args.port), "--events",
            str(args.output / "target-events.jsonl")], cwd=ROOT,
            env=dict(os.environ, PYTHONUTF8="1"), stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        service = None
        result = dict(evidence_kind="REAL_MODEL_DEVELOPMENT_NOT_FORMAL_ACCEPTANCE",
                      initial=None, fixed=[], passed=False)
        try:
            origin = f"http://127.0.0.1:{args.port}"
            async with httpx.AsyncClient(trust_env=False) as client:
                async with asyncio.timeout(30):
                    while True:
                        if child.poll() is not None:
                            raise ValueError("ISOLATED_TARGET_EXITED")
                        try:
                            if (await client.get(origin + "/health")).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.2)
            service = configured_task_service(args.output / "workspace", projects={},
                                              model_env=ROOT / ".env", generic_intake=True)
            raw = json.dumps(document("parcels"), ensure_ascii=False)
            operations = []
            selected = ({"createParcel", "readParcel", "deleteParcel"}
                        if args.case == "write" else {"readAvailability"})
            for op in analyze_contract(load_openapi(raw)):
                if op["operation_id"] in selected:
                    operations.append(dict(operation_id=op["operation_id"], alias=op["summary"],
                        statuses=op["statuses"], checks=op["checks"], arguments=[
                            {k: v for k, v in a.items() if k in {"location", "name", "value",
                             "source_operation", "selector", "generated", "needs_input"}}
                            for a in op["arguments"]]))
            setup = GenericSetup(base_url=origin, isolated_confirmed=True, allow_mutations=True,
                assisted=True, operations=operations, resource_lifecycles=[ResourceLifecycle(
                    create_operation="createParcel", delete_operation="deleteParcel",
                    path_parameter="parcelId", id_selector="$.id",
                    fresh_resource_basis="自建隔离靶场用 uuid4 为每次成功创建分配全新专用包裹。")]
                    if args.case == "write" else [])
            saved = service.intake.save(IntakeInput(name="写回归开发检查", document=raw,
                generic=setup, confirmed=True), "operator")
            row = await service.submit("operator", saved["id"],
                "创建一个专用测试包裹，读取确认内容后删除同一个包裹，只检查这条完整流程。"
                    if args.case == "write" else "检查服务可用状态，异常时调查，并保留原始失败。",
                uuid4().hex)
            initial = await terminal(service, row)
            result["initial"] = initial
            for case in initial["summary"].get("cases", []):
                if case.get("outcome") != "PASS" or not case.get("resource_lifecycle"):
                    continue
                service.export_regression(initial["id"], "operator", case["run_id"])
                for _ in range(2):
                    fixed = await terminal(service, await service.saved.submit(
                        "operator", initial["id"], case["run_id"], uuid4().hex))
                    result["fixed"].append(fixed)
                break
            result["passed"] = (len(result["fixed"]) == 2 and all(
                r["summary"].get("fixed_regression_passed")
                and r["summary"]["context_usage"]["model_calls"] == 0
                for r in result["fixed"]))
            if args.case == "investigation":
                cases = initial["summary"].get("cases", [])
                result["passed"] = any(c.get("outcome") == "ASSERTION_FAILED"
                    and 1 <= len(c.get("investigations", [])) <= 2
                    and c.get("failure_explanation", {}).get("cause_confirmed") is False
                    for c in cases)
        finally:
            if service:
                await service.close()
            child.terminate()
            child.wait(timeout=10)
            result["elapsed_seconds"] = round(perf_counter() - started, 3)
            (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False,
                indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=result["passed"], output=str(args.output),
                         elapsed_seconds=result["elapsed_seconds"]), ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=18341)
    parser.add_argument("--case", choices=["write", "investigation"], default="write")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("An isolated loopback port is required")
    raise SystemExit(asyncio.run(check(args)))
