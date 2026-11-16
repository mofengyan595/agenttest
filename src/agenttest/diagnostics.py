"""Closed diagnostic vocabulary. Never serialize exception text, context or inputs."""

import re
from enum import Enum
from typing import Any, Literal

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
    # Bounded repair facts the model needs: instance/operation ids, missing dependencies and a
    # deterministic hint. Never request or response values; callers trim before assigning.
    detail: dict[str, Any] = Field(default_factory=dict, max_length=12)

    @model_serializer(mode="wrap")
    def serialize_reference(self, handler):
        data = handler(self)
        if self.reference is None:
            data.pop("reference", None)
        if self.intent is None:
            data.pop("intent", None)
        if not self.detail:
            data.pop("detail", None)
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

# A schema *shape* rejection can only be repaired if the model learns which key it invented or
# omitted. Extra and missing keys are echoed when they look like identifiers (no spaces, quotes,
# punctuation, or value-shaped tokens), and never in any other diagnostic family.
SHAPE_ECHO_TYPES = frozenset({"extra_forbidden", "missing"})
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
MAX_ECHOED_KEYS = 4


def presentation_language(text):
    """Report the prose language of model text without rewriting or gating it.

    Presentation is separate from structure, authorization and safety failures: a
    non-Chinese explanation is recorded, and the original words stay traceable.
    """
    body = (text or '').strip()
    if not body:
        return dict(state="EMPTY", preserved=True)
    return dict(state="CHINESE" if re.search(r'[\u4e00-\u9fff]', body) else "NON_CHINESE",
                has_latin=bool(re.search(r'[A-Za-z]', body)), preserved=True)


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
    fields, extra_keys, missing_keys = [], [], []
    for error in errors[:8]:
        echo = error["type"] in SHAPE_ECHO_TYPES
        segments = []
        for part in error["loc"]:
            if type(part) is int and 0 <= part <= 100:
                segments.append(str(part))
            elif type(part) is str and (part in SAFE_FIELDS
                                        or (echo and IDENTIFIER.match(part))):
                segments.append(part)
                if part not in SAFE_FIELDS:
                    (extra_keys if error["type"] == "extra_forbidden"
                     else missing_keys).append(part)
            else:
                segments.append("*")
        fields.append(".".join(segments))
    diagnostic = Diagnostic(layer=layer, code=layer.value.upper() + "_SCHEMA_" + suffix,
                            fields=fields)
    if extra_keys:
        diagnostic.detail = dict(unknown_fields=sorted(set(extra_keys))[:MAX_ECHOED_KEYS])
    elif missing_keys:
        diagnostic.detail = dict(missing_fields=sorted(set(missing_keys))[:MAX_ECHOED_KEYS])
    return diagnostic
