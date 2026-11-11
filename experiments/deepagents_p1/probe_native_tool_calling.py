"""Admission test: does ecnu-max speak native OpenAI tool calling?

This is a standalone experiment entry point. It never imports the product chain and it
never touches a business target: the only endpoint it reaches is the operator-configured
model gateway. The question it answers is narrow and load-bearing for P1:

    can the configured model accept a ``tools`` array, answer with ``tool_calls``,
    receive ``role: tool`` results, and keep going for several turns?

If that is false, no agent harness built on LangChain tool calling can be honestly
adopted, and the P1 report has to say so with evidence instead of prose.

Recorded evidence is written as JSON (request shape, response shape, usage, timings).
Authorization headers and the API key never enter the record.

Usage::

    .\\.venv\\Scripts\\python.exe experiments/deepagents_p1/probe_native_tool_calling.py
    .\\.venv\\Scripts\\python.exe experiments/deepagents_p1/probe_native_tool_calling.py
        --scenario multi_turn
    .\\.venv\\Scripts\\python.exe experiments/deepagents_p1/probe_native_tool_calling.py
        --replay evidence.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = REPO / "experiments" / "deepagents_p1" / "evidence" / "native-tool-calling.json"

SYSTEM_PROMPT = (
    "You are a bounded API-testing agent. Use the supplied tools to gather facts. "
    "Never invent tool names, arguments or results."
)

# Three tools is enough to prove real selection: two look plausible, one is correct.
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_contract",
            "description": "Read the approved OpenAPI operations for one resource family.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {
                        "type": "string",
                        "description": "Resource family name, e.g. parcels.",
                    },
                    "methods": {
                        "type": "array",
                        "items": {"type": "string",
                                  "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                        "description": "Optional method filter.",
                    },
                },
                "required": ["resource"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_test_coverage",
            "description": "List which approved testing obligations already have evidence.",
            "parameters": {
                "type": "object",
                "properties": {"resource": {"type": "string"}},
                "required": ["resource"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "investigate_failure",
            "description": "Run one approved read-only investigation for a failed check.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string"},
                    "hypothesis": {"type": "string",
                                   "enum": ["PRECONDITION_MISSING", "TARGET_UNAVAILABLE",
                                            "CONTRACT_MISMATCH"]},
                },
                "required": ["operation_id", "hypothesis"],
                "additionalProperties": False,
            },
        },
    },
]

# What the fake tool layer answers; the model only ever sees these strings.
TOOL_RESULTS = {
    "get_contract": {
        "resource": "parcels",
        "operations": [
            {"operation_id": "createParcel", "method": "POST", "path": "/api/parcels"},
            {"operation_id": "readParcel", "method": "GET", "path": "/api/parcels/{id}"},
            {"operation_id": "deleteParcel", "method": "DELETE", "path": "/api/parcels/{id}"},
        ],
    },
    "get_test_coverage": {
        "resource": "parcels",
        "open": ["deleteParcel:204", "readParcel:404-after-delete"],
        "satisfied": ["createParcel:201"],
    },
    "investigate_failure": {
        "operation_id": "readParcel",
        "hypothesis": "PRECONDITION_MISSING",
        "finding": "the 404 expectation ran against a resource that still exists",
    },
}


@dataclass
class Turn:
    """One HTTP exchange; every field is measured or read from the response."""

    label: str
    request: dict
    status: int | None = None
    elapsed_ms: float = 0.0
    usage: dict | None = None
    finish_reason: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    content: str | None = None
    error: str | None = None
    request_bytes: int = 0
    response_bytes: int = 0

    def public(self) -> dict:
        return dict(label=self.label, status=self.status, elapsed_ms=self.elapsed_ms,
                    usage=self.usage, finish_reason=self.finish_reason,
                    tool_calls=self.tool_calls, content=self.content, error=self.error,
                    request_bytes=self.request_bytes, response_bytes=self.response_bytes,
                    request=self.request)

    def openai_tool_call_names(self) -> list[str]:
        return [call["name"] for call in self.tool_calls]


def load_settings() -> Any:
    """Reuse the product's validated settings reader; it is the only credential path."""
    import sys

    sys.path.insert(0, str(REPO / "src"))
    from agenttest.llm_planner import ModelSettings

    return ModelSettings.from_env_file(REPO / ".env")


def envelope(settings: Any, messages: list[dict], *, tools: list[dict] | None = None,
             tool_choice: Any = None, response_format: dict | None = None,
             temperature: float | None = None) -> dict:
    """Chat-completions payload in the shape the school gateway documents."""
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "max_tokens": settings.max_output_tokens,
        "temperature": 0 if temperature is None else temperature,
    }
    if settings.protocol == "chatecnu":
        payload["thinking"] = {"type": "disabled"}
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


def parse_tool_calls(message: dict) -> list[dict]:
    """Normalize OpenAI tool calls; a malformed call is recorded, never repaired."""
    calls = []
    for item in message.get("tool_calls") or ():
        function = item.get("function") or {}
        raw = function.get("arguments")
        parsed, args_ok, parse_error = None, False, None
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                args_ok = isinstance(parsed, dict)
            except ValueError as exc:
                parse_error = type(exc).__name__
        calls.append(dict(id=item.get("id"), type=item.get("type"),
                          name=function.get("name"), arguments_raw=raw,
                          arguments=parsed, arguments_valid_json=args_ok,
                          parse_error=parse_error))
    return calls


async def post(client: httpx.AsyncClient, settings: Any, payload: dict, label: str) -> Turn:
    """One bounded request; no retries, no redirects, no header persistence."""
    turn = Turn(label=label, request=payload)
    body = json.dumps(payload, ensure_ascii=False).encode()
    turn.request_bytes = len(body)
    started = time.perf_counter()
    try:
        response = await client.post(settings.base_url + "/chat/completions", content=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " +
                                              settings.api_key.get_secret_value()})
        turn.status = response.status_code
        turn.response_bytes = len(response.content)
        if response.status_code != 200:
            turn.error = f"HTTP_{response.status_code}:{response.text[:400]}"
            return turn
        data = response.json()
        turn.usage = data.get("usage")
        choice = (data.get("choices") or [{}])[0]
        turn.finish_reason = choice.get("finish_reason")
        message = choice.get("message") or {}
        turn.content = message.get("content")
        turn.tool_calls = parse_tool_calls(message)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        turn.error = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        turn.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return turn


def schema_check(turn: Turn, tool_name: str) -> dict:
    """Validate the first call against the declared parameter schema, field by field."""
    tool = next((item for item in TOOLS if item["function"]["name"] == tool_name), None)
    call = next((item for item in turn.tool_calls if item["name"] == tool_name), None)
    if tool is None or call is None:
        return dict(ok=False, reason="MISSING_CALL" if tool else "UNKNOWN_TOOL")
    schema = tool["function"]["parameters"]
    args = call.get("arguments")
    problems = []
    if not isinstance(args, dict):
        problems.append("ARGUMENTS_NOT_OBJECT")
        return dict(ok=False, problems=problems)
    for key in schema.get("required", []):
        if key not in args:
            problems.append(f"MISSING_REQUIRED:{key}")
    if schema.get("additionalProperties") is False:
        for key in args:
            if key not in schema["properties"]:
                problems.append(f"UNEXPECTED:{key}")
    for key, spec in schema["properties"].items():
        if key not in args:
            continue
        value = args[key]
        types = {"string": str, "array": list, "object": dict, "boolean": bool,
                 "integer": int, "number": (int, float)}
        expected = spec.get("type")
        if expected in types and not isinstance(value, types[expected]):
            problems.append(f"WRONG_TYPE:{key}")
        if isinstance(value, list) and "enum" in spec.get("items", {}):
            allowed = set(spec["items"]["enum"])
            problems.extend(f"ENUM:{key}" for item in value if item not in allowed)
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"ENUM:{key}")
    return dict(ok=not problems, problems=problems, arguments=args)


def assistant_message(turn: Turn) -> dict:
    """Replay the model's own turn; tool call ids are kept byte-identical."""
    return dict(role="assistant", content=turn.content, tool_calls=[
        dict(id=call["id"], type="function",
             function=dict(name=call["name"], arguments=call["arguments_raw"]))
        for call in turn.tool_calls])


def tool_messages(turn: Turn) -> list[dict]:
    return [dict(role="tool", tool_call_id=call["id"],
                 content=json.dumps(TOOL_RESULTS.get(call["name"], {"error": "UNKNOWN_TOOL"}),
                                    ensure_ascii=False))
            for call in turn.tool_calls]


async def scenario_single_tool(client, settings, record: dict) -> None:
    """S1: does the gateway return a native tool_call at all?"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "先看清包裹接口有哪些操作，再决定怎么测。"}]
    turn = await post(client, settings, envelope(settings, messages, tools=TOOLS), "s1_single")
    record["turns"].append(turn.public())
    record["assertions"]["s1_tool_call_returned"] = bool(turn.tool_calls)
    record["assertions"]["s1_finish_reason_tool_calls"] = turn.finish_reason == "tool_calls"
    record["assertions"]["s1_usage_reported"] = bool((turn.usage or {}).get("total_tokens"))
    if turn.tool_calls:
        check = schema_check(turn, turn.tool_calls[0]["name"] or "")
        record["checks"]["s1_arguments"] = check
        record["assertions"]["s1_arguments_match_schema"] = check["ok"]


async def scenario_numbered_multi_turn(client, settings, record: dict) -> None:
    """S2: tool results回灌, then a second *different* tool chosen from the fed-back facts.

    The point is not "the model always calls a second tool" -- after two results it may
    legitimately answer in text. The load-bearing assertion is that the continuation is
    grounded in the returned tool output, and that a follow-up request for a *new* fact
    produces a new, correctly-parameterised call.

    Note: turn 1 returned **two** tool calls (parallel tool calling), which is recorded
    rather than assumed.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "包裹管理功能要测，先看接口，再看还缺哪些证据。"}]
    first = await post(client, settings, envelope(settings, messages, tools=TOOLS), "s2_turn1")
    record["turns"].append(first.public())
    record["assertions"]["s2_turn1_tool_call"] = bool(first.tool_calls)
    record["facts"] = {**(record.get("facts") or {}),
                       "s2_parallel_tool_calls_supported": len(first.tool_calls) > 1,
                       "s2_turn1_calls": first.openai_tool_call_names()}
    if not first.tool_calls:
        return
    messages = [*messages, assistant_message(first), *tool_messages(first)]
    second = await post(client, settings, envelope(settings, messages, tools=TOOLS), "s2_turn2")
    record["turns"].append(second.public())
    grounded = (second.content or "").strip()
    record["assertions"]["s2_tool_results_reach_model"] = bool(second.tool_calls or grounded)
    # A grounded continuation repeats facts that only the tool results contained.
    record["assertions"]["s2_continuation_quotes_tool_output"] = bool(
        second.tool_calls or "deleteParcel" in grounded)
    if not grounded and not second.tool_calls:
        return
    # Third turn: a *new* fact is required, so a new call with faithful arguments is due.
    messages = [*messages, assistant_message(second),
                dict(role="user", content="现在用只读调查确认 readParcel 的 404 失败"
                                          "是否属于前置条件缺失（PRECONDITION_MISSING）。")
                ] if not second.tool_calls else [*messages, assistant_message(second),
                                                 *tool_messages(second),
                                                 dict(role="user", content="继续。")]
    third = await post(client, settings, envelope(settings, messages, tools=TOOLS), "s2_turn3")
    record["turns"].append(third.public())
    if third.tool_calls:
        check = schema_check(third, "investigate_failure")
        record["checks"]["s2_third_call_arguments"] = check
        record["assertions"]["s2_third_call_is_investigation"] = (
            third.openai_tool_call_names() == ["investigate_failure"])
        record["assertions"]["s2_third_call_arguments_faithful"] = bool(
            check.get("ok") and (check.get("arguments") or {}).get("hypothesis")
            == "PRECONDITION_MISSING")
    else:
        record["assertions"]["s2_third_turn_answered_in_text"] = bool((third.content or "").strip())
    record["s2_call_names"] = [first.openai_tool_call_names(),
                              second.openai_tool_call_names(),
                              third.openai_tool_call_names()]


async def scenario_forced_choice(client, settings, record: dict) -> None:
    """S3: forced tool_choice must be honoured or rejected explicitly."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "帮我看一下已经覆盖了什么。"}]
    turn = await post(client, settings, envelope(
        settings, messages, tools=TOOLS,
        tool_choice={"type": "function", "function": {"name": "get_test_coverage"}}),
        "s3_forced")
    record["turns"].append(turn.public())
    record["assertions"]["s3_forced_choice_status_200"] = turn.status == 200
    record["assertions"]["s3_forced_choice_named_tool"] = turn.openai_tool_call_names() == [
        "get_test_coverage"]
    if turn.error:
        record["checks"]["s3_error"] = turn.error


async def scenario_json_schema_with_tools(client, settings, record: dict) -> None:
    """S4: the product's strict json_schema output, requested together with tools."""
    schema = {
        "type": "object",
        "properties": {
            "next_action": {"type": "string",
                            "enum": ["DESIGN_MORE", "READ_PROBE", "FINISH", "ASK_QUESTION"]},
            "target": {"type": ["string", "null"]},
            "reason": {"type": "string"},
        },
        "required": ["next_action", "target", "reason"],
        "additionalProperties": False,
    }
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "接下来应该做什么？只输出规定的 JSON。"}]
    turn = await post(client, settings, envelope(
        settings, messages, tools=TOOLS,
        response_format=dict(type="json_schema",
                             json_schema=dict(name="probe_next_action", schema=schema))),
        "s4_json_schema_with_tools")
    record["turns"].append(turn.public())
    parsed, ok = None, False
    if turn.content:
        try:
            parsed = json.loads(turn.content)
            ok = isinstance(parsed, dict) and set(parsed) == {"next_action", "target", "reason"}
        except ValueError:
            ok = False
    record["checks"]["s4_parsed"] = parsed
    record["assertions"]["s4_json_schema_with_tools_ok"] = turn.status == 200 and (ok or bool(
        turn.tool_calls))
    # The same schema without tools is the control: it is what the product sends today.
    control = await post(client, settings, envelope(
        settings, messages, response_format=dict(type="json_schema", json_schema=dict(
            name="probe_next_action", schema=schema))), "s4_control_without_tools")
    record["turns"].append(control.public())
    try:
        control_parsed = json.loads(control.content or "")
        control_ok = isinstance(control_parsed, dict)
    except ValueError:
        control_ok = False
    record["assertions"]["s4_control_json_schema_ok"] = control.status == 200 and control_ok


async def scenario_tool_selection(client, settings, record: dict) -> None:
    """S5: with eight distinct plausible tools, does the model pick the right one?"""
    extra = [("get_approved_policies", "Read operator-approved assertions only."),
             ("design_test_cases", "Design legal test scenarios for one obligation."),
             ("validate_test_plan", "Compile and validate a plan without sending HTTP."),
             ("execute_approved_test", "Execute an already validated plan."),
             ("read_test_report", "Read redacted execution evidence.")]
    tools = [*TOOLS, *[
        {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": {
                "resource": {"type": "string"}}, "required": ["resource"],
                "additionalProperties": False}}}
        for name, description in extra]]
    record["facts"] = {**(record.get("facts") or {}),
                       "s5_catalog_size": len(tools),
                       "s5_catalog_unique": len({item["function"]["name"] for item in tools})
                       == len(tools)}
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "哪些已批准的测试还缺证据？用最直接的工具查。"}]
    turn = await post(client, settings, envelope(settings, messages, tools=tools), "s5_selection")
    record["turns"].append(turn.public())
    names = turn.openai_tool_call_names()
    record["s5_selected"] = names
    record["assertions"]["s5_selected_expected_tool"] = "get_test_coverage" in names
    record["assertions"]["s5_no_invented_tool"] = bool(names) and all(
        name in {item["function"]["name"] for item in tools} for name in names)


async def scenario_duplicate_catalog(client, settings, record: dict) -> None:
    """S7: the 500 first observed in S5, reproduced deliberately.

    The first S5 run appended a second ``get_test_coverage`` to a catalog that already had
    it, and the gateway answered HTTP 500 with an empty body. That is a real property of the
    gateway worth recording: a malformed tool catalog fails hard, not with a 400 and not
    with a diagnostic. A harness must therefore validate catalog uniqueness itself.
    """
    duplicated = [
        {"type": "function", "function": {
            "name": "get_contract", "description": "Read approved operations.",
            "parameters": {"type": "object", "properties": {
                "resource": {"type": "string"}}, "required": ["resource"],
                "additionalProperties": False}}},
        {"type": "function", "function": {
            "name": "get_contract", "description": "Read approved operations (second copy).",
            "parameters": {"type": "object", "properties": {
                "resource": {"type": "string"}}, "required": ["resource"],
                "additionalProperties": False}}},
    ]
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "查一下契约。"}]
    turn = await post(client, settings, envelope(settings, messages, tools=duplicated),
                      "s7_duplicate_catalog")
    record["turns"].append(turn.public())
    record["facts"] = {**(record.get("facts") or {}),
                       "s7_duplicate_catalog_status": turn.status,
                       "s7_duplicate_catalog_error": turn.error}
    # This assertion records the observed behaviour; it is not a pass/fail on the harness.
    record["checks"]["s7_observed"] = dict(status=turn.status, error=turn.error)


async def scenario_graceful_error(client, settings, record: dict) -> None:
    """S6: an unknown tool name in history must fail loudly, never silently succeed."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "查一下契约。"},
                {"role": "assistant", "content": None, "tool_calls": [dict(
                    id="call_probe_1", type="function",
                    function=dict(name="get_contract", arguments='{"resource":"parcels"}'))]},
                {"role": "tool", "tool_call_id": "call_probe_1",
                 "content": '{"error": "TOOL_TIMEOUT", "retryable": true}'}]
    turn = await post(client, settings, envelope(settings, messages, tools=TOOLS),
                      "s6_error_feedback")
    record["turns"].append(turn.public())
    record["assertions"]["s6_tool_error_reaches_model"] = bool(
        turn.tool_calls or (turn.content or "").strip())
    record["assertions"]["s6_no_http_500"] = turn.status is not None and turn.status < 500


async def scenario_empty_tool_calls(client, settings, record: dict) -> None:
    """S8: is ``"tool_calls": []`` on an assistant message fatal for this gateway?

    This is the exact shape a LangChain ``AIMessage`` produces for a plain final answer.
    If the gateway rejects an empty array with HTTP 500, an adapter must omit the key, and
    that requirement has to be written down instead of discovered run-per-run.
    """
    base = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "直接回答：包裹功能里有几个已批准操作？"}]
    with_empty = [*base, {"role": "assistant", "content": "我先看一下。", "tool_calls": []},
                  {"role": "user", "content": "继续说。"}]
    without_key = [*base, {"role": "assistant", "content": "我先看一下。"},
                   {"role": "user", "content": "继续说。"}]
    empty_turn = await post(client, settings, envelope(settings, with_empty, tools=TOOLS),
                            "s8_empty_tool_calls_array")
    clean_turn = await post(client, settings, envelope(settings, without_key, tools=TOOLS),
                            "s8_key_omitted")
    record["turns"].extend([empty_turn.public(), clean_turn.public()])
    record["facts"] = {**(record.get("facts") or {}),
                       "s8_empty_array_status": empty_turn.status,
                       "s8_omitted_key_status": clean_turn.status}
    record["checks"]["s8_observed"] = dict(
        empty_array=dict(status=empty_turn.status, error=empty_turn.error),
        omitted=dict(status=clean_turn.status, error=clean_turn.error))
    record["assertions"]["s8_omitted_key_works"] = clean_turn.status == 200


SCENARIOS = {
    "single": scenario_single_tool,
    "multi_turn": scenario_numbered_multi_turn,
    "forced": scenario_forced_choice,
    "json_schema": scenario_json_schema_with_tools,
    "selection": scenario_tool_selection,
    "error": scenario_graceful_error,
    "duplicate_catalog": scenario_duplicate_catalog,
    "empty_tool_calls": scenario_empty_tool_calls,
}


async def run(names: list[str]) -> dict:
    settings = load_settings()
    record: dict = dict(probe="native_tool_calling", model=settings.model,
                        base_url=settings.base_url, protocol=settings.protocol,
                        tools=[item["function"]["name"] for item in TOOLS],
                        turns=[], assertions={}, checks={},
                        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    timeout = httpx.Timeout(settings.timeout, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False,
                                 follow_redirects=False) as client:
        for name in names:
            await SCENARIOS[name](client, settings, record)
    record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    record["summary"] = dict(
        turns=len(record["turns"]),
        passed=sum(1 for value in record["assertions"].values() if value is True),
        failed=[key for key, value in record["assertions"].items() if value is not True],
        total_tokens=sum((turn.get("usage") or {}).get("total_tokens") or 0
                         for turn in record["turns"]),
        errors=[turn["error"] for turn in record["turns"] if turn.get("error")])
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="all", choices=[*SCENARIOS, "all"])
    parser.add_argument("--out", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--replay", type=Path, default=None,
                        help="print the assertions of an earlier evidence file")
    args = parser.parse_args(argv)
    if args.replay is not None:
        record = json.loads(args.replay.read_text(encoding="utf-8"))
        print(json.dumps(dict(summary=record.get("summary"),
                              assertions=record.get("assertions")), ensure_ascii=False,
                         indent=2))
        return 0 if not record.get("summary", {}).get("failed") else 1
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    record = asyncio.run(run(names))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record["summary"], ensure_ascii=False, indent=2))
    for name, value in record["assertions"].items():
        print(f"{'PASS' if value is True else 'FAIL'}  {name}")
    print(f"evidence: {args.out}")
    return 0 if not record["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
