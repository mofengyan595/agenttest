"""Frozen acceptance oracle preconditions; never provided to a product planner."""

import json
from pathlib import Path

from serve_colleague_targets import document

from agenttest.contracts import load_openapi
from agenttest.request_validation import request_schemas, validate_value


def preflight(manifest):
    tasks = manifest["tasks"]
    if (manifest["rounds"] != 3 or len(tasks) != 3
            or {t["project"] for t in tasks} != {"parcels", "reservations", "directory"}
            or len({t["id"] for t in tasks}) != 3 or len({t["request"] for t in tasks}) != 3):
        raise ValueError("FROZEN_THREE_NEW_REQUESTS_THREE_ROUNDS_REQUIRED")
    root = Path(__file__).resolve().parents[1]
    old = {t["request"] for path in (root / "docs").glob("testing-colleague-acceptance-v*.json")
           for t in json.loads(path.read_bytes())["tasks"]}
    if any(t["request"] in old for t in tasks):
        raise ValueError("HISTORICAL_REQUEST_CANNOT_BE_NEW_QUALITY_EVIDENCE")
    checked = []
    for task in tasks:
        contract = load_openapi(json.dumps(document(task["project"])))
        operations = {o.operation_id: o for o in contract.operations}
        if set(task["required_operations"]) - operations.keys():
            raise ValueError("REFERENCE_OPERATION_NOT_DOCUMENTED")
        if "initial_body" in task:
            operation = operations["createParcel" if task["project"] == "parcels"
                                   else "createReservation"]
            _, schema = request_schemas(operation, "application/json")
            validate_value(task["initial_body"], schema)
            if "negative_missing_field" in task:
                field = task["negative_missing_field"]
                if field not in schema["required"] or str(task["negative_status"]) not in (
                        operation.responses):
                    raise ValueError("REFERENCE_NEGATIVE_NOT_DOCUMENTED")
                if str(task["approved_reader_status"]) not in operations[
                        "readReservation"].responses:
                    raise ValueError("REFERENCE_IDENTITY_EXPECTATION_NOT_DOCUMENTED")
        if "update_body" in task:
            _, schema = request_schemas(operations["changeParcel"], "application/json")
            validate_value(task["update_body"], schema)
        if "query" in task:
            schemas, _ = request_schemas(operations["readSection12"], None)
            for name, value in task["query"].items():
                validate_value(value, schemas["query"][name])
        checked.append(dict(id=task["id"], operations=len(contract.operations), checked=True))
    return dict(tasks=checked, model_calls=0, target_requests=0,
                limitation="New requests on existing fixtures; no new-project generalization claim")
