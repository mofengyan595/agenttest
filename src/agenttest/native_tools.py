"""Opt-in Chat Completions function envelope, sharing JSON DSL validation and transport."""

import json

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.llm_planner import OpenAICompatiblePlanner, PlannerFailure
from agenttest.report import Outcome, fingerprint
from agenttest.tool_registry import native_plan_tool


class NativeFunctionPlanner(OpenAICompatiblePlanner):
    def __init__(self, settings, transport=None, *, provider_supported=False):
        super().__init__(settings, transport, profile="generic", planning_constraints=True)
        self._provider_supported = provider_supported

    def prepare_request(self, context):
        if not self._provider_supported or self._settings.protocol != "openai_compatible":
            raise PlannerFailure(Outcome.NEEDS_REVIEW, "NATIVE_FUNCTION_PROTOCOL_UNSUPPORTED")
        payload = json.loads(super().prepare_request(context))
        schema = payload.pop("response_format")["json_schema"]["schema"]
        tool = native_plan_tool(schema)
        payload.update(tools=[tool], parallel_tool_calls=False,
            tool_choice=dict(type="function", function=dict(name=tool["function"]["name"])))
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.stats.input_token_upper_bound = len(encoded) + 512
        if (self.stats.input_token_upper_bound > self._settings.max_input_tokens
                or self.stats.input_token_upper_bound + self._settings.max_output_tokens
                > self._settings.max_total_tokens):
            raise PlannerFailure(Outcome.TEST_INFRA_ERROR, "MODEL_TOKEN_BUDGET")
        self.stats.request_bytes = len(encoded)
        self.stats.request_sha256 = fingerprint(payload)
        return encoded

    def _message_content(self, choice):
        calls = choice["message"].get("tool_calls", [])
        if (choice["finish_reason"] != "tool_calls" or len(calls) != 1
                or calls[0].get("type") != "function"
                or calls[0].get("function", {}).get("name") != "submit_test_plan"
                or choice["message"].get("content") not in {None, ""}
                or not isinstance(calls[0]["function"].get("arguments"), str)):
            raise ValidationFailure(Layer.MODEL, "MODEL_TOOL_CALL_INVALID")
        return calls[0]["function"]["arguments"]
