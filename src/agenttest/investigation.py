"""Operator-owned read-only capabilities; analyzer output grants no authority."""

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from agenttest.safety import StrictModel


class InvestigationBudget(StrictModel):
    # The first milestone permits at most one selection and one HTTP attempt.
    max_requests: int = Field(default=0, ge=0, le=1)
    max_rounds: int = Field(default=0, ge=0, le=1)
    max_model_calls: int = Field(default=0, ge=0, le=1)
    timeout: float = Field(default=0.0, ge=0, le=30)


class ReadOnlyCapability(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
    operation_id: str
    roles: list[str] = Field(min_length=1)
    policy_id: str
    business_scenario: str
    source_policy_id: str
    source_selector: str
    path_parameter: str
    effect: Literal["read_only"]
    no_side_effect_basis: str = Field(min_length=10, max_length=1000)


class ReadOnlyCapabilityRegistry:
    """Only operator-selected files belong here, never analyzer-generated approvals."""

    def __init__(self, entries: list[ReadOnlyCapability] = ()) -> None:
        self._entries: dict[str, str] = {}
        for entry in entries:
            if entry.id in self._entries:
                raise ValueError("Duplicate read-only capability")
            self._entries[entry.id] = entry.model_dump_json()

    @classmethod
    def from_json(cls, raw: bytes | str) -> "ReadOnlyCapabilityRegistry":
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("Read-only capability registry must be a list")
        return cls([ReadOnlyCapability.model_validate(item) for item in data])

    def entries(self) -> list[ReadOnlyCapability]:
        return [ReadOnlyCapability.model_validate_json(raw) for raw in self._entries.values()]

    def snapshot(self) -> "ReadOnlyCapabilityRegistry":
        return ReadOnlyCapabilityRegistry(self.entries())


class InvestigationConfig(StrictModel):
    enabled: bool = False
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)


class AnalyzerSelection(StrictModel):
    capability_id: str = Field(max_length=61)
    role: str = Field(max_length=61)
    # An opaque, ephemeral handle, not a resource ID or a reusable step ID.
    resource_ref: str = Field(pattern=r"^[a-f0-9]{32}$")


@dataclass
class FakeAnalyzer:
    """Select from a filtered catalog; fixture overrides are still untrusted JSON."""

    response: str | None = None
    calls: int = 0
    contexts: list[dict] = field(default_factory=list)

    async def generate(self, context: dict) -> str:
        self.calls += 1
        self.contexts.append(json.loads(json.dumps(context)))
        if self.response is not None:
            return self.response
        return json.dumps(context["choices"][0])
