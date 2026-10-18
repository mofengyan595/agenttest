"""Deterministic review drafts; these objects never grant policy or target authority."""

import json
import os
from pathlib import Path
from typing import Literal

from agenttest.contracts import ContractSummary
from agenttest.credentials import CredentialVault
from agenttest.discovery import discover
from agenttest.planner_input import PRIVATE_LITERAL, validate_user_task
from agenttest.policies import PolicyRegistry
from agenttest.project import check_project
from agenttest.report import fingerprint
from agenttest.report_files import pinned_directory
from agenttest.safety import StrictModel


class ReviewSlot(StrictModel):
    field: str
    scope: Literal["project", "task"]
    reason: Literal["TARGET_AUTHORIZATION_UNKNOWN", "ROLES_UNKNOWN", "EXPECTATIONS_UNKNOWN",
                    "RESOURCE_RELATIONSHIPS_UNKNOWN", "TASK_REQUIREMENTS_UNKNOWN"]


class ProjectDraft(StrictModel):
    schema_version: Literal["agenttest.project-draft.v1"] = "agenttest.project-draft.v1"
    review_status: Literal["NEEDS_REVIEW"] = "NEEDS_REVIEW"
    execution_authorized: Literal[False] = False
    contract_sha256: str
    operations: list[dict]
    security_schemes: dict
    missing: list[ReviewSlot]
    discovery_summary: dict


class TaskDraft(StrictModel):
    schema_version: Literal["agenttest.task-draft.v1"] = "agenttest.task-draft.v1"
    review_status: Literal["NEEDS_REVIEW"] = "NEEDS_REVIEW"
    execution_authorized: Literal[False] = False
    user_task: str
    contract_sha256: str
    target_config_sha256: str
    policy_sha256: str
    requirements: None = None
    available_policies: list[dict]
    missing: list[ReviewSlot]


def schema_facts(schema: dict) -> dict:
    """Retain structural facts; examples, defaults, prose and string enum values stay private."""
    result = {}
    for name in ("type", "required", "nullable", "minimum", "maximum", "exclusiveMinimum",
                 "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems"):
        if name in schema:
            result[name] = schema[name]
    if "enum" in schema:
        result["enum_count"] = len(schema["enum"])
    if isinstance(schema.get("properties"), dict):
        result["properties"] = {name: schema_facts(child)
                                for name, child in schema["properties"].items()}
    if isinstance(schema.get("items"), dict):
        result["items"] = schema_facts(schema["items"])
    return result


def _public_metadata(data) -> None:
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and (len(value) > 8000 or PRIVATE_LITERAL.search(value)
                or any(ord(character) < 32 and character not in "\n\t" for character in value)):
            raise ValueError("DRAFT_UNSAFE_METADATA")


def prepare_project(contract: ContractSummary) -> ProjectDraft:
    """Discovery uses an empty Registry; a contract's security metadata is never a role approval."""
    candidates = discover(contract, PolicyRegistry())
    operations = []
    for operation in contract.operations:
        operations.append({"operation_id": operation.operation_id, "method": operation.method,
            "path": operation.path,
            "parameters": [{"name": parameter["name"], "in": parameter["in"],
                "required": parameter.get("required", False),
                "schema": schema_facts(parameter.get("schema", {}))}
                for parameter in operation.parameters],
            "request_body": {media: schema_facts(body.get("schema", {})) for media, body in
                (operation.request_body or {}).get("content", {}).items()},
            "declared_response_codes": sorted(operation.responses), "security": operation.security})
    draft = ProjectDraft(contract_sha256=fingerprint(contract.model_dump()), operations=operations,
        security_schemes={name: {key: value for key, value in definition.items()
                                 if key in {"type", "scheme"}}
                          for name, definition in contract.security_schemes.items()}, missing=[
            ReviewSlot(field="target_authorization", scope="project",
                       reason="TARGET_AUTHORIZATION_UNKNOWN"),
            ReviewSlot(field="roles", scope="project", reason="ROLES_UNKNOWN"),
            ReviewSlot(field="business_expectations", scope="project",
                       reason="EXPECTATIONS_UNKNOWN"),
            ReviewSlot(field="resource_relationships", scope="project",
                       reason="RESOURCE_RELATIONSHIPS_UNKNOWN"),
            ReviewSlot(field="requirements", scope="task", reason="TASK_REQUIREMENTS_UNKNOWN")],
        discovery_summary={"candidates": len(candidates.candidates),
            "operation_coverage": candidates.coverage.operation.ratio.model_dump(),
            "http_requests": 0, "model_calls": 0, "execution_authorized": False})
    _public_metadata(draft.model_dump())
    return draft


def prepare_task(project: Path, user_task: str, environ: dict[str, str]) -> TaskDraft:
    """Compute checked project identities, leaving new expectations pending."""
    user_task = validate_user_task(user_task)
    checked = check_project(project, environ)
    if not checked.ready:
        raise ValueError("TASK_DRAFT_PROJECT_NOT_READY")
    inputs = checked.inputs
    available = [{"policy_id": policy.id, "operation_id": policy.operation_id,
                  "roles": policy.roles,
                  "bindings": [{"location": rule.location, "name": rule.name,
                      "literal_schema": schema_facts(rule.literal_schema)
                          if rule.literal_schema is not None else None,
                      "source_policy_id": rule.source_policy_id,
                      "source_selector": rule.source_selector, "role_relation": rule.role_relation}
                      for rule in policy.binding_rules]}
                 for policy in inputs.registry.definitions()
                 if policy.business_scenario == inputs.target.business_scenario]
    draft = TaskDraft(user_task=user_task,
        contract_sha256=fingerprint(inputs.contract.model_dump()),
        target_config_sha256=fingerprint(inputs.target.model_dump()),
        policy_sha256=inputs.registry.digest(), available_policies=available,
        missing=[ReviewSlot(field="requirements.steps", scope="task",
                            reason="TASK_REQUIREMENTS_UNKNOWN"),
                 ReviewSlot(field="requirements.before", scope="task",
                            reason="TASK_REQUIREMENTS_UNKNOWN"),
                 ReviewSlot(field="requirements.required_goals", scope="task",
                            reason="TASK_REQUIREMENTS_UNKNOWN")])
    _public_metadata(draft.model_dump())
    vault = CredentialVault(inputs.target, environ)
    try:
        if vault.contains_secret(draft.model_dump()):
            raise ValueError("TASK_DRAFT_CREDENTIAL_LITERAL")
    finally:
        vault.close()
    return draft


def write_draft(draft: ProjectDraft | TaskDraft, output: Path) -> None:
    raw = (json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n").encode()
    if len(raw) > 2_000_000:
        raise ValueError("DRAFT_SIZE_LIMIT")
    path = output.absolute()
    with pinned_directory(path.parent, ".", create=True) as directory:
        with os.fdopen(directory.open(path.name, create=True), "wb") as stream:
            stream.write(raw)
