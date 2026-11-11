"""The Deep Agents loop: the eight product tools and nothing else.

The P1 brief allows this harness only if its default capabilities can be *reliably* removed.
The measured facts behind the configuration below (see ``experiments/deepagents_p1/
audit_harness.py`` and the P1 report):

* Deep Agents always registers ``FilesystemMiddleware`` and ``SubAgentMiddleware``; excluding
  them raises at construction time, so they cannot be removed, only narrowed.
* ``HarnessProfile.excluded_tools`` does remove the individual tools: ``ls/read_file/write_file/
  edit_file/delete/glob/grep/execute``.
* ``GeneralPurposeSubagentProfile(enabled=False)`` removes the general-purpose subagent, which
  would otherwise re-introduce the file and shell tools.
* The profile registry is process-global and keyed by provider+model, so the profile name is
  asserted before use; a silent fallback to ``default`` would widen authority.

What is left for the model is exactly the eight logical tools, plus ``task`` (delegation) which
is disabled below. Nothing in the resulting catalogue can read the filesystem, run a command or
reach an arbitrary URL, so the AgentTest security requirements are not lowered to fit the
framework -- either the catalogue is exactly this, or the loop refuses to start.
"""

import json

from agenttest.deep_harness.product_tools import ToolRefused

HARNESS_TOOL_NAMES = (
    "get_contract",
    "get_approved_policies",
    "get_test_coverage",
    "design_test_cases",
    "validate_test_plan",
    "execute_approved_test",
    "read_test_report",
    "investigate_failure",
)

# Tools the framework would add on its own; every one of these must be gone before a run.
DENIED_DEFAULT_TOOLS = ("ls", "read_file", "write_file", "edit_file", "delete", "glob",
                        "grep", "execute", "task")

SYSTEM_PROMPT = """你是 AgentTest 的测试助手。你只能通过工具完成工作：不能执行代码、读写文件或直接发
HTTP。

可用工具：
- get_contract / get_approved_policies / get_test_coverage：只读事实。
- design_test_cases：请设计模型针对已批准操作提出场景，本身不发请求。
- validate_test_plan：确定性预检；只有预检通过的 plan_id 才能执行。
- execute_approved_test：执行一个 plan_id，是唯一的业务写入口。
- read_test_report / investigate_failure：读取证据与只读复核。

规则：
1. 不要臆造 operation_id、状态码或断言；只能使用 get_approved_policies 里已批准的内容。
2. 若想看“删除后再查询应 404”，必须先删除同一资源，再查询；不要对仍然存在的资源期待 404。
3. 失败的结论只有证据充分时才说“缺陷”；前置条件未成立时说明“依据不足”。
4. 不要重复执行与已执行计划语义相同的场景；补测必须带来新信息。
5. 完成后用简短中文总结：执行了什么、结论是什么、还有什么未覆盖。
"""


class HarnessUnavailable(RuntimeError):
    """The narrowed catalogue could not be guaranteed, so the loop must not run."""


def build_profile_name(ls_params: dict) -> str:
    """Same key the framework uses: ``ls_provider[:ls_model_name]``."""
    provider = (ls_params or {}).get("ls_provider") or "unknown"
    model = (ls_params or {}).get("ls_model_name")
    return f"{provider}:{model}" if model else provider


def install_narrow_profile(model) -> tuple[str, object]:
    """Register the profile that removes every default capability, and verify it is the one.

    The registry is process-global and keyed by ``provider[:model]``, so registering is not
    proof. Two things are checked: the profile is read back from *both* keys the framework may
    resolve (the exact ``provider:model`` and the bare provider), and every denied tool plus the
    general-purpose subagent is confirmed off. A provider-level default that still allows a
    denied tool means the loop must not start.
    """
    from deepagents.profiles import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        register_harness_profile,
    )
    from deepagents.profiles.harness.harness_profiles import _get_harness_profile

    profile = HarnessProfile(
        excluded_tools=frozenset(DENIED_DEFAULT_TOOLS),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    )
    ls_params = model._get_ls_params()
    provider = ls_params.get("ls_provider") or "unknown"
    identifier = getattr(model, "model_name", None) or ls_params.get("ls_model_name")
    exact = f"{provider}:{identifier}" if identifier else provider
    for key in {provider, exact}:
        register_harness_profile(key, profile)
    for key in {provider, exact}:
        stored = _get_harness_profile(key)
        if stored is None:
            raise HarnessUnavailable("无法回读已注册的 harness profile：" + key)
        missing = set(DENIED_DEFAULT_TOOLS) - set(stored.excluded_tools or ())
        if missing:
            raise HarnessUnavailable("profile 未排除默认工具：" + ", ".join(sorted(missing)))
        if not (stored.general_purpose_subagent is not None
                and stored.general_purpose_subagent.enabled is False):
            raise HarnessUnavailable("profile 未禁用 general-purpose subagent。")
    return exact, profile


def catalogue_of(agent) -> list[str]:
    """Every tool the executor could dispatch, read from the compiled graph.

    This is the *outer* bound, not the model's view: the profile filters the model request and
    rejects excluded names at the call boundary, while the executor keeps them registered.
    ``verify_enforcement`` measures both halves.
    """
    names: list[str] = []
    for node in getattr(agent, "nodes", {}).values():
        bound = getattr(node, "bound", None)
        by_name = getattr(bound, "tools_by_name", None)
        if isinstance(by_name, dict):
            names.extend(by_name)
    return sorted({name for name in names if name})


async def verify_enforcement(model, *, prompt: str = "离线校验") -> dict:
    """Prove, offline, that the denied capabilities are neither advertised nor executed.

    Markers: a benign tool passed in must be the *only* thing the model sees; the denied
    filesystem/executors must be absent from the request and rejected at the call boundary."""
    import tempfile
    from pathlib import Path

    from deepagents import create_deep_agent
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import StructuredTool

    async def marker_probe() -> dict:
        """A benign tool that must survive filtering, so the probe cannot pass vacuously."""
        return {"ok": True}

    marker = StructuredTool.from_function(coroutine=marker_probe, name="probe_marker",
                                          description="enforcement probe marker")

    scripted_observed: list = []
    identifier = getattr(model, "model_name", None) or ""
    install_narrow_profile(model)
    probe_error = None
    written: list[str] = []
    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / "should-not-exist.txt"
        scripted = scripted_model([
            AIMessage(content="", tool_calls=[dict(name="write_file", args={
                "file_path": str(target), "content": "leak"}, id="probe-1")]),
            AIMessage(content="done"),
        ], scripted_observed, identifier)
        agent = create_deep_agent(model=scripted, tools=[marker],
                                  system_prompt="offline enforcement probe")
        try:
            result = await agent.ainvoke({"messages": [HumanMessage(content=prompt)]},
                                         config={"recursion_limit": 6})
        except Exception as exc:  # a refusal at the boundary is also acceptable evidence
            probe_error = type(exc).__name__
            result = {"messages": []}
        outputs = [str(getattr(item, "content", ""))[:200]
                   for item in result.get("messages") or []
                   if getattr(item, "type", "") == "tool"]
        if target.exists():
            written.append(str(target))
    view = scripted_observed[0]["tool_names"] if scripted_observed else []
    advertised = sorted({name for name in view if name})
    denied_advertised = sorted(set(advertised) & set(DENIED_DEFAULT_TOOLS))
    rejected = any("is not available" in text for text in outputs)
    if not advertised and probe_error:
        # A probe that failed for an unrelated reason proves nothing, so it must not pass.
        raise HarnessUnavailable("权限探针未能运行，无法证明默认工具被禁用：" + probe_error)
    if denied_advertised:
        raise HarnessUnavailable("模型仍看到被禁用的工具：" + ", ".join(denied_advertised))
    if set(advertised) != {"probe_marker"}:
        raise HarnessUnavailable("模型看到的工具集不是预期的收窄结果：" + ", ".join(advertised))
    if not rejected:
        raise HarnessUnavailable("被禁用的 write_file 调用没有被拒绝，拒绝在未验证权限时运行。")
    if written:
        raise HarnessUnavailable("被禁用的 write_file 实际写入了文件：" + ", ".join(written))
    return dict(model_view=advertised, boundary_rejected=rejected,
                probe_error=probe_error, tool_outputs=outputs,
                files_written=written)


def scripted_model(script, observed, model_name: str):
    """A model that replays ``script`` and records each bound tool catalog; no network.

    It reports the *same* provider and model identifier the real adapter reports, so the harness
    profile resolves to the same key and the enforcement probe measures the real configuration
    instead of a look-alike.
    """
    from agenttest.deep_harness.chat_model import EcnuxChatModel
    from agenttest.llm_planner import ModelSettings

    unit = ModelSettings(model=model_name, base_url="https://unit.invalid/v1",
                         api_key="offline-probe-not-a-secret", max_input_tokens=1000,
                         max_total_tokens=2000, max_output_tokens=512)

    class _Probe(EcnuxChatModel):

        def _payload(self, messages, tools=None, tool_choice=None):
            # Record what would go on the wire: the exact catalog the model can see.
            schemas = []
            for item in tools or ():
                schema = item.get("function", {}) if isinstance(item, dict) else {}
                schemas.append(schema.get("name"))
            observed.append(dict(turn=len(observed), tool_names=schemas))
            return super()._payload(messages, tools, tool_choice)

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            from langchain_core.messages import AIMessage as _AI
            from langchain_core.outputs import ChatGeneration, ChatResult

            turn = len(observed)
            # Build the real payload so the recorded catalog is exactly what a model would see.
            self._payload(messages, kwargs.get("tools") if isinstance(kwargs.get("tools"), list)
                          else None, kwargs.get("tool_choice"))
            message = (script[min(turn, len(script) - 1)] if script
                       else _AI(content="done"))
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _Probe(settings=unit)


def verify_catalogue(agent) -> dict:
    """Refuse to run unless the catalogue is exactly the eight logical tools."""
    names = catalogue_of(agent)
    denied_present = sorted(set(names) & set(DENIED_DEFAULT_TOOLS))
    unexpected = sorted(set(names) - set(HARNESS_TOOL_NAMES) - set(DENIED_DEFAULT_TOOLS))
    if denied_present or unexpected:
        raise HarnessUnavailable(
            "工具目录未被可靠收窄：denied=" + json.dumps(denied_present, ensure_ascii=False)
            + " unexpected=" + json.dumps(unexpected, ensure_ascii=False))
    if not names:
        raise HarnessUnavailable("无法从 agent 读取已绑定工具，拒绝在未验证的情况下运行。")
    return dict(bound_tools=names, denied_present=denied_present, unexpected=unexpected)


# -------------------------------------------------------------------------- LangChain tools


def build_tools(session):
    """The eight tools as LangChain tools; arguments are validated by the session itself.

    A refusal is *feedback*, not a crash: a budget or an unapproved operation must come back to
    the model as a result it can read, because the loop is supposed to adapt. Only a genuine
    defect escapes, and even then the run records it instead of losing the whole trace.
    """
    from langchain_core.tools import StructuredTool

    async def call(name: str, action, record_ok: bool = True):
        import inspect

        try:
            produced = action()
            result = await produced if inspect.isawaitable(produced) else produced
            if record_ok:
                session.record(name, ok=True)
            return _bounded_view(result)
        except ToolRefused as exc:
            session.record(name, refused=str(exc))
            return dict(refused=True, reason=str(exc))
        except Exception as exc:  # a defect must be visible, not fatal to the whole run
            session.record(name, error=f"{type(exc).__name__}: {exc}"[:300])
            return dict(error=type(exc).__name__, detail=str(exc)[:300])

    async def contract() -> dict:
        return await call("get_contract", session.contract_view)

    async def policies() -> dict:
        return await call("get_approved_policies", session.policy_view)

    async def coverage() -> dict:
        return await call("get_test_coverage", session.coverage_view)

    async def design(operations: list[str], request: str = "") -> dict:
        async def action():
            result = await session.design(operations, request)
            session.record("design_test_cases", operations=operations,
                           designs=len(result.get("designs") or ()))
            return result

        return await call("design_test_cases", action, record_ok=False)

    async def validate(design_id: str, indexes: list[int] | None = None) -> dict:
        async def action():
            result = await session.validate(design_id, indexes)
            session.record("validate_test_plan", design_id=design_id,
                           accepted=result.get("accepted"))
            return result

        return await call("validate_test_plan", action, record_ok=False)

    async def execute(plan_id: str) -> dict:
        async def action():
            result = await session.execute(plan_id)
            session.record("execute_approved_test", plan_id=plan_id,
                           outcome=result.get("outcome"), skipped=bool(result.get("skipped")))
            return result

        return await call("execute_approved_test", action, record_ok=False)

    async def read_report(run_id: str) -> dict:
        async def action():
            result = session.report_view(run_id)
            session.record("read_test_report", run_id=run_id)
            return result

        return await call("read_test_report", action, record_ok=False)

    async def investigate(run_id: str, operation_id: str | None = None) -> dict:
        async def action():
            result = session.investigate(run_id, operation_id)
            session.record("investigate_failure", run_id=run_id, operation_id=operation_id)
            return result

        return await call("investigate_failure", action, record_ok=False)

    return [
        StructuredTool.from_function(coroutine=contract, name="get_contract",
                                     description="读取已批准的操作目录。"),
        StructuredTool.from_function(coroutine=policies, name="get_approved_policies",
                                     description="读取已批准的断言与资源生命周期规则。"),
        StructuredTool.from_function(coroutine=coverage, name="get_test_coverage",
                                     description="读取已测量的覆盖与请求预算。"),
        StructuredTool.from_function(coroutine=design, name="design_test_cases",
                                     description="请设计模型针对已批准操作提出场景（不发请求）。"),
        StructuredTool.from_function(coroutine=validate, name="validate_test_plan",
                                     description="对场景执行确定性预检，返回可执行 plan_id。"),
        StructuredTool.from_function(coroutine=execute, name="execute_approved_test",
                                     description="执行一个已通过预检的 plan_id。"),
        StructuredTool.from_function(coroutine=read_report, name="read_test_report",
                                     description="读取某次运行的报告与检查证据。"),
        StructuredTool.from_function(coroutine=investigate, name="investigate_failure",
                                     description="对失败步骤做只读复核。"),
    ]


def _bounded_view(payload: dict) -> dict:
    """Cap one tool result so a single read cannot consume the whole context."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= MAX_TOOL_CHARS:
        return payload
    return dict(truncated=True, note="结果过长已截断，请缩小查询范围。",
                preview=text[:MAX_TOOL_CHARS])


MAX_TOOL_CHARS = 8_000


async def run_goal(session, model, *, recursion_limit: int = 40) -> dict:
    """Run one natural-language goal through the harness and report what it actually did.

    The framework owns the inner loop (model turn -> tool -> model turn); ``recursion_limit`` is
    the independent, finite stop, exactly as the LangGraph supervisor is bounded. A limit hit is
    recorded as ``LIMIT`` rather than reported as success, and the transcript is evidence, not a
    claim: the caller decides whether the run produced value.
    """
    from deepagents import create_deep_agent
    from langchain_core.messages import HumanMessage

    install_narrow_profile(model)
    enforcement = await verify_enforcement(model)
    tools = build_tools(session)
    agent = create_deep_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
    catalogue = dict(bound_tools=catalogue_of(agent), enforcement=enforcement,
                     expected=list(HARNESS_TOOL_NAMES))
    started = 0
    transcript: list[dict] = []
    stopped = "ANSWER"
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(content=session.text)]},
                                     config={"recursion_limit": recursion_limit})
        messages = result["messages"]
    except Exception as exc:  # the framework's own stop is evidence, not a crash
        from langgraph.errors import GraphRecursionError

        if not isinstance(exc, GraphRecursionError):
            raise
        messages, stopped = [], "LIMIT"
    transcript.extend(_turns(messages, already=started))
    return dict(goal=session.text, catalogue=catalogue, stopped=stopped,
                recursion_limit=recursion_limit,
                tool_calls=list(session.calls),
                transcript=transcript, plans=list(session.plan_meta.values()),
                runs=[dict(plan_id=plan_id, run_id=report.run_id,
                           outcome=report.outcome.value, completed=report.completed)
                      for plan_id, report in session.runs.items()],
                answer=_final_answer(messages), requests=session.requests_used)


def _turns(messages, already=0) -> list[dict]:
    """A compact, secret-free record of what the model said and which tools it called."""
    turns = []
    for message in messages[already:]:
        kind = getattr(message, "type", None)
        calls = getattr(message, "tool_calls", None) or []
        for call in calls:
            turns.append(dict(kind="TOOL_CALL", name=call.get("name"), args=call.get("args")))
        if kind == "tool":
            content = getattr(message, "content", "")
            turns.append(dict(kind="TOOL_RESULT", name=getattr(message, "name", None),
                              preview=str(content)[:600]))
        if kind == "ai" and not calls:
            text = getattr(message, "content", "")
            if isinstance(text, list):
                text = "".join(str(item.get("text", "")) for item in text
                               if isinstance(item, dict))
            if text:
                turns.append(dict(kind="ANSWER", text=str(text)[:2000]))
    return turns


def _final_answer(messages) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai" and not getattr(message, "tool_calls", None):
            content = getattr(message, "content", "")
            if isinstance(content, list):
                content = "".join(str(item.get("text", "")) for item in content
                                  if isinstance(item, dict))
            return str(content)[:4000]
    return ""
