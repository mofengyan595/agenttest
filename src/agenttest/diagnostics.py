"""Closed diagnostic vocabulary. Never serialize exception text, context or inputs."""

from enum import Enum

from pydantic import BaseModel, Field, ValidationError


class Layer(str, Enum):
    JSON = "json"
    MODEL = "model"
    DSL = "dsl"
    OPENAPI = "openapi"
    POLICY = "policy"
    DEPENDENCY = "dependency"
    INTENT = "intent"


class Diagnostic(BaseModel):
    layer: Layer
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    # Paths contain only schema-owned field names and array indices; unknown names are masked.
    fields: list[str] = Field(default_factory=list, max_length=8)


class ValidationFailure(ValueError):
    def __init__(self, layer: Layer, code: str, message: str | None = None):
        super().__init__(message or code)
        self.diagnostic = Diagnostic(layer=layer, code=code)


SAFE_FIELDS = frozenset({
    "name", "steps", "id", "operation_id", "role", "policy_id", "arguments", "location",
    "value", "from_step", "select", "body", "path_parameters", "query", "upload", "field",
    "filename", "content", "poll", "read_retries", "csv_fixture_id", "poll_policy_id", "json_value",
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
    first = errors[0]["type"] if errors else ""
    suffix = types.get(first, "TYPE_OR_CONSTRAINT")
    fields = []
    for error in errors[:8]:
        fields.append(".".join(str(p) if type(p) is int and 0 <= p <= 100 else
                               p if type(p) is str and p in SAFE_FIELDS else "*"
                               for p in error["loc"]))
    return Diagnostic(layer=layer, code=layer.value.upper() + "_SCHEMA_" + suffix,
                      fields=fields)
