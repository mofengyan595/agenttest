"""One implementation-owned tool vocabulary for protocol schemas and permission metadata."""

from dataclasses import dataclass

from pydantic import Field

from agenttest.safety import StrictModel


class EmptyRequest(StrictModel):
    pass


class ValidateRequest(StrictModel):
    plan_json: str = Field(min_length=1, max_length=64_000)


class RunRequest(StrictModel):
    scenario_id: str = Field(min_length=1, max_length=64)
    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ReportRequest(StrictModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    purpose: str
    permission: str
    resource_relation: str
    result_type: str
    request_type: type[StrictModel] | None = None

    def public(self):
        return dict(name=self.name, purpose=self.purpose, permission=self.permission,
            resource_relation=self.resource_relation, result_type=self.result_type,
            parameters=self.request_type.model_json_schema() if self.request_type else None)


TOOLS = {t.name: t for t in (
    ToolDefinition("list_tests", "List approved disposable scenarios", "read_metadata",
        "operator_selected_project", "scenario_catalog", EmptyRequest),
    ToolDefinition("get_contract", "Read public contract and policy references", "read_metadata",
        "operator_selected_project", "public_contract", EmptyRequest),
    ToolDefinition("validate_plan", "Validate DSL without executing HTTP", "validate_only",
        "registered_operations_and_bindings", "validation_result", ValidateRequest),
    ToolDefinition("run_scenario", "Execute an approved disposable scenario", "isolated_execution",
        "invocation_id_once", "redacted_run_report", RunRequest),
    ToolDefinition("read_report", "Read a completed redacted report", "read_evidence",
        "service_owned_run_id", "redacted_run_report", ReportRequest),
    ToolDefinition("submit_test_plan", "Propose a test plan; local gates decide execution",
        "propose_only", "registered_operations_roles_policies_and_dependencies", "untrusted_dsl"),
)}

REQUESTS = {name: tool.request_type for name, tool in TOOLS.items() if tool.request_type}


def native_plan_tool(schema):
    tool = TOOLS["submit_test_plan"]
    return dict(type="function", function=dict(name=tool.name, description=tool.purpose,
                                               parameters=schema, strict=True))
