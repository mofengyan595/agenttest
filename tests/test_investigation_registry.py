import json

import pytest
from pydantic import ValidationError

from agenttest.demo import PROJECT_ROOT
from agenttest.investigation import (
    AnalyzerSelection,
    InvestigationBudget,
    InvestigationConfig,
    ReadOnlyCapabilityRegistry,
)


def test_defaults_grant_nothing():
    config = InvestigationConfig()
    assert not config.enabled
    assert config.budget == InvestigationBudget()
    assert all(v == 0 for v in config.budget.model_dump().values())


def test_operator_registry_is_a_snapshot():
    registry = ReadOnlyCapabilityRegistry.from_json(
        (PROJECT_ROOT / "examples/nl-readonly-capabilities.json").read_bytes())
    entries = registry.entries()
    assert {c.operation_id for c in entries} == {"getCourse"}
    entries[0].policy_id = "forged"
    assert registry.snapshot().entries()[0].policy_id == "nl_read_v1"
    with pytest.raises(ValueError):
        ReadOnlyCapabilityRegistry(registry.entries() * 2)


@pytest.mark.parametrize("field", [
    "url", "headers", "resource_id", "run_id", "policy_id", "assertions", "reviewed",
    "source_selector", "operation_id", "budget", "expected_status", "from_step",
])
def test_selection_cannot_supply_authority(field):
    data = dict(capability_id="course_owner_read", role="alice", resource_ref="a" * 32)
    data[field] = "untrusted"
    with pytest.raises(ValidationError):
        AnalyzerSelection.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field,value", [
    ("max_requests", 2), ("max_rounds", 2), ("max_model_calls", 2), ("timeout", 31.0),
])
def test_minimal_budget_is_bounded(field, value):
    with pytest.raises(ValidationError):
        InvestigationBudget.model_validate({field: value})
