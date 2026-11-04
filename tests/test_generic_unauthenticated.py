"""Anonymous denial tests require an exact operator policy, never a global auth bypass."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.report import Outcome, ReportStore


def project(*, documented=True, status=401):
    responses = {"200": dict(description="ok")}
    if documented:
        responses["401"] = dict(description="unauthenticated")
    doc = json.dumps(dict(openapi="3.0.3", info=dict(title="Private reads", version="1"),
        paths={"/private": {"get": dict(operationId="readPrivate", security=[{"bearer": []}],
            responses=responses)}}, components=dict(securitySchemes=dict(
                bearer=dict(type="http", scheme="bearer")))))
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18999",
        isolated_confirmed=True, assisted=True, roles=[dict(alias="owner",
            bearer_env="AGENTTEST_TARGET_OWNER_TOKEN")], operations=[dict(
            operation_id="readPrivate", alias="读取", role="owner", statuses=[200],
            access_checks=[dict(role=None, statuses=[status], rule="未登录读取应拒绝，返回401")])]))
    return build_generic_project("web_denial", doc, load_openapi(doc), setup,
        environ={"AGENTTEST_TARGET_OWNER_TOKEN": "synthetic-test-token"})


@pytest.mark.asyncio
async def test_only_exact_approved_anonymous_policy_can_omit_authentication(tmp_path):
    p = project()
    base, denial = p.registry.definitions()
    plan = Scenario.model_validate(dict(name="检查未登录拒绝", steps=[
        dict(id="owner", operation_id="readPrivate", policy_id=base.id, role="owner"),
        dict(id="anonymous", operation_id="readPrivate", policy_id=denial.id)]))
    sent = []

    def target(request):
        sent.append(request)
        if len(sent) == 1:
            assert request.headers["Authorization"] == "Bearer synthetic-test-token"
            return httpx.Response(200, headers={"set-cookie": "session=discard-me; Path=/"})
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        return httpx.Response(401)

    executor = Executor(p.target, p.contract, ReportStore(tmp_path), httpx.MockTransport(target),
        {"AGENTTEST_TARGET_OWNER_TOKEN": "synthetic-test-token"}, p.registry)
    assert (await executor.run(plan)).outcome == Outcome.PASS
    invalid = plan.model_copy(deep=True)
    invalid.steps = [invalid.steps[1]]
    invalid.steps[0].policy_id = base.id
    with pytest.raises(ValueError, match="role alias"):
        executor.validate(invalid)
    assert len(sent) == 2


@pytest.mark.parametrize("kwargs", [dict(documented=False), dict(status=200)])
def test_anonymous_approval_requires_a_documented_401(kwargs):
    with pytest.raises(ValueError, match="DOCUMENTED_401"):
        project(**kwargs)
