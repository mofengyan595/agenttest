"""CourseInsight response shapes through the existing deterministic executor, offline."""

import json

import httpx
import pytest

from agenttest.courseinsight import execute, inputs
from agenttest.dsl import ArgumentBinding
from agenttest.executor import Executor
from agenttest.report import Outcome, ReportStore

ENV = {"COURSEINSIGHT_DEMO_USERNAME": "synthetic-admin",
       "COURSEINSIGHT_DEMO_PASSWORD": "synthetic-test-password"}
TOKEN = "synthetic-vault-only-jwt"


def bundle():
    scenario, target, contract, registry = inputs()
    target.allow_mutations = True  # MockTransport only: no local or outbound service.
    target.min_request_interval = 0.0
    return scenario, target, contract, registry


async def run(tmp_path, fault=None, mutate=None):
    scenario, target, contract, registry = bundle()
    if mutate:
        mutate(scenario, target)
    calls = []
    created = {}

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/auth/login":
            assert json.loads(request.content) == {
                "username": ENV["COURSEINSIGHT_DEMO_USERNAME"],
                "password": ENV["COURSEINSIGHT_DEMO_PASSWORD"]}
            return httpx.Response(200, json={"code": 0, "data": {
                "accessToken": TOKEN, "tokenType": "Bearer",
                "role": "STUDENT" if fault == "role" else "ADMIN"}})
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if request.method == "POST":
            created.update(json.loads(request.content))
            if fault == "timeout":
                raise httpx.ReadTimeout("private transport details", request=request)
            if fault == "redirect":
                return httpx.Response(307, headers={"Location": "http://127.0.0.1:8080"})
            return httpx.Response(409 if fault == "duplicate" else 201,
                                  json={"code": 409 if fault == "code" else 0,
                                        "data": "wrong-id" if fault == "id_type" else 987654})
        assert request.url.path == "/api/courses/987654"
        data = {"id": 987654, **created}
        if fault in {"id", "code_field", "name", "teacherName"}:
            data["code" if fault == "code_field" else fault] = (
                987655 if fault == "id" else "wrong synthetic value")
        return httpx.Response(200, json={"code": 0, "data": data})

    executor = Executor(target, contract, ReportStore(tmp_path), httpx.MockTransport(handler),
                        ENV, registry=registry)
    report = await executor.run(scenario)
    return report, calls, scenario


@pytest.mark.asyncio
async def test_java_envelope_scalar_id_and_all_consistency_checks(tmp_path):
    report, calls, scenario = await run(tmp_path)
    assert report.outcome == Outcome.PASS
    assert [request.method for request in calls] == ["POST", "POST", "GET"]
    assert [step.attempts[0].status_code for step in report.steps] == [200, 201, 200]
    assert {check.selector for check in report.steps[-1].checks} >= {
        "$.data.id", "$.data.code", "$.data.name", "$.data.teacherName", "$.code"}
    assert report.investigations == []
    raw = (tmp_path / report.run_id / "report.json").read_text()
    for secret in [TOKEN, *ENV.values(), "987654", scenario.steps[1].body["code"].value]:
        assert secret not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,count,outcome", [
    ("role", 1, Outcome.ASSERTION_FAILED), ("code", 2, Outcome.ASSERTION_FAILED),
    ("duplicate", 2, Outcome.ASSERTION_FAILED), ("timeout", 2, Outcome.TEST_INFRA_ERROR),
    ("redirect", 2, Outcome.TEST_INFRA_ERROR), ("id_type", 2, Outcome.TOOL_ERROR),
    *[(field, 3, Outcome.ASSERTION_FAILED) for field in
      ["id", "code_field", "name", "teacherName"]],
])
async def test_failures_never_retry_creation(tmp_path, fault, count, outcome):
    report, calls, _ = await run(tmp_path, fault)
    assert report.outcome == outcome and len(calls) == count
    assert sum(req.method == "POST" and req.url.path == "/api/courses" for req in calls) <= 1
    assert report.investigations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["static_id", "token", "policy", "input", "role", "write_retry"])
async def test_scope_and_dependency_tampering_sends_zero_requests(tmp_path, change):
    def mutate(plan, target):
        if change == "static_id":
            plan.steps[2].path_parameters["id"] = ArgumentBinding(value=1)
        elif change == "token":
            plan.steps[1].body["name"] = ArgumentBinding(from_step="login",
                                                       select="$.data.accessToken")
        elif change == "policy":
            plan.steps[2].policy_id = "read_own_course_v1"
        elif change == "input":
            plan.steps[1].body["code"] = ArgumentBinding(value="different")
        elif change == "role":
            plan.steps[2].role = "teacher"
        else:
            plan.steps[1].read_retries = 1
    report, calls, _ = await run(tmp_path, mutate=mutate)
    assert report.outcome != Outcome.PASS and calls == []


def test_one_off_ascii_fixture_and_default_deny():
    first, target, _, _ = inputs()
    second, _, _, _ = inputs()
    code = first.steps[1].body["code"].value
    assert code != second.steps[1].body["code"].value
    assert code.isascii() and code.isalnum() and len(code) == 28
    assert target.allow_mutations is False
    assert target.max_requests == 3 and target.max_steps == 3
    assert target.base_url == "http://127.0.0.1:18080"
    assert target.roles["demo_admin"].token_selector == "$.data.accessToken"


@pytest.mark.asyncio
async def test_auditable_fixture_manifest_has_no_credentials(tmp_path):
    scenario, target, contract, registry = bundle()
    def handler(request):
        return httpx.Response(401, json={"code": 401, "data": None})
    result = await execute(scenario, target, contract, registry, ENV, ReportStore(tmp_path),
                           transport=httpx.MockTransport(handler))
    manifest = (tmp_path / result.run_id / "fixture.json").read_text()
    assert scenario.steps[1].body["code"].value in manifest
    assert all(value not in manifest for value in [TOKEN, *ENV.values()])
    assert result.plan_valid and len(result.plan_structure) == 3
    assert result.plan_structure[2].dependencies == {"path_parameters.id": "create:$.data"}
