"""Stage 1 admission test, second half: ecnu-max **through LangChain**, not through httpx.

``probe_native_tool_calling.py`` proves the gateway speaks the protocol. This script proves
that a ``BaseChatModel`` adapter drives it through LangChain's own machinery -- the exact
interface a Deep Agents harness uses:

* ``bind_tools`` -> ``ainvoke`` returns ``AIMessage.tool_calls``;
* ``ToolMessage`` results are accepted back and grounded continuation follows;
* a third turn forces a *different* tool with faithful arguments;
* structured output parses through LangChain's own wrapper;
* the ``tool_calls: []`` gateway defect stays fixed by this adapter (regression).

Run with the experiment interpreter, which is the only one that has langchain::

    $env:PYTHONUTF8='1'
    .\\.venv-deepagents\\Scripts\\python.exe experiments/deepagents_p1/probe_langchain.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agenttest.deep_harness.chat_model import (  # noqa: E402
    ModelHarnessError,
    build_chat_model,
)
from agenttest.llm_planner import ModelSettings  # noqa: E402

DEFAULT_EVIDENCE = REPO / "experiments" / "deepagents_p1" / "evidence" / "langchain-adapter.json"
SYSTEM = ("You are a bounded API-testing agent. Use the supplied tools; never invent a "
          "tool, an argument or a result. Answer in Chinese when you answer in text.")

CONTRACT = {
    "resource": "parcels",
    "operations": [
        {"operation_id": "createParcel", "method": "POST", "path": "/api/parcels"},
        {"operation_id": "readParcel", "method": "GET", "path": "/api/parcels/{id}"},
        {"operation_id": "deleteParcel", "method": "DELETE", "path": "/api/parcels/{id}"},
    ],
}
COVERAGE = {"resource": "parcels", "open": ["deleteParcel:204", "readParcel:404-after-delete"],
            "satisfied": ["createParcel:201"]}
INVESTIGATION = {"operation_id": "readParcel", "hypothesis": "PRECONDITION_MISSING",
                 "finding": "the 404 read ran while the parcel still existed"}


@tool
def get_contract(resource: str, methods: list[str] | None = None) -> dict:
    """Read the approved OpenAPI operations for one resource family."""
    return CONTRACT


@tool
def get_test_coverage(resource: str) -> dict:
    """List which approved testing obligations already have evidence."""
    return COVERAGE


@tool
def investigate_failure(operation_id: str, hypothesis: str) -> dict:
    """Run one approved read-only investigation for a failed check.

    hypothesis is PRECONDITION_MISSING, TARGET_UNAVAILABLE or CONTRACT_MISMATCH.
    """
    return INVESTIGATION


TOOLS = [get_contract, get_test_coverage, investigate_failure]


class NextAction(BaseModel):
    """Structured decision the harness could consume instead of free text."""

    next_action: str = Field(description="DESIGN_MORE, READ_PROBE, FINISH or ASK_QUESTION")
    target: str | None = Field(default=None, description="Open obligation id, if any")
    reason: str = Field(description="One sentence citing existing evidence")


def turn_record(name: str, ai: AIMessage, elapsed_ms: float, stats: dict) -> dict:
    return dict(turn=name, elapsed_ms=elapsed_ms, stats=stats,
                content=(ai.content if isinstance(ai.content, str) else str(ai.content)),
                tool_calls=[dict(name=call["name"], args=call["args"], id=call.get("id"))
                            for call in ai.tool_calls],
                finish_reason=(ai.response_metadata or {}).get("finish_reason"),
                usage_metadata=ai.usage_metadata)


async def run(args) -> dict:
    settings = ModelSettings.from_env_file(REPO / ".env")
    model = build_chat_model(settings)
    bound = model.bind_tools(TOOLS)
    record: dict = dict(probe="langchain_adapter", model=settings.model,
                        base_url=settings.base_url, adapter="EcnuxChatModel",
                        tool_names=[item.name for item in TOOLS], turns=[],
                        assertions={}, checks={},
                        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    history: list = [SystemMessage(content=SYSTEM),
                     HumanMessage(content="包裹管理功能要测，先看接口，再看还缺哪些证据。")]
    started = time.perf_counter()
    first = await bound.ainvoke(history)
    record["turns"].append(turn_record("l1_turn1", first, round(
        (time.perf_counter() - started) * 1000, 3), model.stats.model_dump(mode="json")))
    record["assertions"]["l1_langchain_tool_calls"] = bool(first.tool_calls)
    record["assertions"]["l1_calls_are_declared_tools"] = bool(first.tool_calls) and all(
        call["name"] in {item.name for item in TOOLS} for call in first.tool_calls)
    if not first.tool_calls:
        record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return record

    history = [*history, first]
    tool_output = {"get_contract": CONTRACT, "get_test_coverage": COVERAGE,
                   "investigate_failure": INVESTIGATION}
    for call in first.tool_calls:
        history.append(ToolMessage(
            content=json.dumps(tool_output.get(call["name"], {"error": "UNKNOWN_TOOL"}),
                               ensure_ascii=False),
            tool_call_id=call["id"]))
    started = time.perf_counter()
    second = await bound.ainvoke(history)
    record["turns"].append(turn_record("l2_turn2_after_tool_results", second, round(
        (time.perf_counter() - started) * 1000, 3), model.stats.model_dump(mode="json")))
    text = second.content if isinstance(second.content, str) else ""
    record["assertions"]["l2_tool_results_reach_model"] = bool(second.tool_calls or text.strip())
    record["assertions"]["l2_grounded_in_tool_output"] = bool(
        second.tool_calls or "deleteParcel" in text)

    # This turn is the regression: the history now contains an AIMessage whose tool_calls
    # list is empty, which the raw gateway rejects with HTTP 500 if serialized as [].
    history = [*history, second,
               HumanMessage(content="用只读调查确认 readParcel 的 404 是前置条件缺失。")]
    started = time.perf_counter()
    third = await bound.ainvoke(history)
    record["turns"].append(turn_record("l3_turn3_after_empty_tool_calls", third, round(
        (time.perf_counter() - started) * 1000, 3), model.stats.model_dump(mode="json")))
    record["assertions"]["l3_no_gateway_500_after_final_answer"] = True
    record["assertions"]["l3_third_call_is_investigation"] = [
        call["name"] for call in third.tool_calls] == ["investigate_failure"]
    record["assertions"]["l3_arguments_faithful"] = bool(third.tool_calls) and (
        third.tool_calls[0]["args"].get("hypothesis") == "PRECONDITION_MISSING")

    # Structured output through LangChain's own function-calling strategy.
    structured = model.with_structured_output(NextAction, method="function_calling")
    try:
        decision = await structured.ainvoke([
            SystemMessage(content=SYSTEM),
            HumanMessage(content="读接口和覆盖都做完了：create 已通过，delete 只有 204 预期，"
                                 "readParcel 的 404 预期没有删除前置。请给出下一步动作。")])
        record["checks"]["structured_output"] = decision.model_dump()
        record["assertions"]["l4_structured_output_parsed"] = isinstance(decision, NextAction)
        record["assertions"]["l4_structured_action_known"] = decision.next_action in {
            "DESIGN_MORE", "READ_PROBE", "FINISH", "ASK_QUESTION"}
    except (ModelHarnessError, ValueError, TypeError) as exc:
        record["checks"]["structured_output_error"] = f"{type(exc).__name__}: {exc}"[:300]
        record["assertions"]["l4_structured_output_parsed"] = False

    record["usage"] = dict(calls=sum(1 for _ in record["turns"]),
                           total_tokens=sum((turn["stats"] or {}).get("total_tokens") or 0
                                            for turn in record["turns"]))
    record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--replay", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)
    if args.replay is not None:
        record = json.loads(args.replay.read_text(encoding="utf-8"))
        print(json.dumps(record.get("assertions"), ensure_ascii=False, indent=2))
        return 0 if all(value is True for value in record.get("assertions", {}).values()) else 1
    record = asyncio.run(run(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [key for key, value in record["assertions"].items() if value is not True]
    print(json.dumps(dict(turns=len(record["turns"]), usage=record.get("usage"),
                          failed=failed), ensure_ascii=False, indent=2))
    for name, value in record["assertions"].items():
        print(f"{'PASS' if value is True else 'FAIL'}  {name}")
    print(f"evidence: {args.out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
