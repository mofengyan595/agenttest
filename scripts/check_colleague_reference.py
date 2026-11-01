"""Offline preconditions for the disposable web acceptance, never product planning context."""

import hashlib
import json

from agenttest.contracts import load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.request_validation import request_schemas, validate_value


def check_literal_preconditions(task):
    """Keep frozen literal oracles consistent with the task and declared contract examples."""
    requirements = task["contract_requirements"]
    source = task["request"] + "\n" + task.get("followup", "")
    for check in task.get("literal_checks", []):
        matches = [r for r in requirements if r["operation_id"] == check["operation_id"]
                   and r["location"] == check["slot"] and r["name"] == check["name"]
                   and not r.get("path")]
        value = check["value"]
        if not any(type(item) is type(value) and item == value
                   for r in matches for item in r.get("invalid_values", [])):
            raise ValueError("REFERENCE_LITERAL_PRECONDITION_MISMATCH:" + task["project"])
        text = value if type(value) is str else json.dumps(value, ensure_ascii=False)
        if text not in source:
            raise ValueError("REFERENCE_LITERAL_SOURCE_MISMATCH:" + task["project"])
    choice = task.get("input_choice")
    if choice is not None:
        if (choice not in task["request"] or task.get("initial_body", {}).get("title") != choice
                or not any(r["name"] == "title" and choice in r.get("valid_values", [])
                           for r in requirements)):
            raise ValueError("REFERENCE_INPUT_CHOICE_MISMATCH:" + task["project"])
    return len(task.get("literal_checks", []))


def check_reference(manifest, documents=None):
    if not manifest.get("require_contract_preflight"):
        return dict(classification="LEGACY_REFERENCE_CHECK_NOT_DECLARED", model_calls=0)
    if documents is None:
        from serve_colleague_targets import document

        documents = {task["project"]: document(task["project"]) for task in manifest["tasks"]}
    checked = []
    for task in manifest["tasks"]:
        name = task["project"]
        raw = json.dumps(documents[name], ensure_ascii=False).encode()
        contract = load_openapi(raw)
        if len(contract.operations) != task["operation_denominator"]:
            raise ValueError("REFERENCE_OPERATION_DENOMINATOR_MISMATCH:" + name)
        operations = {operation.operation_id: operation for operation in contract.operations}
        if not task.get("contract_requirements"):
            raise ValueError("REFERENCE_PRECONDITIONS_MISSING:" + name)
        for requirement in task["contract_requirements"]:
            operation = operations[requirement["operation_id"]]
            parameters, body = request_schemas(operation,
                "application/json" if operation.request_body else None)
            location = requirement["location"]
            schema = ((body or {}).get("properties", {}).get(requirement["name"])
                if location == "body" else parameters[
                    "path" if location == "path_parameters" else location].get(requirement["name"]))
            for field in requirement.get("path", []):
                schema = (schema or {}).get("properties", {}).get(field)
            if schema is None:
                raise ValueError("REFERENCE_FIELD_NOT_DOCUMENTED:" + ":".join([
                    name, operation.operation_id, location, requirement["name"]]))
            if not set(requirement.get("statuses", [])) <= {int(status)
                    for status in operation.responses if status.isdigit()}:
                raise ValueError("REFERENCE_STATUS_NOT_DOCUMENTED:" + operation.operation_id)
            for value in requirement.get("valid_values", []):
                validate_value(value, schema)
            invalid = list(requirement.get("invalid_values", []))
            if requirement.get("invalid_lengths") and schema.get("type") != "string":
                raise ValueError("REFERENCE_LENGTH_REQUIRES_STRING:" + operation.operation_id)
            invalid.extend("x" * length for length in requirement.get("invalid_lengths", []))
            for value in invalid:
                try:
                    validate_value(value, schema)
                except ValidationFailure:
                    continue
                raise ValueError("REFERENCE_EXPECTED_INVALID_VALUE_IS_VALID:"
                                 + operation.operation_id)
        record = dict(project=name, operations=len(operations),
            document_sha256=hashlib.sha256(raw).hexdigest(),
            checked_fields=len(task["contract_requirements"]))
        if manifest.get("require_literal_preflight"):
            record["checked_literal_assertions"] = check_literal_preconditions(task)
        checked.append(record)
    return dict(classification="OFFLINE_REFERENCE_PRECONDITIONS_PASSED", projects=checked,
                model_calls=0, target_http_requests=0)
