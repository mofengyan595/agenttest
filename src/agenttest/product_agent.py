"""The product's autonomous test runtime: Deep Agents drives the existing deterministic services.

P1 established that ``ecnu-max`` provides real native tool calling through the adapter, that
Deep Agents can be narrowed to exactly the domain tools, and that the deterministic
Planning IR / preflight / policy / Executor chain stays the single exit for business HTTP. P2
turns that experiment into the product path:

* the **only** agent loop is Deep Agents' graph (``create_deep_agent``). There is no Python
  ``while`` around the model and no second supervisor deciding the next action;
* every tool is a narrow door onto machinery the product already owns: ``run_test_group`` calls
  the *same* ``ProductTasks.run_group`` the deterministic round used, and the obligation view
  comes from ``product_obligations.supervision_facts`` -- no second coverage calculation;
* the model chooses *what to test next* from a server-computed candidate set, reacts to
  structured refusals (missing precondition, rejected plan, duplicate retest, budget) and
  stops. It never authors an expectation, a URL, a command or a file;
* completion is decided afterwards by :mod:`agenttest.task_completion`, from measured
  evidence only. The model's closing prose is stored as an answer and is never a verdict.

Migration note: ``ProductTasks.supervise`` / ``RoundSupervisor`` remain importable as the
explicit ``LEGACY_SUPERVISOR`` fallback, but they are no longer the default path and are never
started beside this runtime.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from agenttest.deep_harness.loop import (
    _final_answer,
    _turns,
    catalogue_of,
    install_narrow_profile,
    verify_enforcement,
)
from agenttest.deep_harness.product_tools import ProductSession
from agenttest.deep_harness.skills import SKILL_ROOT, skill_files, skill_filesystem
from agenttest.product_obligations import executed_evidence, supervision_facts
from agenttest.report import fingerprint
from agenttest.task_completion import (
    PRE_RELEASE,
    classify_task,
    evaluate,
    public_view,
    subject_scope,
)

ENGINE = "DEEP_AGENTS"
LEGACY = "LEGACY_SUPERVISOR"
DETERMINISTIC = "DETERMINISTIC"
AUTO = "AUTO"

TOOL_NAMES = (
    "get_contract",
    "get_approved_policies",
    "get_test_scope",
    "get_test_coverage",
    "run_test_group",
    "run_open_obligation",
    "read_test_report",
    "investigate_failure",
)

STOP_ANSWER = "ANSWER"
STOP_MODEL_CALL_LIMIT = "MODEL_CALL_LIMIT"
STOP_TOOL_CALL_LIMIT = "TOOL_CALL_LIMIT"
STOP_TURN_LIMIT = "TURN_LIMIT"
STOP_MODEL_ERROR = "MODEL_ERROR"
STOP_RUNTIME_ERROR = "RUNTIME_ERROR"
STOP_CANCELLED = "CANCELLED"
STOP_AUTHORITY_CHANGED = "AUTHORITY_CHANGED"
STOP_DEADLINE = "DEADLINE"

MAX_GROUP_RUNS = 2          # one broad attempt plus one informed redesign, then stop
MAX_RESULT_BYTES = 6_000    # one tool result, re-sent on every following model turn
# Context editing threshold for old tool output. P2 re-sent the first contract/policy/scope
# reads on every later turn (~20 KB per request by turn 3). Eight thousand approximate tokens
# leaves the recent four results untouched and only drops what has already been superseded.
CONTEXT_CLEAR_TRIGGER_TOKENS = 8_000
CLEARED_TOOL_RESULT = "[旧工具结果已清理；按 run_id 或 refresh=true 重新读取]"

SYSTEM_PROMPT = """你是 AgentTest 的接口测试助手。先根据原始目标选读一个相关 Skill，然后调用
get_test_scope 获取本轮已批准分组和义务。Skill 指导测试方法，授权、断言和完成判据以工具为准。
缺少适用 Skill 时，仍按 get_test_scope → 测试动作 → get_test_coverage 执行。
用工具返回的 ID 和 next_step。只在缺少事实时读契约或策略，避免重复读取。
预算允许时处理可执行的未满足义务。失败时使用 error.code/hint 调整设计或停止，禁止等价重试。
只经受控测试工具执行 HTTP。遵守批准断言、身份、前置条件和资源清理。结果未知的写操作不得重放。
结束时用中文引用真实证据，说明发现、未覆盖项和清理状态。完成状态由确定性判据计算。
"""


class AgentRuntimeUnavailable(RuntimeError):
    """An explicitly requested runtime cannot be guaranteed, so it must not be replaced silently."""


def _harness_available() -> bool:
    import importlib.util

    return bool(importlib.util.find_spec("deepagents")
                and importlib.util.find_spec("langchain"))


def resolve_engine(project, service) -> tuple[str, str]:
    """Resolve the autonomous runtime once per task; the reason is always reportable.

    ``AUTO`` (the project default) uses Deep Agents whenever the deployment configured a
    model factory and the optional dependencies are installed; otherwise the deterministic
    round runs unchanged and the reason is recorded. A project that *explicitly* asks for
    Deep Agents fails closed when it cannot be honoured instead of silently degrading.
    """
    budget = getattr(project.registration, "budget", None)
    requested = (getattr(budget, "loop_engine", AUTO) or AUTO).upper()
    factory = getattr(service, "agent_model_factory", None)
    installed = _harness_available()
    if requested == DETERMINISTIC:
        return DETERMINISTIC, "PROJECT_REQUESTED_DETERMINISTIC"
    if requested == LEGACY:
        return LEGACY, "PROJECT_REQUESTED_LEGACY_SUPERVISOR"
    if requested == ENGINE:
        if factory is None:
            raise AgentRuntimeUnavailable("AGENT_RUNTIME_MODEL_NOT_CONFIGURED")
        if not installed:
            raise AgentRuntimeUnavailable("AGENT_RUNTIME_DEPENDENCIES_NOT_INSTALLED")
        return ENGINE, "PROJECT_REQUESTED_DEEP_AGENTS"
    if factory is not None and installed:
        return ENGINE, "DEFAULT_DEEP_AGENTS"
    return DETERMINISTIC, ("AGENT_RUNTIME_MODEL_NOT_CONFIGURED" if factory is None
                           else "AGENT_RUNTIME_DEPENDENCIES_NOT_INSTALLED")


def _transient_model_error(exc: Exception) -> bool:
    """Only a transient gateway failure may be retried; budgets and rejections never are."""
    code = str(getattr(exc, "code", "") or "")
    if code in {"MODEL_TOKEN_BUDGET", "MODEL_CALL_BUDGET", "MODEL_INPUT_REJECTED",
                "MODEL_TOOL_CATALOG_INVALID", "MODEL_SECRET_OUTPUT", "MODEL_REFUSAL",
                "MODEL_USAGE_UNKNOWN"}:
        return False
    text = f"{code} {exc}"
    return any(marker in text for marker in (
        "MODEL_SERVICE_ERROR", "MODEL_TIMEOUT", "MODEL_CONNECTION_ERROR",
        "concurrency", "429", "502", "503", "504", "timed out"))


def _json(payload, limit: int = MAX_RESULT_BYTES) -> str:
    """Serialize one tool result under a byte budget without losing the repair fields.

    P2'S 6 KB preview cut from the front of the message, so a rejection whose ``error`` sat
    after a long case list lost exactly the code and detail the model needed to repair it.
    The essential keys are always kept in full; only the preview of the rest is truncated.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    if isinstance(payload, dict):
        head_keys = ("ok", "error", "status", "group_id", "candidate_id", "run_id", "outcome",
                     "executed", "requests", "budget", "next_step", "evidence_refs")
        head = {key: payload[key] for key in head_keys if key in payload}
        head["truncated"] = True
        head["note"] = ("结果过大：保留错误和证据引用，其余为前部预览；"
                        "用更窄的参数读取完整证据（如 read_test_report(run_id)）。")
        head["preview"] = raw[:max(1024, limit // 2)].decode("utf-8", "ignore")
        return json.dumps(head, ensure_ascii=False, default=str)
    preview = raw[:limit].decode("utf-8", "ignore")
    return json.dumps(dict(truncated=True, note="结果过大已截断。", preview=preview),
                      ensure_ascii=False)


def _refusal(code: str, message: str, *, retryable: bool = True, hint: str = "",
             **extra) -> dict:
    payload = dict(ok=False, error=dict(code=code, message=message, retryable=retryable,
                                        hint=hint))
    payload.update(extra)
    return payload


@dataclass
class AgentSession:
    """One authorised product task, driven by the model but bounded by deterministic code."""

    scope: object
    tasks: object
    max_turns: int = 16
    max_tool_calls: int = 24
    max_model_calls: int = 8

    def __post_init__(self):
        svc = self.scope.svc
        self.svc = svc
        self.project = self.scope.project
        self.summary = self.scope.summary
        self.text = self.scope.text
        self.ledger = self.scope.ledger
        self.facts = ProductSession(project=self.project, store=svc.reports,
                                    text=self.text, environ=svc.environ,
                                    summary=self.summary)
        self.tool_calls: list[dict] = []
        self.group_runs: dict[str, int] = {}
        self.group_notes: dict[str, str] = {}
        self.generation = 0
        self.seen: dict[str, dict] = {}
        self.stop = "RUNNING"
        self.error: dict | None = None
        self.started = time.monotonic()
        self.max_seconds = float(getattr(self.scope, "max_seconds", 0) or 0)
        self.answer = ""
        self.context_trace: list[dict] = []
        # Publish the runtime identity before the first action so a cancel/timeout still has
        # an auditable owner even when no tool ever ran.
        self.flush()

    # ------------------------------------------------------------------ audit + budget
    def record(self, name: str, *, phase: str = "done", **fields) -> None:
        row = dict(tool=name, phase=phase, at=round(time.monotonic() - self.started, 3),
                   **fields)
        self.tool_calls.append(row)
        self.flush()

    def flush(self) -> None:
        """Keep the runtime audit on the task summary so a crash cannot erase it."""
        self.summary["agent_runtime"] = self.public_state()

    def public_state(self) -> dict:
        return dict(engine=ENGINE, stop_reason=self.stop, error=self.error,
                    tool_calls=list(self.tool_calls),
                    group_runs=dict(self.group_runs),
                    attempts=list(self.scope.state.get("attempts") or ()),
                    elapsed_ms=round((time.monotonic() - self.started) * 1000, 3),
                    context_trace=list(self.context_trace),
                    answer=self.answer[:2000],
                    tools=list(TOOL_NAMES))

    def _budget_view(self) -> dict:
        ledger = self.ledger
        return dict(requests_left=max(0, ledger.max_requests - ledger.requests),
                    model_calls_left=max(0, ledger.max_model_calls - ledger.model_calls),
                    tool_calls_left=max(0, self.max_tool_calls - len(self.tool_calls)),
                    turns_left=max(0, self.max_turns - len(
                        [row for row in self.tool_calls if row["tool"] in
                         ("run_test_group", "run_open_obligation")])),
                    seconds_left=(max(0.0, self.max_seconds - (time.monotonic() - self.started))
                                  if self.max_seconds else None))

    def _stop(self, reason: str) -> None:
        if self.stop == "RUNNING":
            self.stop = reason

    def _operable(self) -> dict | None:
        """Deterministic gate before any business action; returns a refusal payload or None."""
        if self.stop != "RUNNING":
            return _refusal("RUNTIME_STOPPED", f"本轮已停止（{self.stop}），不再执行业务动作。",
                            retryable=False, hint="结束并总结已有证据。")
        if getattr(self.svc, "_shutting_down", False):
            self._stop(STOP_CANCELLED)
            return _refusal("TASK_CANCELLED", "操作者已取消本任务。", retryable=False,
                            hint="停止；保留已有证据，不重放请求。")
        try:
            self.scope.guard()
        except Exception:
            self._stop(STOP_AUTHORITY_CHANGED)
            return _refusal("AUTHORITY_CHANGED", "项目授权或版本在任务执行期间发生变化。",
                            retryable=False, hint="停止；重新检查授权后再提交新任务。")
        if self.ledger.requests >= self.ledger.max_requests:
            return _refusal("REQUEST_BUDGET_EXHAUSTED", "本轮 HTTP 请求预算已用尽。",
                            retryable=False, hint="停止并总结已获得的证据。")
        if self.ledger.model_calls >= self.ledger.max_model_calls:
            return _refusal("MODEL_BUDGET_EXHAUSTED", "本轮模型调用预算已用尽。",
                            retryable=False, hint="停止并总结已获得的证据。")
        if self.max_seconds and time.monotonic() - self.started > self.max_seconds:
            self._stop(STOP_DEADLINE)
            return _refusal("TASK_DEADLINE", "本轮已达到时间上限。", retryable=False,
                            hint="停止并总结已获得的证据。")
        return None

    # ------------------------------------------------------------------ deterministic views
    def _facts(self, *, remaining: dict | None = None) -> dict:
        attempts = list(self.scope.state.get("attempts") or ())
        mode = (self.summary.get("focus") or {}).get("mode")
        return supervision_facts(
            project=self.project, summary=self.summary, text=self.text,
            remaining=remaining or self._budget_view(),
            allow_negatives=(mode != "selected" or self._classification()["kind"] == PRE_RELEASE
                             or self.text == getattr(self.tasks, "DEFAULT_ROUND", None)),
            attempts=attempts,
            attempted=[item.get("target") for item in attempts if item.get("target")])

    def _classification(self) -> dict:
        return classify_task(self.text)

    def _subject(self) -> dict:
        return subject_scope(self.project, self.text, self._classification()["kind"])

    def _group_view(self) -> list[dict]:
        evidence = executed_evidence(self.summary)
        policies = {policy.id: policy for policy in self.project.registry.definitions()}
        rows = []
        for group in self.summary.get("groups") or ():
            group_id = group.get("id")
            members = list(group.get("primary") or ())
            related = [policy.id for policy in policies.values()
                       if policy.operation_id in members and policy.negative is None]
            measured = [policy_id for policy_id in related if policy_id in evidence]
            failed = [policy_id for policy_id in measured
                      if not evidence[policy_id]["passed"]]
            status = group.get("status") or "PENDING"
            runs = self.group_runs.get(group_id, 0)
            runnable = (bool(group.get("supported", True)) and runs < MAX_GROUP_RUNS
                        and status not in {"OUTSIDE_FOCUS"}
                        and not (related and len(measured) == len(related)))
            row = dict(group_id=group_id, operations=members,
                       policy_ids=related,
                       status=status, runs=runs,
                       reason=(group.get("reason") or "")[:200] or None,
                       measured_policy_ids=measured, failed_policy_ids=failed,
                       next_step=(dict(tool="run_test_group",
                                       arguments={"group_id": group_id})
                                  if runnable else None))
            if status in {"NOT_EXECUTED", "REJECTED"} and runs:
                row["retry_hint"] = ("上一次设计被拒绝；如要重试，请在 design_note 中写明一条"
                                     "新的、与既有证据不同的设计约束。")
            rows.append(row)
        return rows

    def scope_view(self) -> dict:
        """Deterministic coverage guidance: what is approved, open, blocked and worth doing."""
        facts = self._facts()
        classification = self._classification()
        subject = self._subject()
        groups = self._group_view()
        candidates = [dict({key: item[key] for key in ("id", "action", "kind",
                                                      "operations", "policies")}, next_step=dict(
            tool="run_open_obligation", arguments={"candidate_id": item["id"]}))
            for item in facts.get("candidates") or ()]
        inventory = (self.summary.get("inventory") or {}).get("operations") or []
        blocked = [dict(operation_id=item.get("operation_id"), state=item.get("state"),
                        reason=(item.get("reason") or "")[:200] or None,
                        next_step=(item.get("next_step") or "")[:200] or None)
                   for item in inventory if item.get("state") != "READY"]
        out_of_scope = [item.get("operation_id") for item in inventory
                        if item.get("state") == "READY"
                        and item.get("operation_id") not in {
                            operation for group in groups
                            for operation in group["operations"]}]
        return dict(user_task=self.text, task=dict(kind=classification["kind"],
                                                   reason=classification["reason"],
                                                   subject=subject),
                    groups=groups, candidates=candidates, blocked_operations=blocked[:10],
                    out_of_scope_operations=out_of_scope[:10],
                    satisfied=facts.get("satisfied_obligations") or [],
                    open_obligations=(facts.get("open_obligations") or [])[:10],
                    open_obligations_total=len(facts.get("open_obligations") or ()),
                    budget=self._budget_view(),
                    completion=self._completion_preview(facts),
                    guidance=[
                        "先执行一个 runnable 的 group（若存在），一次获得多条义务的证据。",
                        "随后用 get_test_coverage 检查 open_obligations；对剩余义务使用 "
                        "candidate。",
                        "返回 ok=false 时按 error.code/hint 调整，不要重复同一失败动作。",
                        "没有可执行动作或预算不足时结束；完成判据由确定性代码给出。"])

    def coverage_view(self) -> dict:
        facts = self._facts()
        base = self.facts.coverage_view()
        base.pop("cases", None)  # per-case detail belongs to read_test_report
        base.update(
            task=dict(kind=self._classification()["kind"],
                      reason=self._classification()["reason"]),
            satisfied=(facts.get("satisfied_obligations") or [])[:12],
            open_obligations=(facts.get("open_obligations") or [])[:12],
            open_obligations_total=len(facts.get("open_obligations") or ()),
            confirmed_anomalies=(facts.get("confirmed_anomalies") or [])[:6],
            unconfirmed_causes=(facts.get("unconfirmed_causes") or [])[:6],
            investigations=(facts.get("investigations") or [])[:6],
            resources=facts.get("resources"),
            candidates=[item["id"] for item in facts.get("candidates") or ()][:12],
            # A rejected design is the one fact a repair depends on. It is repeated here as a
            # bounded structured summary so trimming old tool output can never remove the code
            # and the missing dependency the model must act on.
            design_failures=[
                dict(group_id=row.get("group_id"), reason=row.get("reason"),
                     code=(row.get("diagnostic") or {}).get("code"),
                     fields=((row.get("diagnostic") or {}).get("fields") or [])[:4],
                     detail=(row.get("diagnostic") or {}).get("detail") or {})
                for row in (self.summary.get("design_records") or ())[-4:]
                if row.get("status") == "REJECTED"],
            evidence_refs=self.evidence_refs(),
            budget=self._budget_view(), completion=self._completion_preview(facts),
            duplicates=self.summary.get("duplicate_retests") or [])
        return base

    def _completion_preview(self, facts: dict) -> dict:
        report = evaluate(self.project, self.summary, self.text,
                          stop=dict(reason=self.stop if self.stop != "RUNNING"
                                    else "NO_FURTHER_ACTION"))
        # The unmet criteria and the one deterministic action that can address each of them are
        # part of the guidance, so "stop early" is a visible choice rather than an accident.
        candidates = {item["id"]: item for item in facts.get("candidates") or ()}
        next_steps = []
        for criterion in report["criteria"]:
            if criterion["status"] != "UNMET":
                continue
            evidence = criterion.get("evidence") or {}
            wanted = {evidence.get("policy_id")} | set(evidence.get("policies") or ())
            target = next((item_id for item_id, item in candidates.items()
                           if wanted & set(item.get("policies") or ())), None)
            next_steps.append(dict(criterion=criterion["id"], detail=criterion["detail"],
                                   candidate_id=target,
                                   action=(f"run_open_obligation({target})" if target
                                           else "run_test_group(下一个未设计分组)")))
        return dict(complete=report["complete"], unmet=report["unmet"],
                    next_steps=next_steps, flags=report["flags"],
                    coverage=report["coverage"])

    def run_ids(self) -> dict[str, dict]:
        rows = {}
        for case in self.summary.get("cases") or ():
            run_id = case.get("run_id")
            if run_id:
                rows[run_id] = dict(case_id=case.get("case_id"),
                                    operations=case.get("executed_operations") or [],
                                    outcome=case.get("outcome"))
        for probe in self.scope.state.get("probes") or ():
            if probe.get("run_id"):
                rows[probe["run_id"]] = dict(case_id=None,
                                             operations=[probe.get("operation_id")],
                                             outcome=probe.get("outcome"))
        return rows

    def evidence_refs(self) -> list[dict]:
        return [dict(run_id=key, outcome=row["outcome"])
                for key, row in list(self.run_ids().items())[-8:]]

    def record_context(self, payload: dict, paths: set[str]) -> None:
        """Measure the actual adapter input without retaining raw message contents."""
        messages = payload["messages"]
        system = "\n".join(row["content"] for row in messages if row["role"] == "system")
        calls = {call["id"]: call["function"] for row in messages
                 for call in row.get("tool_calls", ())}
        offered = sorted(path for path in paths if f"`{path}`" in system)
        tool_bytes: dict[str, int] = {}
        loaded = set()
        cleared = 0
        for row in messages:
            if row["role"] != "tool":
                continue
            call = calls.get(row["tool_call_id"], {})
            name = call.get("name", "unknown")
            content = row["content"]
            tool_bytes[name] = tool_bytes.get(name, 0) + len(content.encode("utf-8"))
            cleared += content == CLEARED_TOOL_RESULT
            if name == "read_file":
                path = json.loads(call["arguments"]).get("file_path")
                if path in paths and "description:" in content and "# " in content:
                    loaded.add(path)
        self.context_trace.append(dict(
            turn=len(self.context_trace) + 1, skills_discovered=offered,
            skills_in_context=sorted(loaded), tool_result_bytes=tool_bytes,
            cleared_tool_results=cleared,
            tool_names=[item["function"]["name"] for item in payload.get("tools", ())]))

    # ------------------------------------------------------------------ actions
    async def run_group(self, group_id: str, design_note: str = "") -> dict:
        """Design, preflight and execute one deterministic group; the only broad action."""
        refusal = self._operable()
        if refusal:
            return refusal
        group = next((item for item in self.summary.get("groups") or ()
                      if item.get("id") == group_id), None)
        if group is None:
            return _refusal("UNKNOWN_GROUP_ID", f"没有分组 {group_id!r}。", hint=
                            "group_id 只能来自 get_test_scope 的 groups[].group_id。",
                            valid_group_ids=[item.get("id") for item
                                             in self.summary.get("groups") or ()])
        if not group.get("supported", True):
            return _refusal("GROUP_UNSUPPORTED", "该分组依赖超过一次可处理的接口数量。",
                            retryable=False,
                            hint="用 run_open_obligation 逐项处理候选义务。")
        runs = self.group_runs.get(group_id, 0)
        if runs >= MAX_GROUP_RUNS:
            return _refusal("GROUP_RUN_LIMIT", f"分组 {group_id} 已执行 {runs} 次。", 
                            retryable=False, hint="改用候选义务或结束本轮。")
        evidence = executed_evidence(self.summary)
        policies = {policy.id: policy for policy in self.project.registry.definitions()}
        related = [policy.id for policy in policies.values()
                   if policy.operation_id in (group.get("primary") or ())
                   and policy.negative is None]
        if related and all(policy_id in evidence for policy_id in related) and not design_note:
            refusal_payload = _refusal(
                "GROUP_ALREADY_MEASURED", "该分组内所有已批准义务都已有执行证据。",
                hint="用 get_test_coverage 查看未测义务；通过候选动作补测独立检查。",
                unmeasured=[policy_id for policy_id in related if policy_id not in evidence])
            return refusal_payload
        note = self._note(design_note)
        if (runs and group.get("status") in {"NOT_EXECUTED", "REJECTED"}
                and (not note or note == self.group_notes.get(group_id))):
            return _refusal("REPAIR_NOTE_REQUIRED", "此分组已经拒绝过一次。",
                            hint="按上一轮结构化错误给出一条新的设计约束，或选择其他义务。")
        supervision = None
        if note or group.get("status") in {"NOT_EXECUTED", "REJECTED"}:
            round_number = self.tasks.next_supervision_round(self.scope)
            supervision = dict(round=round_number, target=f"agent:group:{group_id}",
                               text=self._design_text(group, note), preserve_focus=True,
                               design_note=note)
        before_cases = len(self.summary.get("cases") or ())
        before_records = len(self.summary.get("design_records") or ())
        before_requests = self.ledger.requests
        self.group_runs[group_id] = runs + 1
        self.group_notes[group_id] = note
        self.record("run_test_group", phase="start", group_id=group_id,
                    design_note=bool(note), status="STARTED")
        try:
            await self.tasks.run_group(self.scope, group, supervision=supervision)
        except Exception as exc:  # a defect is reported to the model, never fatal to the run
            self.record("run_test_group", phase="error", group_id=group_id,
                        error=type(exc).__name__)
            return _refusal("TOOL_ERROR", f"{type(exc).__name__}: {str(exc)[:200]}",
                            hint="该动作没有完成；读取覆盖后决定是否换一个候选。")
        self.generation += 1
        record = (self.summary.get("design_records") or [None])[-1] \
            if len(self.summary.get("design_records") or ()) > before_records else None
        cases = (self.summary.get("cases") or ())[before_cases:]
        self.record("run_test_group", group_id=group_id, status=group.get("status"),
                    cases=len(cases), requests=self.ledger.requests - before_requests)
        payload = dict(ok=True, group_id=group_id, status=group.get("status"),
                       operations=list(group.get("primary") or ()),
                       design=dict(status=(record or {}).get("status"),
                                   reason=(record or {}).get("reason"),
                                   code=self._diagnostic_code(record),
                                   fields=self._diagnostic(record).get("fields") or [],
                                   detail=self._diagnostic(record).get("detail") or {},
                                   planning_engine=(record or {}).get("actual_planning_engine")
                                   or (record or {}).get("planning_engine")),
                       cases=[self._case_summary(case) for case in cases][:8],
                       requests=self.ledger.requests - before_requests,
                       budget=self._budget_view(),
                       next_step=dict(tool="get_test_coverage", arguments={}))
        if group.get("status") == "NOT_EXECUTED":
            code = self._diagnostic_code(record) or "GROUP_NOT_DESIGNED"
            detail = self._diagnostic(record).get("detail") or {}
            payload["ok"] = False
            payload["error"] = dict(
                code=code,
                message="设计或预检没有通过，未发送业务请求。",
                fields=self._diagnostic(record).get("fields") or [],
                detail=detail,
                retryable=bool(note) is False,
                hint=detail.get("hint") or self._repair_hint(code))
        elif cases and all(case.get("status") == "REJECTED" for case in cases):
            group["status"] = payload["status"] = "REJECTED"
            payload["ok"] = False
            payload["error"] = dict(
                code=cases[0].get("reason") or "PLAN_REJECTED",
                message="方案未通过执行前校验，未发送业务请求。",
                retryable=runs == 0,
                hint="核对必填参数或绑定后提供新的设计约束，或选择其他可执行义务。")
        return payload

    @staticmethod
    def _diagnostic(record) -> dict:
        """The deterministic rejection diagnosis, bounded and free of request values."""
        value = (record or {}).get("diagnostic")
        return value if isinstance(value, dict) else {}

    def _diagnostic_code(self, record) -> str:
        """The compiler/precondition code, which is more actionable than MODEL_INVALID_RESPONSE."""
        diagnostic = self._diagnostic(record)
        return str(diagnostic.get("code") or (record or {}).get("reason") or "")

    @staticmethod
    def _repair_hint(code: str) -> str:
        return {
            "PLANNING_INSTANCE_COVERAGE":
                "这些产生者实例不在本场景中：要么补上它们的步骤，要么去掉消费它们的步骤。",
            "PLANNING_INSTANCE_UNBOUND":
                "有步骤找不到可绑定的规划实例；按 operation_id/policy_id 选择真实实例，"
                "不要为同一操作臆造额外步骤。",
            "PLANNING_INSTANCE_AMBIGUOUS":
                "同一操作有多个实例且无法唯一确定；用 planning 字段或不同 policy_id 区分。",
            "PLANNING_UNSOURCED_STEP":
                "requirement_source_id 必须来自本轮可执行请求子句。",
            "DESIGN_SELECTED_CLEANUP_MISSING":
                "该目标有已批准的清理删除；创建资源的场景必须计划这一步，否则会遗留资源。",
            "DESIGN_ABSENCE_PRECONDITION_MISSING":
                "404/410 断言必须在同一资源已批准删除之后；补上删除步骤或去掉该断言。",
            "MODEL_JSON_INVALID":
                "上一版方案不是合法 JSON：detail 给出了 position 和出错附近的 window，"
                "修正该处后重新提交，不要重复同一段输出。",
            "MODEL_OUTPUT_TRUNCATED":
                "上一版方案超过单次输出上限被截断：把这一组拆成更小的方案（更少的用例或"
                "更少的步骤）再调用一次。",
        }.get(code, "按 code 与 detail 调整设计方案；如无更具体的修复信息，请提供 design_note "
                    "说明一条新的设计约束。预检未通过时不会发送任何业务请求。")

    async def run_obligation(self, candidate_id: str, design_note: str = "") -> dict:
        """One obligation-level action, chosen from the server's candidate set."""
        refusal = self._operable()
        if refusal:
            return refusal
        candidates = {item["id"]: item for item in (self._facts().get("candidates") or ())}
        candidate = candidates.get(candidate_id)
        if candidate is None:
            return _refusal("UNKNOWN_CANDIDATE_ID", f"候选 {candidate_id!r} 当前不可执行。",
                            hint="candidate_id 只能来自 get_test_scope 的 candidates[].id；"
                                 "已满足或已尝试过的义务不会再出现。",
                            valid_candidate_ids=sorted(candidates)[:12])
        before_cases = len(self.summary.get("cases") or ())
        before_requests = self.ledger.requests
        self.record("run_open_obligation", phase="start", candidate_id=candidate_id,
                    action=candidate.get("action"), design_note=bool(design_note))
        note = self._note(design_note)
        try:
            if candidate.get("action") == "READ_PROBE":
                outcome = await self.tasks.supervise_read(self.scope, dict(candidate))
            else:
                scoped = dict(candidate)
                if note:
                    scoped["why"] = (str(candidate.get("why") or "")
                                     + " 本轮补充约束：" + note)[:400]
                outcome = await self.tasks.supervise_design(self.scope, scoped)
        except Exception as exc:
            self.record("run_open_obligation", phase="error", candidate_id=candidate_id,
                        error=type(exc).__name__)
            return _refusal("TOOL_ERROR", f"{type(exc).__name__}: {str(exc)[:200]}",
                            hint="该候选动作没有完成；读取覆盖后再决定。")
        cases = (self.summary.get("cases") or ())[before_cases:]
        self.generation += 1
        self.record("run_open_obligation", candidate_id=candidate_id,
                    executed=outcome.executed, requests=self.ledger.requests - before_requests)
        record = (self.summary.get("design_records") or [None])[-1]
        diagnostic = self._diagnostic(record) if record else {}
        code = diagnostic.get("code") or (outcome.note or "ACTION_NOT_EXECUTED")
        obtained_evidence = bool(outcome.executed and outcome.requests)
        return dict(ok=obtained_evidence, candidate_id=candidate_id,
                    action=candidate.get("action"), executed=outcome.executed,
                    cases=[self._case_summary(case) for case in cases][:6],
                    requests=self.ledger.requests - before_requests,
                    detail=outcome.detail, note=outcome.note,
                    error=None if obtained_evidence else dict(
                        code=code,
                        message="该候选动作没有产生执行证据。", retryable=False,
                        fields=diagnostic.get("fields") or [],
                        detail=diagnostic.get("detail") or {},
                        hint=(diagnostic.get("detail") or {}).get("hint")
                        or self._repair_hint(code)),
                    budget=self._budget_view(),
                    next_step=dict(tool="get_test_coverage", arguments={}))

    # ------------------------------------------------------------------ read-only evidence
    def read_view(self, name: str, build, *, refresh: bool = False) -> dict:
        """Return a pointer within a revision and allow explicit recovery after compaction."""
        static = name in {"get_contract", "get_approved_policies"}
        revision = 0 if static else self.generation
        previous = self.seen.get(name)
        if not refresh and previous is not None and previous["generation"] == revision:
            pointer = dict(ok=True, repeated=True, unchanged_since_previous_call=True,
                           **previous, note="复用上文。若旧结果已清理，传 refresh=true。",
                           next_step=dict(tool=name, arguments={"refresh": True}))
            if not static:
                pointer.update(budget=self._budget_view(), evidence_refs=self.evidence_refs())
            return pointer
        payload = build()
        self.seen[name] = dict(generation=revision, sha256=fingerprint(payload))
        return payload

    def report_view(self, run_id: str, *, step_offset: int = 0, step_limit: int | None = None,
                    operation_id: str | None = None) -> dict:
        known = self.run_ids()
        if run_id not in known:
            return _refusal("UNKNOWN_RUN_ID", f"没有本轮运行 {run_id!r} 的报告。",
                            hint="run_id 只能来自工具结果中的 cases[].run_id。",
                            available_run_ids=sorted(known)[:12])
        try:
            report = self.svc.reports.read(run_id)
        except (OSError, ValueError):
            # A case without a readable report is a real defect to report, never a success.
            return _refusal("REPORT_UNAVAILABLE", "该运行没有可读取的报告。", retryable=False,
                            hint="保留已有证据；报告缺失本身就是需要报告的异常。")
        steps = [step for step in report.steps
                 if operation_id is None or step.operation_id == operation_id]
        end = step_offset + step_limit if step_limit is not None else len(steps)
        next_step = (dict(tool="read_test_report", arguments=dict(
            run_id=run_id, step_offset=end, step_limit=step_limit, operation_id=operation_id))
            if end < len(steps) else None)
        return dict(ok=True, run_id=run_id, outcome=report.outcome.value,
                    completed=report.completed, cleanup=report.cleanup,
                    steps_total=len(steps), step_offset=step_offset, next_step=next_step,
                    resource_lifecycle=report.resource_lifecycle,
                    created_resources=report.created_resources,
                    failure_explanation=report.failure_explanation,
                    steps=[dict(step_id=step.step_id, operation_id=step.operation_id,
                                method=step.method, path=step.path_template,
                                outcome=step.outcome.value, reason=step.reason,
                                checks=[dict(kind=check.kind, selector=check.selector,
                                             expected=check.expected, actual=check.actual,
                                             passed=check.passed)
                                        for check in step.checks])
                           for step in steps[step_offset:end]])

    def investigate(self, run_id: str, operation_id: str | None = None) -> dict:
        view = self.report_view(run_id)
        if not view.get("ok"):
            return view
        failed = [step for step in view["steps"] if step["outcome"] != "PASS"]
        if not failed:
            return dict(ok=True, run_id=run_id, failed=False, read_only=True,
                        note="该报告没有失败步骤，无需排查。")
        chosen = [step for step in failed
                  if operation_id is None or step["operation_id"] == operation_id]
        if not chosen:
            return _refusal("NO_SUCH_FAILED_STEP", "该报告的失败步骤不包含指定接口。",
                            hint="operation_id 只能来自 read_test_report 的 steps。",
                            failed_operations=[step["operation_id"] for step in failed])
        step = chosen[0]
        from agenttest.product_probe import explain_failure

        report = self.svc.reports.read(run_id)
        return dict(ok=True, run_id=run_id, operation_id=step["operation_id"],
                    outcome=step["outcome"],
                    failing_checks=[check for check in step["checks"]
                                    if not check["passed"]],
                    conclusion=explain_failure(report), read_only=True,
                    note="只读复核：不重发写请求，不改写已确认的预期。")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _note(value: str) -> str:
        text = " ".join(str(value or "").split())
        return text[:300]

    def _design_text(self, group: dict, note: str) -> str:
        policies = {policy.id: policy for policy in self.project.registry.definitions()}
        related = sorted(policy.id for policy in policies.values()
                         if policy.operation_id in (group.get("primary") or ()))
        base = (f"[本轮由自主运行时指定一个已批准分组] 分组 {group.get('id')}；接口："
                + "、".join(group.get("primary") or ())
                + "；已批准策略：" + "、".join(related))
        return base + (("；补充设计约束：" + note) if note else "")

    @staticmethod
    def _case_summary(case: dict) -> dict:
        failed = [dict(operation_id=step.get("operation_id"), kind=check.get("kind"),
                       expected=check.get("expected"), actual=check.get("actual"))
                  for step in case.get("evidence") or () if step.get("executed")
                  for check in step.get("checks") or () if check.get("passed") is False]
        return dict(case_id=case.get("case_id"), status=case.get("status"),
                    outcome=case.get("outcome"), run_id=case.get("run_id"),
                    operations=case.get("executed_operations") or [],
                    requests=case.get("http_requests") or 0,
                    failed=failed[:6], reason=(case.get("reason") or "")[:200] or None,
                    duplicate_of=case.get("duplicate_of"),
                    absence_expectations=[dict(policy_id=row.get("policy_id"),
                                               satisfied=row.get("satisfied"))
                                          for row in case.get("absence_expectations") or ()][:4],
                    cleanup=case.get("cleanup"))


# ---------------------------------------------------------------------- tools + harness


def _absence_approvals(project) -> list[dict]:
    """Project the operator's ``404/410`` policies; no parallel approval format is added."""
    from agenttest.product_preconditions import absence_expectation

    operations = {operation.operation_id: operation for operation in project.contract.operations}
    policies = list(project.registry.definitions())
    rows = []
    for policy in policies:
        if not absence_expectation(policy):
            continue
        operation = operations.get(policy.operation_id)
        producers = {rule.source_policy_id for rule in policy.binding_rules
                     if rule.source_policy_id}
        establishing = [other.id for other in policies
                        if other.negative is None and other.id not in producers
                        and operations.get(other.operation_id) is not None
                        and operations[other.operation_id].method == "DELETE"
                        and {rule.source_policy_id for rule in other.binding_rules} & producers]
        rows.append(dict(
            policy_id=policy.id, operation_id=policy.operation_id,
            method=operation.method if operation else None,
            approved_statuses=sorted(policy.assertions.status.codes),
            bound_source_policies=sorted(producers),
            establishing_delete_policy_ids=establishing,
            admissible=bool(establishing),
            rule=("缺失断言只有在同一资源已按已批准删除策略删除后才成立；"
                  "预检会拒绝缺少该前置条件的计划。" if establishing else
                  "该缺失断言没有可绑定的产生者/删除者，预检将拒绝整条计划。"),
            source_note=(f"operator-approved absence of {policy.operation_id} "
                         f"after {', '.join(sorted(producers)) or 'no producer'}")))
    return rows


def build_tools(session: AgentSession) -> list:
    """The eight product tools; every argument is validated by the session again."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class RunGroupArgs(BaseModel):
        group_id: str = Field(description=(
            "分组 id，必须来自 get_test_scope 返回的 groups[].group_id。"))
        design_note: str = Field(default="", max_length=300, description=(
            "可选：仅在需要重新设计时提供一句新的设计约束（例如已经失败或缺失的前置条件），"
            "不得改变批准范围或断言。"))

    class RunObligationArgs(BaseModel):
        candidate_id: str = Field(description=(
            "候选 id，必须来自 get_test_scope 返回的 candidates[].id；"
            "已满足或已尝试过的候选不会出现。"))
        design_note: str = Field(default="", max_length=300, description=(
            "可选：补充一句新的设计约束，用于在失败后重新设计。"))

    class RunIdArgs(BaseModel):
        run_id: str = Field(description="运行 id，必须来自工具结果 cases[].run_id。")
        operation_id: str | None = Field(default=None, description="只查看此接口的步骤。")
        step_offset: int = Field(default=0, ge=0)
        step_limit: int = Field(default=3, ge=1, le=5)

    class InvestigateArgs(BaseModel):
        run_id: str = Field(description="运行 id，必须来自 read_test_report 或用例结果。")
        operation_id: str | None = Field(default=None, description=(
            "可选：只看该接口的失败步骤；必须来自报告 steps[].operation_id。"))

    def result(payload) -> str:
        return _json(payload)

    def view(name, build, refresh):
        payload = session.read_view(name, build, refresh=refresh)
        session.record(name, repeated=bool(payload.get("repeated")), refresh=refresh)
        return result(payload)

    async def contract(refresh: bool = False) -> str:
        return view("get_contract", session.facts.contract_view, refresh)

    async def policies(refresh: bool = False) -> str:
        def build():
            view = session.facts.policy_view()
            view["absence_approvals"] = _absence_approvals(session.project)
            return view

        return view("get_approved_policies", build, refresh)

    async def scope(refresh: bool = False) -> str:
        return view("get_test_scope", session.scope_view, refresh)

    async def coverage(refresh: bool = False) -> str:
        return view("get_test_coverage", session.coverage_view, refresh)

    async def group(group_id: str, design_note: str = "") -> str:
        return result(await session.run_group(group_id, design_note))

    async def obligation(candidate_id: str, design_note: str = "") -> str:
        return result(await session.run_obligation(candidate_id, design_note))

    async def report(run_id: str, operation_id: str | None = None, step_offset: int = 0,
                     step_limit: int = 3) -> str:
        session.record("read_test_report", run_id=run_id)
        return result(session.report_view(run_id, step_offset=step_offset, step_limit=step_limit,
                                          operation_id=operation_id))

    async def investigate(run_id: str, operation_id: str | None = None) -> str:
        session.record("investigate_failure", run_id=run_id, operation_id=operation_id)
        return result(session.investigate(run_id, operation_id))

    return [
        StructuredTool.from_function(coroutine=contract, name="get_contract",
            description="读取已批准的操作目录（方法、路径、是否需要身份）。"),
        StructuredTool.from_function(coroutine=policies, name="get_approved_policies",
            description=("读取操作员批准的断言、资源生命周期规则和缺失断言批准（404/410）。"
                         "这些是唯一允许的业务预期。")),
        StructuredTool.from_function(coroutine=scope, name="get_test_scope",
            description=("读取本轮任务类型、已批准义务、可执行分组、候选动作（含精确参数）与预算。"
                         "先调用它；group_id/candidate_id 只能来自这里。")),
        StructuredTool.from_function(coroutine=coverage, name="get_test_coverage",
            description=("读取已测量覆盖：满足/未满足义务、已确认异常、资源清理、剩余预算以及"
                         "确定性完成判据的当前状态。")),
        StructuredTool.from_function(coroutine=group, name="run_test_group",
            args_schema=RunGroupArgs,
            description=("执行一个分组：设计模型提出用例→确定性预检→仅经 Executor 发送业务请求，"
                         "创建的资源按已批准删除策略清理。返回用例、run_id 和失败证据。")),
        StructuredTool.from_function(coroutine=obligation, name="run_open_obligation",
            args_schema=RunObligationArgs,
            description=("执行一个仍未满足的候选义务（只读观测或一次受控补测）。"
                         "candidate_id 只能来自 get_test_scope。")),
        StructuredTool.from_function(coroutine=report, name="read_test_report",
            args_schema=RunIdArgs,
            description="读取一个真实运行的报告：步骤、断言、清理与失败解释。"),
        StructuredTool.from_function(coroutine=investigate, name="investigate_failure",
            args_schema=InvestigateArgs,
            description=("对失败步骤做只读复核与失败解释；不重发写请求、不改写预期。")),
    ]


def verify_catalogue(agent, expected=(*TOOL_NAMES, "read_file")) -> dict:
    """Check actual dispatcher registration as well as the separate model-view probe."""
    names = catalogue_of(agent)
    missing = sorted(set(expected) - set(names))
    unexpected = sorted(set(names) - set(expected))
    if not names or missing or unexpected:
        raise AgentRuntimeUnavailable("TOOL_CATALOGUE_NOT_NARROWED:" + json.dumps(
            dict(missing=missing, unexpected=unexpected), ensure_ascii=False))
    return dict(bound_tools=names, expected=list(expected), missing=missing,
                unexpected=unexpected, executor_only=[])


@dataclass
class RuntimeResult:
    session: AgentSession
    stop_reason: str
    model_totals: dict
    call_trace: list
    catalogue: dict
    enforcement: dict
    transcript: list
    answer: str
    error: dict | None
    elapsed_ms: float


async def run_agent(scope, tasks, settings, *, max_turns: int = 16,
                    max_tool_calls: int = 24, max_model_calls: int = 8,
                    transport=None, model=None, enable_skills: bool = True) -> RuntimeResult:
    """Run one product goal through Deep Agents; deterministic code still owns every verdict.

    ``model`` exists for offline tests and for a deployment that wants to hand in its own
    configured adapter; the default is the same ``EcnuxChatModel`` every other model call uses.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langchain.agents.middleware import (
        ClearToolUsesEdit,
        ContextEditingMiddleware,
        ModelCallLimitMiddleware,
        ModelRetryMiddleware,
        ToolCallLimitMiddleware,
    )
    from langchain_core.messages import HumanMessage
    from langgraph.errors import GraphRecursionError

    from agenttest.deep_harness.chat_model import (
        MODEL_CALL_BUDGET,
        MODEL_TOKEN_BUDGET,
        MODEL_USAGE_UNKNOWN,
        EcnuxChatModel,
        ModelBudgetLimits,
        ModelHarnessError,
    )

    started = time.monotonic()
    session = AgentSession(scope=scope, tasks=tasks, max_turns=max_turns,
                           max_tool_calls=max_tool_calls, max_model_calls=max_model_calls)
    limits = ModelBudgetLimits(max_calls=max_model_calls,
                               max_total_tokens=int(getattr(settings, "max_total_tokens", 0)
                                                    or 0) or None)
    if model is None:
        model = EcnuxChatModel(settings=settings, limits=limits, transport=transport)

    def charge(stats) -> None:
        """One ledger for designer and harness calls; unknown usage stays unknown."""
        ledger = scope.ledger
        ledger.model_calls += stats.calls
        if stats.calls:
            if stats.total_tokens is None and not getattr(stats, "refused", False):
                ledger.usage_unknown = True
            elif stats.total_tokens is not None:
                ledger.known_tokens += stats.total_tokens

    def check_model_budget() -> None:
        ledger = scope.ledger
        if ledger.usage_unknown:
            raise ModelHarnessError(MODEL_USAGE_UNKNOWN)
        if ledger.model_calls >= ledger.max_model_calls:
            raise ModelHarnessError(MODEL_CALL_BUDGET)
        if ledger.known_tokens >= ledger.max_total_tokens:
            raise ModelHarnessError(MODEL_TOKEN_BUDGET)

    model.on_call = charge
    model.before_call = check_model_budget
    files = skill_files() if enable_skills else {}
    model.on_payload = lambda payload: session.record_context(payload, set(files))

    def build_agent(agent_model, tools, middleware=()):
        backend = StateBackend()
        return create_deep_agent(
            model=agent_model, tools=tools, backend=backend,
            skills=[SKILL_ROOT] if files else None, system_prompt=SYSTEM_PROMPT,
            middleware=[skill_filesystem(backend, files), *middleware])

    install_narrow_profile(model)
    enforcement = await verify_enforcement(model, build_agent=build_agent, files=files)
    tools = build_tools(session)
    middleware = [
        # A busy gateway (concurrency limit, timeout, 5xx) is a transport fact, not a decision:
        # one bounded retry keeps an otherwise healthy run alive. Budget and input rejections are
        # deterministic, so they are never retried and keep their stop reason.
        ModelRetryMiddleware(max_retries=2, retry_on=_transient_model_error,
                             on_failure="error", initial_delay=1.0, max_delay=8.0),
        ModelCallLimitMiddleware(run_limit=max_model_calls, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="end"),
        ToolCallLimitMiddleware(tool_name="run_test_group", run_limit=4,
                                exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="run_open_obligation", run_limit=8,
                                exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="get_test_scope", run_limit=4,
                                exit_behavior="continue"),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(trigger=CONTEXT_CLEAR_TRIGGER_TOKENS, keep=4,
                              clear_tool_inputs=True, exclude_tools=["read_file"],
                              placeholder=CLEARED_TOOL_RESULT)]),
    ]
    agent = build_agent(model, tools, middleware)
    catalogue = verify_catalogue(agent)
    stop_reason, error, messages = STOP_ANSWER, None, []
    try:
        # ``recursion_limit`` counts graph node steps, not agent turns: every harness
        # middleware is a node, so a small multiple of the turn budget ends a healthy run
        # early. The product budgets are enforced by the model/tool-call middleware and the
        # session gate; this is only the framework's hard stop.
        async for outcome in agent.astream(
                {"messages": [HumanMessage(content=scope.text)], "files": files},
                config={"recursion_limit": max(64, 40 * max_turns)}, stream_mode="values"):
            messages = outcome.get("messages") or []
    except (ModelHarnessError, GraphRecursionError) as exc:
        if isinstance(exc, GraphRecursionError):
            stop_reason = STOP_TURN_LIMIT
        elif exc.code in {"MODEL_TOKEN_BUDGET", "MODEL_CALL_BUDGET"}:
            # The envelope refused the next request before it was sent. That is a spent budget,
            # not a broken model, and the deterministic report is still produced.
            stop_reason = STOP_MODEL_CALL_LIMIT
            error = dict(code=exc.code, message=str(exc))
        else:
            stop_reason, error = STOP_MODEL_ERROR, dict(code=str(exc))
    except Exception as exc:  # a framework failure is reported, never turned into success
        stop_reason, error = STOP_RUNTIME_ERROR, dict(code=type(exc).__name__,
                                                      message=str(exc)[:300])
    if stop_reason == STOP_ANSWER:
        if model.totals.calls >= max_model_calls:
            stop_reason = STOP_MODEL_CALL_LIMIT
        elif any(msg.type == "tool" and msg.status == "error"
                 and msg.content == "Tool call limit exceeded. Do not make additional tool calls."
                 for msg in messages):
            stop_reason = STOP_TOOL_CALL_LIMIT
    if session.stop != "RUNNING" and stop_reason == STOP_ANSWER:
        stop_reason = session.stop
    session.stop = stop_reason
    session.error = error
    session.answer = _final_answer(messages)[:2000]
    transcript = _turns(messages)
    session.flush()
    return RuntimeResult(session=session, stop_reason=stop_reason,
                         model_totals=model.totals.public(), call_trace=list(model.call_trace),
                         catalogue=catalogue, enforcement=enforcement,
                         transcript=transcript, answer=session.answer, error=error,
                         elapsed_ms=round((time.monotonic() - started) * 1000, 3))


def report_payload(result: RuntimeResult, *, completion: dict, budget: dict) -> dict:
    """The deterministic, structured runtime report stored on the task summary."""
    session = result.session
    return dict(
        schema="agenttest.agent-runtime.v1", engine=ENGINE,
        stop_reason=result.stop_reason, error=result.error,
        elapsed_ms=result.elapsed_ms,
        model=result.model_totals, call_trace=result.call_trace,
        context_trace=list(session.context_trace),
        budget=budget, tool_calls=list(session.tool_calls),
        catalogue=result.catalogue, enforcement=result.enforcement,
        transcript=result.transcript, answer=result.answer,
        tool_names=list(TOOL_NAMES),
        task_completion=public_view(completion))


__all__ = [
    "AgentRuntimeUnavailable",
    "AgentSession",
    "AUTO",
    "DETERMINISTIC",
    "ENGINE",
    "LEGACY",
    "RuntimeResult",
    "TOOL_NAMES",
    "build_tools",
    "report_payload",
    "resolve_engine",
    "run_agent",
    "verify_catalogue",
]
