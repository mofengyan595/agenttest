"""Human-authored plans only. Validate all dependencies and permissions before I/O."""

import os
import re
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import Field, field_validator, model_validator

from agenttest.contracts import ContractSummary, Operation
from agenttest.safety import StrictModel, TargetConfig
from agenttest.selectors import validate_selector


class ArgumentBinding(StrictModel):
    value: Any = None
    from_step: str | None = None
    select: str = "$"
    credential: Literal["username", "password"] | None = None

    @field_validator("select")
    @classmethod
    def safe_selector(cls, value: str) -> str:
        return validate_selector(value)

    @model_validator(mode="after")
    def one_source(self) -> "ArgumentBinding":
        if sum(("value" in self.model_fields_set, self.from_step is not None,
                self.credential is not None)) != 1:
            raise ValueError("Binding requires exactly one source")
        return self


class ExpectedStatus(StrictModel):
    codes: list[int] = Field(min_length=1, max_length=20)

    @field_validator("codes")
    @classmethod
    def valid_codes(cls, value: list[int]) -> list[int]:
        if any(code < 100 or code > 599 for code in value):
            raise ValueError("Invalid expected HTTP status")
        return value


class FieldAssertion(StrictModel):
    select: str
    operator: Literal["equals", "exists", "count", "in"] = "equals"
    expected: Any = None

    @field_validator("select")
    @classmethod
    def safe_selector(cls, value: str) -> str:
        return validate_selector(value)

    @model_validator(mode="after")
    def expectation_shape(self) -> "FieldAssertion":
        if self.operator != "exists" and "expected" not in self.model_fields_set:
            raise ValueError("Assertion requires an explicit expected value")
        if self.operator == "count" and (
            type(self.expected) is not int or self.expected < 0
        ):
            raise ValueError("Count must be a nonnegative integer")
        if self.operator == "in" and not isinstance(self.expected, list):
            raise ValueError("Membership assertion requires a list")
        return self


class AssertionPolicy(StrictModel):
    reviewed: bool = False
    source: str = ""
    status: ExpectedStatus | None = None
    fields: list[FieldAssertion] = Field(default_factory=list, max_length=50)


class PollPolicy(StrictModel):
    select: str
    terminal_values: list[str] = Field(min_length=1, max_length=10)
    max_attempts: int = Field(default=5, ge=1, le=20)
    interval: float = Field(default=0.01, ge=0, le=5)

    @field_validator("select")
    @classmethod
    def safe_selector(cls, value: str) -> str:
        return validate_selector(value)


class CSVUpload(StrictModel):
    field: str = Field(default="file", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    filename: str = Field(default="input.csv", pattern=r"^[A-Za-z0-9_-]+\.csv$")
    content: str = Field(max_length=100_000)


class Step(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
    operation_id: str
    role: str | None = None
    auth_from: ArgumentBinding | None = None
    path_parameters: dict[str, ArgumentBinding] = Field(default_factory=dict)
    query: dict[str, ArgumentBinding] = Field(default_factory=dict)
    body: dict[str, ArgumentBinding] = Field(default_factory=dict)
    upload: CSVUpload | None = None
    assertions: AssertionPolicy = Field(default_factory=AssertionPolicy)
    poll: PollPolicy | None = None
    read_retries: int = Field(default=0, ge=0, le=2)


class Scenario(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    steps: list[Step] = Field(min_length=1, max_length=100)


def validate_plan(
    scenario: Scenario, contract: ContractSummary, config: TargetConfig,
    environ: dict[str, str] | None = None,
) -> dict[str, Operation]:
    environ = dict(os.environ) if environ is None else environ
    if len(scenario.steps) > config.max_steps:
        raise ValueError("Step limit exceeded")
    operations = {op.operation_id: op for op in contract.operations if op.operation_id}
    seen: dict[str, Step] = {}
    for step in scenario.steps:
        if step.id in seen or step.operation_id not in operations:
            raise ValueError("Duplicate step id or unknown operationId")
        operation = operations[step.operation_id]
        config.permit(step.operation_id, operation.method)
        decoded = unquote(unquote(operation.path))
        if (any(segment in {".", ".."} for segment in decoded.split("/"))
                or any(c in decoded for c in "?#\\\r\n") or decoded.startswith("//")):
            raise ValueError("Unsafe operation path")
        placeholders = set(re.findall(r"\{([^{}]+)\}", operation.path))
        if set(step.path_parameters) != placeholders:
            raise ValueError("Path parameters must match operation placeholders")
        if step.role is not None and step.role not in config.roles:
            raise ValueError("Unknown role alias")
        role = config.roles.get(step.role)
        bindings = [*step.path_parameters.values(), *step.query.values(), *step.body.values()]
        if step.auth_from:
            if not step.auth_from.from_step:
                raise ValueError("Authentication must bind a previous step or role environment")
            bindings.append(step.auth_from)
        for binding in bindings:
            if binding.from_step and binding.from_step not in seen:
                raise ValueError("Dependency must reference an earlier step (no cycles)")
            if binding.credential:
                env_name = getattr(role, f"{binding.credential}_env", None)
                if not env_name or not environ.get(env_name):
                    raise ValueError("Missing role credential environment variable")
        requires_auth = bool(operation.security) and {} not in operation.security
        if requires_auth:
            supported = any(
                len(req) == 1 and contract.security_schemes[next(iter(req))].get("type") == "http"
                and contract.security_schemes[next(iter(req))].get("scheme", "").lower() == "bearer"
                for req in operation.security
            )
            if not supported:
                raise ValueError("Stage 1 supports HTTP bearer authentication only")
            if not role:
                raise ValueError("Restricted operation needs a role alias")
            if step.auth_from:
                if seen[step.auth_from.from_step].role != step.role:
                    raise ValueError("Credential source role does not match step role")
            elif not role.bearer_env or not environ.get(role.bearer_env):
                raise ValueError("Restricted operation lacks a credential")
        if operation.method not in {"GET", "HEAD"} and (step.poll or step.read_retries):
            raise ValueError("Mutations cannot be polled or retried")
        if step.upload and step.body:
            raise ValueError("JSON and multipart bodies cannot be combined")
        if step.upload or step.body:
            media = "multipart/form-data" if step.upload else "application/json"
            if not operation.request_body or media not in operation.request_body["content"]:
                raise ValueError("Body media type is not declared in contract")
        for param in operation.parameters:
            if param.get("required"):
                location = {"path": step.path_parameters, "query": step.query}.get(param["in"], {})
                if param["name"] not in location:
                    raise ValueError("Missing required operation parameter")
        if operation.request_body and operation.request_body.get("required"):
            if not step.body and not step.upload:
                raise ValueError("Missing required request body")
        seen[step.id] = step
    return operations
