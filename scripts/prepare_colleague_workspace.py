"""Initialize operator-owned disposable lab projects without model calls or target requests."""

import json
from pathlib import Path

from serve_colleague_targets import document

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, IntakeRole, analyze_contract
from agenttest.project_intake import IntakeInput
from agenttest.report import ReportStore
from agenttest.safety import ResourceLifecycle
from agenttest.task_service import TaskService
from agenttest.task_store import TaskStore


async def prepare(data: Path, port: int, environ):
    workspace = data / "workspace"
    receipt = data / "lab-projects.json"
    if receipt.exists():
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        if saved["product_port"] != port:
            raise ValueError("EXISTING_LAB_PORT_CHANGED_USE_ANOTHER_DATA_DIRECTORY")
        return saved
    service = TaskService({}, TaskStore(workspace / "tasks.sqlite3"),
        ReportStore(workspace / "reports"), lambda _: None, generic_intake=True,
        environ=environ, model_status=lambda: "UNAVAILABLE")
    result = dict(product_port=port, projects={}, model_calls=0, target_requests=0)
    try:
        for offset, kind in enumerate(("parcels", "reservations", "directory"), 1):
            raw = json.dumps(document(kind), ensure_ascii=False)
            existing = next((r for r in service.intake.records() if (r.get("generic") or {}).get(
                "base_url") == f"http://127.0.0.1:{port + offset}"
                and json.loads(r["document"]) == json.loads(raw)), None)
            if existing:
                result["projects"][kind] = existing["id"]
                continue  # Preserve operator changes and confirmation; never auto-reapprove.
            operations = []
            for item in analyze_contract(load_openapi(raw)):
                if not item["supported"]:
                    continue
                operations.append(dict(operation_id=item["operation_id"], alias=item["summary"],
                    statuses=item["statuses"], checks=item["checks"],
                    role="owner" if kind == "reservations" and (
                        item["authentication_required"] or item["operation_id"] == "openSession")
                        else None,
                    arguments=[{k: v for k, v in a.items() if k in {"location", "name", "value",
                        "source_operation", "selector", "generated", "needs_input"}}
                        for a in item["arguments"]]))
            roles, lifecycle = [], []
            if kind == "reservations":
                roles = [IntakeRole(alias="owner", login_operation="openSession",
                    username_env="AGENTTEST_TARGET_LAB_OWNER_USERNAME",
                    password_env="AGENTTEST_TARGET_LAB_OWNER_PASSWORD"),
                    IntakeRole(alias="reader", bearer_env="AGENTTEST_TARGET_LAB_READER_BEARER")]
                next(o for o in operations if o["operation_id"] == "readReservation")[
                    "access_checks"] = [dict(role="reader", statuses=[403],
                        rule="自建靶场约定：另一身份读取本轮所有者预约应返回403。")]
            if kind in {"parcels", "reservations"}:
                prefix, parameter, selector = (("Parcel", "parcelId", "$.id") if kind == "parcels"
                    else ("Reservation", "reservationId", "$.reservation_id"))
                lifecycle = [ResourceLifecycle(create_operation="create" + prefix,
                    delete_operation="delete" + prefix, path_parameter=parameter,
                    id_selector=selector,
                    fresh_resource_basis="自建隔离靶场每次成功创建使用全新随机ID，清理仅作用于本轮资源。")]
            setup = GenericSetup(base_url=f"http://127.0.0.1:{port + offset}",
                isolated_confirmed=True, allow_mutations=kind != "directory", assisted=True,
                approve_negative_tests=True, allow_negative_value_variants=True,
                roles=roles, operations=operations, resource_lifecycles=lifecycle)
            item = service.intake.save(IntakeInput(name={"parcels": "包裹隔离靶场",
                "reservations": "预约隔离靶场", "directory": "目录隔离靶场"}[kind], document=raw,
                business_notes="自建专用隔离靶场；资源不跨靶场重启保存，不能依赖历史数据。",
                generic=setup, confirmed=True), "lab_operator")
            result["projects"][kind] = item["id"]
        with receipt.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
        return result
    finally:
        await service.close()
