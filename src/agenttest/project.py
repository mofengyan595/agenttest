"""Operator-selected local project inputs and offline configuration checks."""

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, field_validator

from agenttest.contracts import MAX_DOCUMENT_BYTES, ContractSummary, load_openapi
from agenttest.credentials import CredentialVault
from agenttest.policies import PolicyRegistry
from agenttest.report import fingerprint
from agenttest.report_files import pinned_directory
from agenttest.safety import StrictModel, TargetConfig
from agenttest.task_contract import TaskContract, task_validator


class ProjectConfig(StrictModel):
    schema_version: Literal["agenttest.project.v1"]
    contract: str = Field(min_length=1, max_length=240)
    target: str = Field(min_length=1, max_length=240)
    policies: str = Field(min_length=1, max_length=240)
    task_contract: str = Field(min_length=1, max_length=240)

    @field_validator("contract", "target", "policies", "task_contract")
    @classmethod
    def local_reference(cls, value: str) -> str:
        if (PurePosixPath(value).is_absolute() or any(c in value for c in "\\:*?\x00")
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or any(p in {"", ".", ".."} for p in value.split("/"))):
            raise ValueError("Project references must stay inside the selected directory")
        return value


class ProjectIssue(StrictModel):
    component: Literal["project", "contract", "target", "policies", "task_contract", "credentials"]
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    # Only implementation-owned field paths; never echo input or exception messages.
    field: str | None = None


@dataclass(repr=False)
class ProjectInputs:
    document: bytes
    contract: ContractSummary
    target: TargetConfig
    registry: PolicyRegistry
    task: TaskContract


@dataclass(repr=False)
class ProjectCheck:
    inputs: ProjectInputs | None
    issues: list[ProjectIssue]

    @property
    def ready(self) -> bool:
        return self.inputs is not None and not self.issues

    def summary(self) -> dict:
        result = {"schema_version": "agenttest.project-check.v1",
                  "configuration_ready": self.ready,
                  "issues": [i.model_dump(exclude_none=True) for i in self.issues],
                  "http_requests": 0, "model_calls": 0}
        if self.ready:
            result["inputs"] = {
                "contract_sha256": fingerprint(self.inputs.contract.model_dump()),
                "target_config_sha256": fingerprint(self.inputs.target.model_dump()),
                "policy_sha256": self.inputs.registry.digest(),
                "task_contract_sha256": fingerprint(self.inputs.task.model_dump(mode="json"))}
        return result


def _read(path: Path, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    with pinned_directory(path.parent, ".", create=False) as directory:
        with os.fdopen(directory.open(path.name, readonly=True), "rb") as source:
            raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("File exceeds project input limit")
    return raw


def check_project(path: Path, environ: dict[str, str]) -> ProjectCheck:
    """Check local files, identities, references and required credentials; no requests.

    Readiness is configuration evidence only. Plans still pass the original policy,
    intent and execution gates; neither service availability nor task success is tested.
    """
    issues = []

    def issue(component, code, field=None):
        issues.append(ProjectIssue(component=component, code=code, field=field))

    def read(component, filename, parse, limit=MAX_DOCUMENT_BYTES):
        try:
            raw = _read(filename, limit)
            return raw, parse(raw)
        except FileNotFoundError:
            issue(component, "PROJECT_FILE_MISSING")
        except (OSError, ValueError, TypeError, RecursionError):
            issue(component, "PROJECT_INPUT_REJECTED")
        return None, None

    path = path.absolute()
    if ".." in path.parts:
        issue("project", "PROJECT_INPUT_REJECTED")
        return ProjectCheck(None, issues)
    _, project = read("project", path, ProjectConfig.model_validate_json, 16_000)
    if project is None:
        return ProjectCheck(None, issues)
    loaded, document = {}, None
    for component, parse in (("contract", load_openapi),
                             ("target", TargetConfig.model_validate_json),
                             ("policies", PolicyRegistry.from_json),
                             ("task_contract", TaskContract.model_validate_json)):
        raw, loaded[component] = read(component, path.parent / getattr(project, component), parse)
        if component == "contract":
            document = raw
    if any(value is None for value in loaded.values()):
        return ProjectCheck(None, issues)
    contract, target, registry, task = (loaded[name] for name in (
        "contract", "target", "policies", "task_contract"))
    for field, actual in (("contract_sha256", fingerprint(contract.model_dump())),
                          ("target_config_sha256", fingerprint(target.model_dump())),
                          ("policy_sha256", registry.digest())):
        if getattr(task, field) != actual:
            issue("task_contract", "PROJECT_VERSION_MISMATCH", field)
    if issues:
        return ProjectCheck(None, issues)
    task_validator(task, contract, target, registry)
    operations = {op.operation_id: op for op in contract.operations if op.operation_id}
    for index, name in enumerate(target.allowed_operations):
        if name not in operations:
            issue("target", "PROJECT_OPERATION_UNKNOWN", f"allowed_operations[{index}]")
    definitions = {p.id: p for p in registry.definitions()}
    if len(task.requirements.steps) > target.max_steps:
        issue("target", "PROJECT_STEP_BUDGET_TOO_SMALL", "max_steps")
    if len(task.requirements.steps) > target.max_requests:
        issue("target", "PROJECT_REQUEST_BUDGET_TOO_SMALL", "max_requests")
    for index, step in enumerate(task.requirements.steps):
        field = f"requirements.steps[{index}]"
        op, policy = operations.get(step.operation_id), definitions.get(step.policy_id)
        if op is None:
            issue("task_contract", "PROJECT_OPERATION_UNKNOWN", field + ".operation_id")
            continue
        try:
            target.permit(step.operation_id, op.method)
        except ValueError:
            issue("target", "PROJECT_OPERATION_NOT_PERMITTED", field + ".operation_id")
        if (policy is None or policy.operation_id != step.operation_id
                or step.role not in policy.roles
                or policy.business_scenario != target.business_scenario
                or not policy.assertions.reviewed or not policy.assertions.source.strip()
                or not (policy.assertions.status or policy.assertions.fields)):
            issue("policies", "PROJECT_POLICY_REFERENCE_REJECTED", field + ".policy_id")
        role = target.roles.get(step.role)
        if step.role is not None and role is None:
            issue("target", "PROJECT_ROLE_UNKNOWN", field + ".role")
            continue
        is_login = role is not None and step.operation_id == role.login_operation
        if not is_login and any(step.operation_id == r.login_operation
                                for r in target.roles.values()):
            issue("target", "PROJECT_AUTHENTICATION_ROLE_REQUIRED", field + ".role")
        protected = bool(op.security) and {} not in op.security
        if protected and not any(
            len(req) == 1 and contract.security_schemes[next(iter(req))].get("type") == "http"
            and contract.security_schemes[next(iter(req))].get("scheme", "").lower() == "bearer"
            for req in op.security
        ):
            issue("contract", "PROJECT_AUTHENTICATION_UNSUPPORTED", field + ".operation_id")
        login_planned = role is not None and any(
            s.role == step.role and s.operation_id == role.login_operation
            for s in task.requirements.steps)
        if is_login:
            if not all(environ.get(name or "") for name in (role.username_env, role.password_env)):
                issue("credentials", "PROJECT_LOGIN_CREDENTIALS_MISSING", field + ".role")
        elif protected and role is None:
            issue("target", "PROJECT_AUTHENTICATION_ROLE_REQUIRED", field + ".role")
        elif protected and not login_planned:
            bearer = environ.get(role.bearer_env or "", "")
            if not bearer or any(c in bearer for c in "\r\n"):
                issue("credentials", "PROJECT_BEARER_CREDENTIAL_MISSING_OR_INVALID",
                      field + ".role")
    vault = CredentialVault(target, environ)
    if vault.contains_secret(task.user_task):
        issue("task_contract", "PROJECT_TASK_CONTAINS_CREDENTIAL", "user_task")
    vault.close()
    inputs = None if issues else ProjectInputs(document, contract, target, registry, task)
    return ProjectCheck(inputs, issues)
