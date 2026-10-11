import pytest

from agenttest.contracts import ContractSummary, Operation
from agenttest.dsl import Scenario, validate_plan
from agenttest.safety import TargetConfig
from agenttest.selectors import select


def test_default_denies_writes_and_pins_localhost() -> None:
    config = TargetConfig(base_url="http://localhost:8080", allowed_operations=["create"])
    assert config.base_url == "http://127.0.0.1:8080"
    with pytest.raises(ValueError):
        config.permit("create", "POST")
    with pytest.raises(ValueError):
        config.permit("unknown", "GET")


@pytest.mark.parametrize("url", [
    "http://evil.test", "http://169.254.169.254", "http://10.0.0.1", "http://8.8.8.8",
    "http://127.0.0.1@evil.test", "http://127.0.0.1/path", "file:///tmp/a",
    "http://127.0.0.1?url=x", "http://2130706433", "http://[::]", "http://[ff02::1]",
])
def test_target_rejects_unsafe_origins(url: str) -> None:
    with pytest.raises(ValueError):
        TargetConfig(base_url=url, allowed_operations=["get"])


def test_explicit_private_isolation() -> None:
    config = TargetConfig(
        base_url="http://10.20.30.40:8080", environment="isolated",
        authorized_ips=["10.20.30.40"], allow_mutations=True, allowed_operations=["create"],
    )
    config.permit("create", "POST")
    with pytest.raises(ValueError):
        TargetConfig(allow_mutations=True, allowed_operations=["create"])


def make_plan(**overrides) -> Scenario:
    return Scenario.model_validate({"name": "test", "steps": [
        {"id": "read", "operation_id": "get", **overrides}
    ]})


def contract(security=None) -> ContractSummary:
    return ContractSummary(version="3.1.0", operations=[Operation(
        method="GET", path="/items", operation_id="get", security=security or [],
    )], security_schemes={"auth": {"type": "http", "scheme": "bearer"}})


@pytest.mark.parametrize("overrides", [
    {"operation_id": "missing"},
    {"body": {"id": {"from_step": "read", "select": "$.id"}}},
    {"body": {"id": {"from_step": "future", "select": "$.id"}}},
    {"role": "missing"}, {"path_parameters": {"id": {"value": 1}}},
])
def test_invalid_plan_rejected_before_io(overrides: dict) -> None:
    with pytest.raises(ValueError):
        validate_plan(make_plan(**overrides), contract(), TargetConfig(allowed_operations=["get"]))


def test_restricted_operation_needs_environment_credential() -> None:
    config = TargetConfig(allowed_operations=["get"], roles={"admin": {"bearer_env": "TOKEN"}})
    with pytest.raises(ValueError):
        validate_plan(make_plan(role="admin"), contract([{"auth": []}]), config, {})
    validate_plan(make_plan(role="admin"), contract([{"auth": []}]), config, {"TOKEN": "test"})


@pytest.mark.parametrize("selector", ["$..x", "$[?(@.x)]", "$.items[*]", "__import__('os')"])
def test_selector_forbids_expressions(selector: str) -> None:
    with pytest.raises(ValueError):
        select({}, selector)


def test_unknown_dsl_fields_and_selector() -> None:
    with pytest.raises(ValueError):
        make_plan(shell="echo hi")
    assert select({"items": [{"id": 7}]}, "$.items[0].id") == 7
