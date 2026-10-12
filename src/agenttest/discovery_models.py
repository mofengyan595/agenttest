"""Static discovery records are deliberately not executable Scenario models."""

from typing import Literal

from pydantic import Field

from agenttest.safety import StrictModel

ScenarioKind = Literal[
    "normal",
    "required_missing",
    "enum_boundary",
    "numeric_lower",
    "numeric_upper",
    "string_min_length",
    "string_max_length",
    "type_mismatch",
    "resource_flow",
    "role_scope",
    "unsupported_schema",
]


class InputVariation(StrictModel):
    location: str
    name: str
    action: str
    value_type: str | None = None
    # No raw strings, enum literals, examples, defaults or response data are exported.
    numeric_value: int | float | None = None
    boolean_value: bool | None = None
    string_length: int | None = None
    enum_index: int | None = None


class DependencyEvidence(StrictModel):
    policy_id: str
    source_policy_id: str
    location: str
    name: str
    source_selector: str
    role_relation: str | None = None


class Candidate(StrictModel):
    candidate_id: str = ""
    operation_id: str
    operation_ids: list[str]
    method: str
    path: str
    scenario_kind: ScenarioKind
    polarity: Literal["normal", "negative", "review"]
    input_variations: list[InputVariation] = Field(default_factory=list)
    schema_basis: list[str]
    policy_refs: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    dependencies: list[DependencyEvidence] = Field(default_factory=list)
    authentication_order: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    review_status: Literal["NEEDS_REVIEW"] = "NEEDS_REVIEW"
    executable_status: Literal["STATIC_ONLY_NEGATIVE", "BLOCKED", "STATIC_VALIDATED"]
    schema_valid: bool | None = None
    policy_match: bool | None = None
    rejection_reason: str | None = None


class CoverageRatio(StrictModel):
    numerator: int
    denominator: int


class OperationCoverage(StrictModel):
    ratio: CoverageRatio
    # Old discovery artifacts omit these; absence remains unknown, not fabricated zero.
    with_normal_candidates: CoverageRatio | None = None
    static_preflight: CoverageRatio | None = None
    trusted_policy: CoverageRatio | None = None
    execution: CoverageRatio | None = None
    contract_operations: int
    missing_operation_id: list[str]
    excluded_operations: list[str]
    unsupported_operations: list[str]
    without_candidates: list[str]


class ScenarioSlot(StrictModel):
    operation_id: str
    scenario_kind: ScenarioKind
    discovered: bool


class PolicyCoverageRow(StrictModel):
    policy_id: str
    operation_id: str
    in_contract: bool
    in_scope: bool
    trusted_assertions: bool
    has_candidate: bool
    static_preflight_met: bool


class StaticCoverage(StrictModel):
    operation: OperationCoverage
    scenario: CoverageRatio
    scenario_slots: list[ScenarioSlot]
    policy: list[PolicyCoverageRow]
    # No trusted six-mechanism mapping exists in OpenAPI/Registry. Do not guess it.
    fault_mapping_status: Literal["NOT_MAPPED"] = "NOT_MAPPED"
    fault_static_involved: list[str] = Field(default_factory=list)
    fault_policy_approved: list[str] = Field(default_factory=list)
    fault_executed: list[str] = Field(default_factory=list)
    fault_detected: list[str] = Field(default_factory=list)


class DiscoveryResult(StrictModel):
    format_version: Literal["agenttest.discovery.v1"] = "agenttest.discovery.v1"
    algorithm_version: Literal["scalar-subset-v1"] = "scalar-subset-v1"
    openapi_version: str
    contract_sha256: str
    policy_sha256: str
    target_config_sha256: str | None
    business_scenario: str
    excluded_operation_ids: list[str]
    candidates: list[Candidate]
    coverage: StaticCoverage
    issues: list[str]
    http_requests: Literal[0] = 0
    model_calls: Literal[0] = 0
    tests_executed: Literal[0] = 0
