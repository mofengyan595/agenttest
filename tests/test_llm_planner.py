import json

import httpx
import pytest
from test_planner_input import inputs

from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerFailure
from agenttest.model_probe import check_connection
from agenttest.planner_input import planning_context
from agenttest.report import Outcome


def draft():
    return {"name": "model-generated", "steps": [{"id": "auth", "operation_id": "login",
            "role": "alice", "policy_id": "nl_login_v1", "arguments": []}]}


def completion(content=None, finish="stop", **overrides):
    return {"choices": [{"finish_reason": finish, "message": {
        "content": json.dumps(draft()) if content is None else content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        **overrides}


def planner(handler, **settings):
    return OpenAICompatiblePlanner(ModelSettings(model="operator-model",
        base_url="https://approved-model.invalid/v1", api_key="synthetic-model-credential",
        **settings), httpx.MockTransport(handler))


def context():
    contract, config, registry = inputs()
    return planning_context(contract, config, registry, "创建并查询课程", {})


@pytest.mark.asyncio
async def test_request_is_strict_structured_and_has_no_target_credentials():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=completion())

    llm = planner(handle)
    result = json.loads(await llm.generate(context()))
    assert result["steps"][0]["policy_id"] == "nl_login_v1"
    payload = json.loads(requests[0].content)
    format = payload["response_format"]["json_schema"]
    assert format["strict"] and format["schema"]["additionalProperties"] is False
    for definition in format["schema"]["$defs"].values():
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])
    assert "nl_forbidden_v1" in format["schema"]["$defs"]["DraftStep"]["properties"][
        "policy_id"]["enum"]
    assert not payload["store"] and "max_completion_tokens" in payload
    raw = requests[0].content.decode()
    for private in ("DEMO_ALICE", "base_url", "synthetic-model-credential", "response_body"):
        assert private not in raw
    assert llm.stats.calls == 1 and llm.stats.total_tokens == 150
    assert "synthetic-model-credential" not in repr(llm._settings)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,outcome,reason", [
    (401, Outcome.TOOL_ERROR, "MODEL_API_ERROR"),
    (400, Outcome.TOOL_ERROR, "MODEL_API_ERROR"),
    (302, Outcome.TOOL_ERROR, "MODEL_API_ERROR"),
    (429, Outcome.TEST_INFRA_ERROR, "MODEL_SERVICE_ERROR"),
    (503, Outcome.TEST_INFRA_ERROR, "MODEL_SERVICE_ERROR"),
])
async def test_api_errors_do_not_retry_or_leak(status, outcome, reason):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "private-provider-body"},
                              headers={"location": "https://unapproved.invalid"})

    llm = planner(handler)
    with pytest.raises(PlannerFailure) as caught:
        await llm.generate(context())
    assert (caught.value.outcome, caught.value.reason) == (outcome, reason)
    assert len(requests) == 1 and "private-provider-body" not in llm.stats.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("response,reason", [
    (completion("{bad"), "MODEL_INVALID_RESPONSE"),
    (completion('{"name":"x","name":"y","steps":[]}'), "MODEL_INVALID_RESPONSE"),
    (completion(json.dumps({**draft(), "reviewed": True})), "MODEL_INVALID_RESPONSE"),
    (completion("{\"name\":1e309}"), "MODEL_INVALID_RESPONSE"),
    (completion(finish="length"), "MODEL_OUTPUT_TRUNCATED"),
    (completion(choices=[{"finish_reason": "stop", "message": {"refusal": "refused"}}]),
     "MODEL_REFUSAL"),
    (completion(usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 1}),
     "MODEL_INVALID_RESPONSE"),
    (completion(usage={"prompt_tokens": 100, "completion_tokens": 5000, "total_tokens": 5100}),
     "MODEL_TOKEN_BUDGET"),
    ({"choices": []}, "MODEL_INVALID_RESPONSE"),
])
async def test_malformed_refused_truncated_and_over_budget(response, reason):
    llm = planner(lambda request: httpx.Response(200, json=response))
    with pytest.raises(PlannerFailure) as caught:
        await llm.generate(context())
    assert caught.value.reason == reason and llm.stats.reason == reason


@pytest.mark.asyncio
async def test_input_budget_stops_before_connection():
    requests = []
    llm = planner(lambda request: requests.append(request), max_input_tokens=1)
    with pytest.raises(PlannerFailure, match="MODEL_TOKEN_BUDGET"):
        await llm.generate(context())
    assert not requests and llm.stats.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("exception,reason", [(httpx.ReadTimeout, "MODEL_TIMEOUT"),
                                            (httpx.ConnectError, "MODEL_CONNECTION_ERROR")])
async def test_transport_failures_are_infrastructure(exception, reason):
    def handler(request):
        raise exception("private-error-message")

    llm = planner(handler)
    with pytest.raises(PlannerFailure) as caught:
        await llm.generate(context())
    assert caught.value.outcome == Outcome.TEST_INFRA_ERROR
    assert caught.value.reason == reason and "private-error-message" not in str(caught.value)


@pytest.mark.asyncio
async def test_response_byte_limit():
    llm = planner(lambda request: httpx.Response(200, content=b"x" * 101), max_response_bytes=100)
    with pytest.raises(PlannerFailure, match="MODEL_RESPONSE_LIMIT"):
        await llm.generate(context())


@pytest.mark.asyncio
async def test_structured_abstention_needs_review_never_becomes_empty_executable_plan():
    llm = planner(lambda request: httpx.Response(200, json=completion(
        json.dumps({"name": "unsupported task", "steps": []}))))
    with pytest.raises(PlannerFailure) as caught:
        await llm.generate(context())
    assert caught.value.outcome == Outcome.NEEDS_REVIEW
    assert caught.value.reason == "MODEL_NO_APPROVED_PLAN" and llm.stats.calls == 1


@pytest.mark.parametrize("base", ["http://model.invalid/v1", "https://user:pass@model.invalid",
                                "https://model.invalid?key=private", "https://model.invalid/../v1"])
def test_model_endpoint_only_operator_https(base):
    with pytest.raises(ValueError):
        ModelSettings(model="model", base_url=base, api_key="synthetic")


def test_model_settings_have_no_secret_defaults():
    with pytest.raises(ValueError):
        ModelSettings.from_environment({})


@pytest.mark.asyncio
async def test_school_wire_format_and_local_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text('OPENAI_API_KEY="synthetic-key"\n'
                    'OPENAI_BASE_URL=https://chat.ecnu.edu.cn/open/api/v1\n'
                    'AGENTTEST_MODEL=ecnu-plus\nDEMO_PASSWORD=not-read\n', encoding="utf-8")
    settings = ModelSettings.from_env_file(path, {})
    assert settings.protocol == "chatecnu" and settings.model == "ecnu-plus"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completion())

    await OpenAICompatiblePlanner(settings, httpx.MockTransport(handler)).generate(context())
    payload = json.loads(requests[0].content)
    assert "max_tokens" in payload and "max_completion_tokens" not in payload
    assert "store" not in payload and "strict" not in payload["response_format"]["json_schema"]
    assert payload["thinking"] == {"type": "disabled"}
    assert "not-read" not in requests[0].content.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,models,reason", [(200, ["operator-model"], "READY"),
                                               (200, ["other-model"], "MODEL_NOT_FOUND"),
                                               (401, [], "MODEL_API_ERROR")])
async def test_metadata_probe_has_zero_generation_calls(status, models, reason):
    llm = planner(lambda req: httpx.Response(200))
    result = await check_connection(llm._settings, httpx.MockTransport(
        lambda req: httpx.Response(status, json={"data": [{"id": m} for m in models]})))
    assert result["reason"] == reason and result["generation_calls"] == 0
