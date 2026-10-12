import pytest
from fastapi.testclient import TestClient

from agenttest.main import create_app
from agenttest.policies import PolicyRegistry, RegisteredPolicy
from agenttest.report import CheckResult, Outcome, ReportStore, RunReport, StepResult, fingerprint
from agenttest.report_queries import public_report
from agenttest.verifier import verify


def policy():
    return RegisteredPolicy(id="items_v1", operation_id="items", roles=[None],
                            business_scenario="default", assertions={"reviewed": True,
                            "source": "synthetic operator", "status": {"codes": [200]}})


def with_check(check, digest="legacy"):
    return RunReport(scenario_sha256="a", contract_sha256="b", environment="isolated",
                     target_origin="http://127.0.0.1", steps=[StepResult(
                         step_id="x", operation_id="items", method="GET", path_template="/items",
                         outcome=Outcome.PASS, reason="OK", policy_sha256=digest, checks=[check])])


@pytest.mark.parametrize("selector", ["$.count", "$.phone", "$.id_card", "$.token",
                                      "$.password", "$.身份证", "$.mobile"])
def test_unqualified_legacy_numeric_fields_are_hidden(selector):
    row = with_check(CheckResult(kind="equals", selector=selector, passed=True,
                                 expected=13800138000, actual=13800138000))
    public = public_report(row)
    assert public.steps[0].checks[0].expected == "[REDACTED]"
    assert public.steps[0].checks[0].actual == "[REDACTED]"
    assert row.steps[0].checks[0].actual == 13800138000


@pytest.mark.parametrize("selector", ["$.count", "$.phone", "$.password"])
def test_independent_approval_is_required_and_sensitive_selectors_win(selector):
    p = policy()
    p.assertions.fields = []
    from agenttest.dsl import FieldAssertion

    p.assertions.fields.append(FieldAssertion(select=selector, operator="equals", expected=2))
    p.assertions.public_evidence = [selector]
    _, checks = verify(p.assertions, 200, {selector[2:]: 2})
    row = with_check(checks[-1], fingerprint(p.model_dump()))
    expected = 2 if selector == "$.count" else "[REDACTED]"
    assert public_report(row, PolicyRegistry([p])).steps[0].checks[0].actual == expected
    assert public_report(row).steps[0].checks[0].actual == "[REDACTED]"
    p.assertions.source = "changed approval"
    assert public_report(row, PolicyRegistry([p])).steps[0].checks[0].actual == "[REDACTED]"


@pytest.mark.parametrize("expected,actual,selector,shown", [
    ([200, 201], 200, None, 200), ([200], 500, None, 500),
    ([200], 13800138000, None, "[REDACTED]"),
    ([13800138000], 200, None, "[REDACTED]"),
    ([200], 200, "$.phone", "[REDACTED]"),
])
def test_only_structural_http_status_evidence_is_public(expected, actual, selector, shown):
    row = with_check(CheckResult(kind="http_status", selector=selector, passed=False,
                                 expected=expected, actual=actual))
    assert public_report(row).steps[0].checks[0].actual == shown


def test_report_metadata_cannot_grant_numeric_approval():
    p = policy()
    row = with_check(CheckResult.model_validate({"kind": "equals", "selector": "$.count",
                      "passed": True, "expected": 7, "actual": 7, "public_numeric": True}),
                     fingerprint(p.model_dump()))
    assert public_report(row, PolicyRegistry([p])).steps[0].checks[0].actual == "[REDACTED]"


def test_public_count_api_requires_operator_policy_and_hides_nonfinite_actual(tmp_path):
    from agenttest.dsl import FieldAssertion

    p = policy()
    p.assertions.fields = [FieldAssertion(select="$.count", operator="count", expected=2)]
    p.assertions.public_evidence = ["$.count"]
    _, checks = verify(p.assertions, 200, {"count": ["a", "b"]})
    row = with_check(checks[-1], fingerprint(p.model_dump()))
    ReportStore(tmp_path).save(row)
    default = TestClient(create_app(tmp_path)).get(f"/api/runs/{row.run_id}").json()
    assert default["steps"][0]["checks"][0]["actual"] == "[REDACTED]"
    client = TestClient(create_app(tmp_path, public_policies=PolicyRegistry([p])))
    approved = client.get(f"/api/runs/{row.run_id}").json()
    assert approved["steps"][0]["checks"][0]["actual"] == 2
    row.steps[0].checks[0].actual = float("nan")
    assert public_report(row, PolicyRegistry([p])).steps[0].checks[0].actual == "[REDACTED]"
