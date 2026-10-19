"""Closed diagnostic vocabulary. Never serialize exception text, context or inputs."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_serializer


class Layer(str, Enum):
    JSON = "json"
    MODEL = "model"
    DSL = "dsl"
    OPENAPI = "openapi"
    POLICY = "policy"
    DEPENDENCY = "dependency"
    INTENT = "intent"


class ReferenceDiagnostic(BaseModel):
    """Closed branch and numeric locations only; no model-provided identifiers or values."""

    branch: Literal["SELECTOR_SYNTAX", "SOURCE_NOT_PREVIOUS", "SOURCE_UNKNOWN",
                    "SOURCE_POLICY", "SOURCE_OPERATION", "SELECTOR_POLICY",
                    "CONSUMER_POLICY", "CONSUMER_OPERATION", "ARGUMENT_SLOT", "LITERAL_SLOT"]
    step_index: int = Field(ge=0, le=19)
    argument_index: int = Field(ge=0, le=29)


class Diagnostic(BaseModel):
    layer: Layer
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    # Paths contain only schema-owned field names and array indices; unknown names are masked.
    fields: list[str] = Field(default_factory=list, max_length=8)
    reference: ReferenceDiagnostic | None = None
    # Closed rule families and operator requirement indexes, never values or rubric keys.
    intent: list["IntentDiagnostic"] | None = Field(default=None, max_length=8)

    @model_serializer(mode="wrap")
    def serialize_reference(self, handler):
        data = handler(self)
        if self.reference is None:
            data.pop("reference", None)
        if self.intent is None:
            data.pop("intent", None)
        return data


class IntentDiagnostic(BaseModel):
    branch: Literal["STEP_COUNT", "OPERATION_ROLE_POLICY", "PARAMETERS", "SOURCE_OR_VALUE",
                    "ORDER", "EXECUTION_OPTION", "GOAL_COVERAGE", "STEP_SET"]
    requirement_index: int | None = Field(default=None, ge=0, le=19)


class ValidationFailure(ValueError):
    def __init__(self, layer: Layer, code: str, message: str | None = None):
        super().__init__(message or code)
        self.diagnostic = Diagnostic(layer=layer, code=code)


SAFE_FIELDS = frozenset({
    "name", "steps", "id", "operation_id", "role", "policy_id", "arguments", "location",
    "value", "from_step", "select", "body", "path_parameters", "query", "upload", "field",
    "filename", "content", "poll", "read_retries", "csv_fixture_id", "poll_policy_id",
})


def schema_diagnostic(exc: ValidationError, layer: Layer) -> Diagnostic:
    """Use Pydantic types/locations only, with a closed mapping and masked dictionary keys."""
    errors = exc.errors(include_input=False, include_context=False, include_url=False)
    types = {"extra_forbidden": "EXTRA_FIELD", "missing": "MISSING_FIELD",
             "value_error": "VALIDATOR_REJECTED",
             "composed_policy_mismatch": "COMPOSED_POLICY_MISMATCH",
             "composed_role_unapproved": "COMPOSED_ROLE_UNAPPROVED",
             "composed_csv_reference": "COMPOSED_CSV_REFERENCE",
             "composed_authentication": "COMPOSED_AUTHENTICATION",
             "composed_poll_reference": "COMPOSED_POLL_REFERENCE"}
    types.update({"generic_argument_source": "GENERIC_ARGUMENT_SOURCE",
                  "generic_container_type": "GENERIC_CONTAINER_TYPE",
                  "generic_nonfinite": "GENERIC_NONFINITE",
                  "generic_duplicate_member": "GENERIC_DUPLICATE_MEMBER",
                  "generic_container_limit": "GENERIC_CONTAINER_LIMIT"})
    first = errors[0]["type"] if errors else ""
    suffix = types.get(first, "TYPE_OR_CONSTRAINT")
    fields = []
    for error in errors[:8]:
        fields.append(".".join(str(p) if type(p) is int and 0 <= p <= 100 else
                               p if type(p) is str and p in SAFE_FIELDS else "*"
                               for p in error["loc"]))
    return Diagnostic(layer=layer, code=layer.value.upper() + "_SCHEMA_" + suffix,
                      fields=fields)
