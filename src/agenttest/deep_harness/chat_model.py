"""LangChain ``BaseChatModel`` adapter for the operator-configured OpenAI-compatible model.

The product already owns one bounded model client (``llm_planner.OpenAICompatiblePlanner``)
that speaks *strict JSON-schema output*. A tool-calling harness needs the other half of the
same gateway: a ``tools`` array in, ``tool_calls`` out, ``role: tool`` results back in. This
module is that half, kept deliberately small and held to the same rules as the existing
client:

* exactly one HTTP request per call -- no retries, no redirects, ``trust_env=False``;
* a bounded response body and a measured token envelope;
* the API key never enters a request body, a log line or an exception message;
* every failure becomes one closed error code, never a fabricated success.

Two behaviours are not obvious and are load-bearing; both were measured against the real
gateway (``experiments/deepagents_p1/evidence/``):

1. An assistant message that carries ``"tool_calls": []`` makes the gateway answer
   HTTP 500, while the identical conversation with the key omitted answers 200. LangChain's
   ``AIMessage.tool_calls`` defaults to an empty list, so :func:`assistant_to_wire` **omits**
   the key unless there is at least one call.
2. ``finish_reason`` is ``tool_calls`` (not ``stop``) when the model calls a tool, and the
   product's JSON-schema client rejects that value. Tool calling must therefore be a
   separate adapter instead of a flag on the planning client.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Sequence

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from agenttest.llm_planner import ModelSettings, provider_request_id, safe_provider_id, timeout_kind
from agenttest.model_retry import retry_after_seconds
from agenttest.model_timing import ModelCallTelemetry
from agenttest.report import ModelCallStats, fingerprint

# Closed error taxonomy; a caller can branch on these without parsing prose.
MODEL_TIMEOUT = "MODEL_TIMEOUT"
MODEL_CONNECTION_ERROR = "MODEL_CONNECTION_ERROR"
MODEL_SERVICE_ERROR = "MODEL_SERVICE_ERROR"
MODEL_API_ERROR = "MODEL_API_ERROR"
MODEL_RESPONSE_LIMIT = "MODEL_RESPONSE_LIMIT"
MODEL_TOKEN_BUDGET = "MODEL_TOKEN_BUDGET"
MODEL_OUTPUT_TRUNCATED = "MODEL_OUTPUT_TRUNCATED"
MODEL_REFUSAL = "MODEL_REFUSAL"
MODEL_INVALID_RESPONSE = "MODEL_INVALID_RESPONSE"
MODEL_SECRET_OUTPUT = "MODEL_SECRET_OUTPUT"
MODEL_INPUT_REJECTED = "MODEL_INPUT_REJECTED"
MODEL_TOOL_CATALOG_INVALID = "MODEL_TOOL_CATALOG_INVALID"
MODEL_CALL_BUDGET = "MODEL_CALL_BUDGET"
MODEL_USAGE_UNKNOWN = "MODEL_USAGE_UNKNOWN"


class ModelBudgetLimits(BaseModel):
    """One run's hard envelope for the harness model; exceeded before sending, never after."""

    max_calls: int | None = Field(default=None, ge=0)
    max_total_tokens: int | None = Field(default=None, ge=0)


class ModelTotals(BaseModel):
    """Per-run accumulation that P1 measured in an ad-hoc subclass; now part of the adapter."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    unknown_usage_calls: int = 0
    failed_calls: int = 0
    request_bytes: int = 0
    response_bytes: int = 0
    elapsed_ms: float = 0.0

    def public(self) -> dict:
        return self.model_dump(mode="json")


class ModelHarnessError(RuntimeError):
    """One measured, closed failure from the tool-calling gateway."""

    def __init__(self, code: str, detail: str = "", *, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(code)
        self.code, self.detail, self.status = code, detail[:400], status
        self.retry_after = retry_after

    def __str__(self) -> str:  # pragma: no cover - message shape only
        return f"{self.code}: {self.detail}" if self.detail else self.code


def text_of(message: BaseMessage) -> str:
    """Flatten message content to text; content blocks are joined, never executed."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else ():
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def assistant_to_wire(message: AIMessage) -> dict:
    """Serialize an assistant turn, omitting ``tool_calls`` when there are none.

    Omitting the key is not cosmetic: an empty array is rejected by the gateway with
    HTTP 500 (measured, see the module docstring).
    """
    payload: dict[str, Any] = {"role": "assistant", "content": text_of(message) or None}
    calls = []
    for call in message.tool_calls or ():
        name = call.get("name")
        if not name:
            continue
        calls.append({"id": call.get("id") or f"call_{len(calls)}",
                      "type": "function",
                      "function": {"name": name,
                                   "arguments": json.dumps(call.get("args") or {},
                                                           ensure_ascii=False)}})
    if calls:
        payload["tool_calls"] = calls
    return payload


def message_to_wire(message: BaseMessage) -> dict:
    """Map one LangChain message onto the wire shape the gateway documents."""
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": text_of(message)}
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": text_of(message)}
    if isinstance(message, ToolMessage):
        return {"role": "tool", "tool_call_id": message.tool_call_id,
                "content": text_of(message)}
    if isinstance(message, AIMessage):
        return assistant_to_wire(message)
    return {"role": "user", "content": text_of(message)}


def messages_to_wire(messages: Sequence[BaseMessage]) -> list[dict]:
    return [message_to_wire(message) for message in messages]


def normalize_tools(tools: Sequence[Any]) -> list[dict]:
    """Convert and validate the tool catalog before it reaches the gateway.

    A duplicated function name is fatal on the real gateway (HTTP 500 with an empty body),
    so uniqueness is enforced here instead of being discovered in a run.
    """
    catalog = [convert_to_openai_tool(tool) for tool in tools]
    names = [item.get("function", {}).get("name") for item in catalog]
    if any(not name for name in names):
        raise ModelHarnessError(MODEL_TOOL_CATALOG_INVALID, "unnamed tool")
    if len(set(names)) != len(names):
        raise ModelHarnessError(MODEL_TOOL_CATALOG_INVALID, "duplicate tool name")
    return catalog


def parse_tool_calls(message: dict, index: int) -> list[dict]:
    """Translate wire tool calls into LangChain ``tool_calls``; never repair arguments."""
    calls = []
    for position, item in enumerate(message.get("tool_calls") or ()):
        function = item.get("function") or {}
        name = function.get("name")
        raw = function.get("arguments")
        if not isinstance(name, str) or not name:
            raise ModelHarnessError(MODEL_INVALID_RESPONSE, "tool call without a name")
        try:
            arguments = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except ValueError:
            raise ModelHarnessError(MODEL_INVALID_RESPONSE, "tool arguments are not JSON") \
                from None
        if not isinstance(arguments, dict):
            raise ModelHarnessError(MODEL_INVALID_RESPONSE, "tool arguments are not an object")
        calls.append({"name": name, "args": arguments,
                      "id": item.get("id") or f"call_{index}_{position}",
                      "type": "tool_call"})
    return calls


def normalize_tool_choice(tool_choice: Any, tools: list[dict] | None) -> Any:
    """Accept LangChain's string form and emit the object form the gateway accepts.

    ``BaseChatModel.bind_tools(tool_choice="Name")`` passes a bare name; the gateway answers
    HTTP 500 to that (measured while wiring structured output) and accepts
    ``{"type": "function", "function": {"name": "Name"}}``. A name that is not in the bound
    catalog is rejected here instead of being sent as an unenforceable preference.
    """
    if tool_choice is None:
        return None
    names = {item.get("function", {}).get("name") for item in tools or ()}
    if isinstance(tool_choice, str):
        if tool_choice in {"auto", "none"}:
            return tool_choice
        if tool_choice in {"any", "required"}:
            # LangChain's structured-output strategy sends "any" (an Anthropic spelling).
            # With a single bound tool the explicit function form is equivalent and is the
            # only spelling this gateway is known to accept, so prefer it.
            if len(names) == 1:
                return {"type": "function", "function": {"name": next(iter(names))}}
            return "required"
        if tool_choice not in names:
            raise ModelHarnessError(MODEL_TOOL_CATALOG_INVALID, "tool_choice not bound")
        return {"type": "function", "function": {"name": tool_choice}}
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name")
        if tool_choice.get("type") == "function" and name not in names:
            raise ModelHarnessError(MODEL_TOOL_CATALOG_INVALID, "tool_choice not bound")
        return tool_choice
    raise ModelHarnessError(MODEL_TOOL_CATALOG_INVALID, "unsupported tool_choice")


class EcnuxChatModel(BaseChatModel):
    """Bounded tool-calling chat model over the operator's configured gateway."""

    settings: ModelSettings
    model_stats: ModelCallStats = Field(default_factory=ModelCallStats)
    totals: ModelTotals = Field(default_factory=ModelTotals)
    limits: ModelBudgetLimits | None = None
    call_trace: list = Field(default_factory=list)
    on_call: Any = Field(default=None, exclude=True)
    on_payload: Any = Field(default=None, exclude=True)
    before_call: Any = Field(default=None, exclude=True)
    transport: Any = None
    max_response_bytes: int = 200_000

    @property
    def _llm_type(self) -> str:
        return "ecnu-openai-compatible"

    @property
    def model_name(self) -> str:
        """The configured model identifier, in the field name frameworks look for.

        Deep Agents builds its profile key from ``model_name``/``model`` plus the provider,
        so the adapter must expose the configured model here or the key silently degrades to
        the bare provider.
        """
        return self.settings.model

    def _get_ls_params(self, **kwargs) -> dict:
        """A stable provider key for harness-profile resolution.

        Deep Agents resolves a harness profile by ``ls_provider[:ls_model_name]``. The base
        class derives that provider from the Python class name, so a wrapper subclass (an
        accounting or probe model) would silently target a *different* profile -- and a
        security narrowing keyed on a class name is worth nothing. This adapter's identity is
        the configured model, so it is reported explicitly and inherited by every subclass.
        """
        return {"ls_provider": "agenttest", "ls_model_name": self.settings.model,
                "ls_model_type": "chat"}

    @property
    def stats(self) -> ModelCallStats:
        return self.model_stats

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: str | None = None,
                   **kwargs: Any):
        """Bind a validated, unique tool catalog; selection stays with the model."""
        bound: dict[str, Any] = {"tools": normalize_tools(tools)}
        if tool_choice is not None:
            bound["tool_choice"] = tool_choice
        return self.bind(**bound, **kwargs)

    # ------------------------------------------------------------------ request
    def _payload(self, messages: Sequence[BaseMessage], tools: list[dict] | None,
                 tool_choice: Any) -> dict:
        settings = self.settings
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": messages_to_wire(messages),
            "max_tokens": settings.max_output_tokens,
            "temperature": 0,
        }
        if settings.protocol == "chatecnu":
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = tools
        choice = normalize_tool_choice(tool_choice, tools)
        if choice is not None and tools:
            payload["tool_choice"] = choice
        if self.on_payload is not None:
            self.on_payload(payload)
        return payload

    def _encode(self, payload: dict) -> bytes:
        """Budget-check the exact request and refuse to send a body containing the key."""
        settings = self.settings
        if self.limits is not None:
            if (self.limits.max_calls is not None
                    and self.totals.calls >= self.limits.max_calls):
                raise ModelHarnessError(MODEL_CALL_BUDGET, "run call limit reached")
            if (self.limits.max_total_tokens is not None
                    and self.totals.total_tokens >= self.limits.max_total_tokens):
                raise ModelHarnessError(MODEL_TOKEN_BUDGET, "run token limit reached")
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        if settings.api_key.get_secret_value() in encoded.decode("utf-8", "ignore"):
            raise ModelHarnessError(MODEL_INPUT_REJECTED, "credential in request body")
        # UTF-8 bytes plus envelope margin, the same conservative bound the planner uses.
        upper_bound = len(encoded) + 512
        self.model_stats.input_token_upper_bound = upper_bound
        self.model_stats.request_bytes = len(encoded)
        self.model_stats.request_sha256 = fingerprint(payload)
        if (upper_bound > settings.max_input_tokens
                or upper_bound + settings.max_output_tokens > settings.max_total_tokens):
            raise ModelHarnessError(MODEL_TOKEN_BUDGET)
        return encoded

    async def _acall(self, payload: dict, stats: ModelCallStats, *,
                     telemetry_started: float) -> tuple[dict, dict, float]:
        settings = self.settings
        credential = settings.api_key.get_secret_value()
        encoded = self._encode(payload)
        started = time.monotonic()
        telemetry = stats.telemetry
        telemetry.mark("request_prepared", started=telemetry_started)
        stats.model_name = settings.audit_model_name
        stats.model_sha256 = fingerprint(settings.model)
        stats.started_at = time.time()
        if self.before_call is not None:
            self.before_call(stats)
        # Count the attempt before dispatch: a timeout may already have reached the gateway,
        # so a request that produced no measured answer still consumed one call.
        stats.calls = 1
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         follow_redirects=False,
                                         timeout=settings.timeout) as client:
                telemetry.mark("dispatch_started", started=telemetry_started)
                async with client.stream(
                        "POST", settings.base_url + "/chat/completions", content=encoded,
                        headers={"Content-Type": "application/json",
                                 "Authorization": "Bearer " + credential}) as response:
                    telemetry.mark("headers_received", started=telemetry_started)
                    stats.http_status = response.status_code
                    stats.provider_request_id = provider_request_id(response, credential)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        if chunk:
                            telemetry.mark("first_body_chunk", started=telemetry_started)
                        raw.extend(chunk)
                        stats.response_bytes = len(raw)
                        if len(raw) > self.max_response_bytes:
                            raise ModelHarnessError(MODEL_RESPONSE_LIMIT)
                    telemetry.mark("body_complete", started=telemetry_started)
                    if response.status_code != 200:
                        infra = response.status_code == 429 or response.status_code >= 500
                        stats.refused = True
                        raise ModelHarnessError(
                            MODEL_SERVICE_ERROR if infra else MODEL_API_ERROR,
                            "MODEL_GATEWAY_CONCURRENCY_LIMIT" if response.status_code == 429
                            and b"model_concurrency_exceeded" in raw else
                            "MODEL_GATEWAY_RATE_LIMIT" if response.status_code == 429 else "",
                            status=response.status_code,
                            retry_after=retry_after_seconds(response.headers.get("Retry-After")))
        except (httpx.TimeoutException, TimeoutError) as exc:
            stats.timeout_kind = timeout_kind(exc)
            telemetry.transport_timeout(stats.timeout_kind)
            raise ModelHarnessError(MODEL_TIMEOUT) from None
        except httpx.TransportError:
            telemetry.interruption_source = "UNKNOWN"
            raise ModelHarnessError(MODEL_CONNECTION_ERROR) from None
        finally:
            stats.elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        try:
            data = json.loads(bytes(raw).decode("utf-8"))
            telemetry.mark("json_parsed", started=telemetry_started)
            if isinstance(data, dict):
                stats.provider_response_id = safe_provider_id(data.get("id"), credential)
            choice = (data.get("choices") or [])[0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            if "json_parsed" not in telemetry.stages_ms:
                telemetry.usage_evidence = "RESPONSE_PARSE_FAILED"
            raise ModelHarnessError(MODEL_INVALID_RESPONSE, "envelope") from None
        return data, message, stats.elapsed_ms

    def _usage(self, data: dict, stats: ModelCallStats, *, started: float) -> dict | None:
        """Validate the provider's usage block; a missing or inconsistent block is UNKNOWN."""
        if stats.telemetry is not None:
            stats.telemetry.observe_usage(data, started=started)
        usage = data.get("usage") or {}
        counts = [usage.get(key) for key in ("prompt_tokens", "completion_tokens",
                                             "total_tokens")]
        if any(type(value) is not int or value < 0 for value in counts):
            stats.reason = "MODEL_USAGE_UNKNOWN"
            return None
        if counts[2] != counts[0] + counts[1]:
            stats.reason = "MODEL_USAGE_UNKNOWN"
            return None
        settings = self.settings
        if (counts[0] > settings.max_input_tokens or counts[1] > settings.max_output_tokens
                or counts[2] > settings.max_total_tokens):
            raise ModelHarnessError(MODEL_TOKEN_BUDGET)
        stats.input_tokens = counts[0]
        stats.output_tokens = counts[1]
        stats.total_tokens = counts[2]
        return {"input_tokens": counts[0], "output_tokens": counts[1],
                "total_tokens": counts[2]}

    def _message(self, data: dict, message: dict, index: int,
                 stats: ModelCallStats, *, started: float) -> AIMessage:
        usage = self._usage(data, stats, started=started)
        choice = data["choices"][0]
        finish = choice.get("finish_reason")
        if finish == "length":
            raise ModelHarnessError(MODEL_OUTPUT_TRUNCATED)
        if message.get("refusal") or finish == "content_filter":
            raise ModelHarnessError(MODEL_REFUSAL)
        content = message.get("content") or ""
        if self.settings.api_key.get_secret_value() in content:
            raise ModelHarnessError(MODEL_SECRET_OUTPUT)
        calls = parse_tool_calls(message, index)
        stats.output_sha256 = fingerprint(content)
        stats.reason = "MODEL_OUTPUT_RECEIVED"
        return AIMessage(content=content, tool_calls=calls,
                         response_metadata={"finish_reason": finish,
                                            "model": data.get("model")},
                         usage_metadata=usage)

    # ----------------------------------------------------------------- generate
    async def _agenerate(self, messages: Sequence[BaseMessage], stop: list[str] | None = None,
                         run_manager: Any = None, **kwargs: Any) -> ChatResult:
        tools = kwargs.get("tools")
        payload = self._payload(messages, tools if isinstance(tools, list) else None,
                               kwargs.get("tool_choice"))
        stats = ModelCallStats(telemetry=ModelCallTelemetry())
        self.model_stats = stats
        started = time.monotonic()
        tool_names: list[str] = []
        error = None
        try:
            data, message, _elapsed = await self._acall(payload, stats, telemetry_started=started)
            result = self._message(data, message, 0, stats, started=started)
            tool_names = [call.get("name") for call in result.tool_calls or ()]
            return ChatResult(generations=[ChatGeneration(message=result)])
        except ModelHarnessError as exc:
            error = exc.code
            raise
        except asyncio.CancelledError as exc:
            stats.telemetry.cancelled(exc)
            raise
        finally:
            self._accumulate(payload, tool_names, error, stats, started=started)

    def _accumulate(self, payload: dict, tool_names: list[str],
                    error: str | None, stats: ModelCallStats, *, started: float) -> None:
        """One authoritative ledger row per attempted call, beside the per-call stat."""
        stats.ended_at = time.time()
        stats.error_type = error
        stats.usage_state = stats.observed_usage_state()
        stats.finish_telemetry(started=started)
        self.model_stats = stats
        self.totals.calls += stats.calls
        self.totals.input_tokens += stats.input_tokens or 0
        self.totals.output_tokens += stats.output_tokens or 0
        self.totals.total_tokens += stats.total_tokens or 0
        self.totals.request_bytes += stats.request_bytes or 0
        self.totals.response_bytes += stats.response_bytes or 0
        self.totals.elapsed_ms += stats.elapsed_ms or 0.0
        if stats.calls and stats.total_tokens is None and not stats.refused:
            self.totals.unknown_usage_calls += 1
        if error:
            self.totals.failed_calls += 1
        self.call_trace.append(dict(
            stats.model_dump(mode="json"), index=len(self.call_trace) + 1,
            tool_names=[name for name in tool_names if name],
            tool_count=len(payload.get("tools") or ()), error=error))
        if self.on_call is not None:
            self.on_call(stats)

    def _generate(self, messages: Sequence[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None,
                  **kwargs: Any) -> ChatResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._agenerate(messages, stop, run_manager, **kwargs))
        raise ModelHarnessError(MODEL_INPUT_REJECTED, "use the async API inside a loop")


def build_chat_model(settings: ModelSettings, *, transport: Any = None) -> EcnuxChatModel:
    """Factory used by the harness: one model object, one measured stats field."""
    return EcnuxChatModel(settings=settings, transport=transport)
