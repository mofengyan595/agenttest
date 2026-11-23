"""Deterministic supervision of the approved-obligation ledger (P3-B2.2).

Two decisions live here, and neither one reads model prose:

* **termination** -- may this run end? A closing answer is accepted only when every approved
  obligation carries a measured receipt, or when a concrete blocker (budget, authority, model
  service, an unrepairable design) makes further dispatch impossible. Five states stay separate:
  obligation executed, assertions passed, cleanup proven, strict task complete, and the blocked
  early termination.
* **bounded repair** -- when an attempt of one batch failed *before any business HTTP* with a
  formal defect (invalid JSON, schema shape, an unconstructible step reference), the runtime
  authors the next constraint itself from the structured diagnostic. Only the unconstructed part
  is repaired; approved policies, expectations, cleanup and scope never move, and no resource is
  created twice.

The module imports no agent framework: it is usable (and tested) without Deep Agents, and it
never executes HTTP. Progress is judged by newly measured obligations, never by new text.
"""

from __future__ import annotations

import re

# Formal defects are repaired by *re-sending the same approved batch*, never by dropping it or
# by reinterpreting what the batch must measure. Semantic rejections (scope, expectation, an
# unapproved value) are not in this set: those need an operator decision, not a retry.
FORMAL_DESIGN_CODES = frozenset({
    "JSON_SYNTAX", "JSON_SIZE", "JSON_DUPLICATE_KEY", "JSON_STRUCTURE_LIMIT",
    "MODEL_OUTPUT_TRUNCATED", "DESIGN_OMISSION_SHAPE_INVALID", "DESIGN_QUESTION_SHAPE_INVALID",
    "CONSTRUCTION_REFERENCE_INVALID", "CONSTRUCTION_DUPLICATE_NODE",
    "CONSTRUCTION_RESERVED_NODE_ID",
})

# Schema-shape repairs are formal too, but only the shape family: a model that invented a field
# has to be told which field, while a model that disagreed with an approved value must not be
# silently retried.
FORMAL_SCHEMA_PREFIX = ("MODEL_SCHEMA_", "GENERIC_", "COMPOSED_")

MAX_CONTINUATIONS = 6       # closing answers the gate may refuse for one run
MAX_STALLED = 2             # consecutive refusals without one newly measured obligation
MAX_PENDING_ROWS = 12
MAX_DECISIONS = 12
MAX_REPAIR_CHARS = 900      # the whole constraint travels as one design note
MAX_STEP_IDS = 12
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")

# The model loop ended on its own or on a bounded rule: the gate's own verdict is the reason.
LEGITIMATE_TERMINALS = frozenset({"ANSWER", "MODEL_CALL_LIMIT", "TURN_LIMIT", "TOOL_CALL_LIMIT",
                                  "MODEL_TOKEN_LIMIT", "NO_FURTHER_ACTION", "NO_PROGRESS",
                                  "DEADLINE", "CLEANUP_RESERVE_REACHED"})
# Persistent model-service failure: the run must say so instead of looking like a partial pass.
MODEL_FAILURE_STOPS = frozenset({"MODEL_ERROR", "GATEWAY_STOP", "MODEL_SERVICE_ERROR",
                                 "DURABLE_MODEL_GATEWAY_UNAVAILABLE"})


def formal_code(code: str) -> bool:
    """Whether this rejection is a formal defect the runtime may repair by itself."""
    text = str(code or "")
    return text in FORMAL_DESIGN_CODES or text.startswith(FORMAL_SCHEMA_PREFIX)


# ---------------------------------------------------------------------- obligation state
def obligation_state(project, summary) -> dict:
    """Approved obligations split by measured receipt, not by passed assertion.

    ``approved_scope`` is the same denominator the deterministic coverage ledger uses, and
    ``executed_evidence`` only records attempts that really ran. An obligation whose assertion
    failed is *measured* -- the negative evidence is the result -- while an obligation no
    request ever touched stays pending.
    """
    from agenttest.product_obligations import approved_scope, executed_evidence

    scope = approved_scope(project, summary)
    evidence = executed_evidence(summary)
    measured, pending = [], []
    for item in scope:
        row = dict(policy_id=item["policy_id"], operation_id=item["operation_id"],
                   kind=item["kind"])
        entry = evidence.get(item["policy_id"])
        if entry is None:
            pending.append(dict(row, state="NOT_EXECUTED"))
        else:
            measured.append(dict(row, observed=entry["observed"], passed=entry["passed"]))
    failed = [row for row in measured if row["passed"] is False]
    return dict(approved=len(scope), measured=measured, pending=pending[:MAX_PENDING_ROWS],
                pending_total=len(pending), failed=failed[:MAX_PENDING_ROWS])


def cleanup_state(project, summary) -> dict:
    """Created resources either proven cleaned (or never created) or still open."""
    from agenttest.write_effects import analyse

    policies = {policy.id: policy for policy in project.registry.definitions()}
    effects = summary.get("write_effects")
    if not isinstance(effects, dict) or "unresolved" not in effects:
        effects = analyse(summary.get("cases") or (), policies)
    unresolved = list(effects.get("unresolved") or ())
    pending = list(effects.get("pending") or ())
    return dict(requests=effects.get("requests") or 0, counts=effects.get("counts") or {},
                unresolved=[dict(step_id=row.get("step_id"),
                                 policy_id=row.get("policy_id"), status=row.get("status"))
                            for row in unresolved[:MAX_PENDING_ROWS]],
                pending=[dict(step_id=row.get("step_id"), status=row.get("status"))
                         for row in pending[:MAX_PENDING_ROWS]],
                proven=not unresolved and not pending)


# ---------------------------------------------------------------------- dispatch planning
def dispatch_plan(project, summary, runs=None, attempt_limit=2, *, group_blocker=None) -> dict:
    """Which approved group can still measure a pending obligation, in a fixed order.

    Batches own their negative checks (only the owning batch may dispatch one), so a pending
    negative whose batch is exhausted is reported as blocked instead of being re-mapped onto a
    different group or a new resource instance. Order is deterministic: least attempts first,
    then the most pending policies, then the group id.

    Agent callers supply their shared group blocker, which also sees the durable repair counters
    and latest design evidence. Without an Agent session only the numeric limit is available.
    """
    attempted = {str(key): int(value) for key, value in (runs or {}).items()}
    limit = int(attempt_limit)
    from agenttest.product_obligations import (
        approved_scope,
        executed_evidence,
        negative_dispatch_floor,
    )

    policies = {policy.id: policy for policy in project.registry.definitions()}
    operations = {operation.operation_id: operation
                  for operation in project.contract.operations}
    inventory = {row.get("operation_id"): row
                 for row in (summary.get("inventory") or {}).get("operations") or ()}
    evidence = executed_evidence(summary)
    scope = {item["policy_id"]: item for item in approved_scope(project, summary)}
    rows = []
    batched = {key for group in summary.get("groups") or ()
               for key in group.get("policy_ids") or ()}
    for group in summary.get("groups") or ():
        owned = list(group.get("policy_ids") or ())
        if owned:
            related = [key for key in owned if key in scope]
        else:
            # Only the owning batch may dispatch one of its negative checks. An ordinary group
            # never picks a negative up -- not even when the owning batch is missing, which is a
            # blocker to report rather than a reason to re-map the check onto another group.
            related = [key for key in scope if key not in batched
                       and scope[key]["kind"] != "APPROVED_NEGATIVE"
                       and scope[key]["operation_id"] in (group.get("primary") or ())]
        pending = [key for key in related if key not in evidence]
        if not pending:
            continue
        floor = (negative_dispatch_floor(project.target, policies, pending) if owned
                 else max(1, len(group.get("primary") or ())))
        operations_of = sorted({scope[key]["operation_id"] for key in pending
                                if key in scope})
        not_ready = [key for key in operations_of
                     if (inventory.get(key) or {}).get("state") != "READY"]
        runs = attempted.get(str(group.get("id")), 0)
        blocker = group_blocker(group) if group_blocker else None
        rows.append(dict(
            group_id=group.get("id"), batch=bool(owned),
            policies=pending[:MAX_PENDING_ROWS], pending=len(pending),
            operations=operations_of, minimum_requests=floor,
            runs=runs, attempts_left=max(0, limit - runs),
            design_blocker=blocker,
            status=group.get("status"), supported=bool(group.get("supported", True)),
            methods=sorted({(operations[scope[key]["operation_id"]].method
                             if scope[key]["operation_id"] in operations else "?")
                            for key in pending if key in scope}),
            authority_ready=not not_ready, not_ready=not_ready))
    # Ordinary groups first: their run is what produces the approved resource a negative batch
    # needs. Then least attempted, most pending, and finally a stable id order.
    rows.sort(key=lambda row: (row["runs"], 1 if row["batch"] else 0, -row["pending"],
                               str(row["group_id"])))
    dispatchable = [row for row in rows if row["supported"] and row["authority_ready"]
                    and row["attempts_left"] > 0 and not row["design_blocker"]]
    count_blockers = {"GROUP_RUN_LIMIT", "REPAIR_NOTE_REQUIRED"}
    blocked = [row for row in rows if not (row["supported"] and row["authority_ready"])
               or row["design_blocker"] and row["design_blocker"] not in count_blockers]
    exhausted = [row for row in rows if row["supported"] and row["authority_ready"]
                 and (row["attempts_left"] <= 0 or row["design_blocker"] in count_blockers)]
    return dict(groups=rows[:MAX_PENDING_ROWS],
                dispatchable=dispatchable[:MAX_PENDING_ROWS],
                blocked=blocked[:MAX_PENDING_ROWS],
                exhausted=exhausted[:MAX_PENDING_ROWS])


def affordable(row, budget) -> tuple[bool, str]:
    """Whether one dispatch of this group fits the remaining authorization and budget."""
    if row is None:
        return False, "OBLIGATION_BUDGET_INSUFFICIENT"
    if int(budget.get("requests_left") or 0) < int(row.get("minimum_requests") or 1):
        return False, "OBLIGATION_REQUEST_BUDGET"
    if int(budget.get("model_calls_left") or 0) < 2:
        # one design call plus the turn that reads its result
        return False, "OBLIGATION_MODEL_CALL_BUDGET"
    if budget.get("tokens_left") == 0:
        return False, "OBLIGATION_MODEL_TOKEN_BUDGET"
    if int(budget.get("tool_calls_left") or 0) < 2:
        return False, "OBLIGATION_TOOL_CALL_BUDGET"
    seconds = budget.get("seconds_left")
    if seconds is not None and float(seconds) <= 0:
        return False, "OBLIGATION_DEADLINE"
    return True, "AFFORDABLE"


# ---------------------------------------------------------------------- the gate itself
TERMINAL_STATES = ("COMPLETE", "PARTIAL", "BLOCKED")


def decide(*, state: dict, plan: dict, cleanup: dict, budget: dict, stop: str = "RUNNING",
           execution_stopped: str | None = None, continuations: int = 0, stalled: int = 0,
           measured: int = 0, measured_before: int = 0,
           max_continuations: int = MAX_CONTINUATIONS,
           max_stalled: int = MAX_STALLED) -> dict:
    """One deterministic verdict: refuse the closing answer, or accept the run as ended.

    Every branch names a legitimate terminal, so the runtime can neither fake completion nor
    loop forever: an obligation that cannot be dispatched for lack of money, authority or a
    legal construction ends the run as ``BLOCKED``, and a refusal that produced no newly
    measured obligation twice ends it as ``PARTIAL``.
    """
    progress = measured - measured_before
    base = dict(schema="agenttest.obligation-gate.v1", obligations=dict(
        approved=state["approved"], measured=len(state["measured"]),
        pending=state["pending"], pending_total=state["pending_total"]))
    if stop != "RUNNING":
        return dict(base, action="END", state="BLOCKED", code="AUTHORITY_CHANGED",
                    detail="运行时已因授权或状态变化停止；不再派发新的义务。")
    if execution_stopped:
        bounded = str(execution_stopped) in LEGITIMATE_TERMINALS
        return dict(base, action="END", state="PARTIAL" if bounded else "BLOCKED",
                    code=str(execution_stopped),
                    detail="执行预算已按确定性规则停止；不再派发新的义务。")
    if state["pending_total"] == 0:
        if cleanup["proven"]:
            return dict(base, action="END", state="COMPLETE", code="OBLIGATIONS_COMPLETE",
                        detail="全部已批准义务都有执行证据，且已创建资源都有清理结论。")
        return dict(base, action="END", state="BLOCKED", code="WRITE_OUTCOME_REQUIRES_REVIEW",
                    detail="义务已测量，但仍有写入结果需要复核；这不能算任务完成。")
    if not plan["dispatchable"]:
        if plan.get("exhausted"):
            return dict(base, action="END", state="PARTIAL",
                        code="OBLIGATION_ATTEMPTS_EXHAUSTED",
                        detail="剩余义务所属分组没有可用的设计尝试；保留原批准义务与证据，"
                               "不再重复同一批次。", exhausted=plan["exhausted"])
        return dict(base, action="END", state="BLOCKED",
                    code="OBLIGATION_AUTHORITY_LIMITED",
                    detail="剩余义务所属分组当前不可派发（授权、前置数据或分组依赖不足）。",
                    blocked=plan["blocked"])
    if stalled >= max_stalled:
        return dict(base, action="END", state="PARTIAL", code="OBLIGATION_STALLED",
                    detail=f"连续 {stalled} 次结束请求都没有新增已测量义务，按有界规则停止。")
    if continuations >= max_continuations:
        return dict(base, action="END", state="PARTIAL",
                    code="OBLIGATION_CONTINUATION_LIMIT",
                    detail=f"已拒绝 {continuations} 次结束请求，仍未测量完剩余义务。")
    row = plan["dispatchable"][0]
    fits, reason = affordable(row, budget)
    if not fits:
        return dict(base, action="END", state="BLOCKED", code=reason,
                    detail="剩余授权或预算不足以再派发一个义务分组。", next=row)
    return dict(base, action="CONTINUE", state="PARTIAL", code="OBLIGATION_PENDING",
                detail=(f"还有 {state['pending_total']} 项已批准义务没有执行证据；"
                        "结束请求被拒绝，继续派发下一个分组。"),
                next=row, progress=progress)


# ---------------------------------------------------------------------- repair constraint
def _expectation_text(policy) -> str:
    from agenttest.product_obligations import policy_expectation

    expected = policy_expectation(policy)
    if policy.negative is not None:
        return "负向策略（必须发送真实的非法请求）"
    if expected.get("kind") == "status":
        return "期望状态码 " + "/".join(str(code) for code in expected.get("status") or ())
    if expected.get("kind") == "fields":
        return f"{len(expected.get('fields') or ())} 个字段断言"
    return "无状态码断言"


def _diagnostic_text(diagnostic) -> str:
    """The structured part of a rejection, in a form a repair can act on."""
    row = diagnostic if isinstance(diagnostic, dict) else {}
    code = str(row.get("code") or "UNKNOWN")
    parts = [f"code={code}"]
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    if detail.get("position") is not None:
        parts.append(f"位置=第{detail.get('line')}行第{detail.get('column')}列"
                     f"（第{detail.get('position')}个字符）")
    if detail.get("reason"):
        parts.append(f"原因={detail['reason']}")
    structure = detail.get("expected_structure")
    if isinstance(structure, dict) and structure.get("kind"):
        shape = f"结构={structure.get('kind')}/{structure.get('expected')}"
        if structure.get("after_key"):
            shape += f"（缺少冒号的键={structure['after_key']}）"
        if structure.get("at_end"):
            shape += "（输入在该位置结束）"
        parts.append(shape)
    if detail.get("repair"):
        parts.append("改法=" + str(detail["repair"]).rstrip("。 "))
    if detail.get("unknown_fields"):
        parts.append("多余字段=" + "、".join(str(item) for item in
                                          detail["unknown_fields"][:4]))
    if "allowed_from_steps" in detail:
        sources = (detail["allowed_from_steps"] or [])[:MAX_STEP_IDS]
        parts.append("可用 from_step=" + ("、".join(str(item) for item in sources) if sources
                                        else "无，需提交符合已批准绑定的前置步骤"))
    if detail.get("reference"):
        parts.append(f"引用问题={detail['reference']}")
    fields = [str(item) for item in row.get("fields") or ()]
    if fields:
        parts.append("字段=" + "、".join(fields[:4]))
    # This text is model input. Producers record enum words and paths, but a value or a header
    # pasted into a free-text slot must not be re-sent, so the assembled line is scrubbed too.
    from agenttest.model_input import redact

    return redact("；".join(parts))[:600]


def repair_constraint(project, group, pending, diagnostic, *, attempt: int) -> str:
    """The runtime's own repair note for the next attempt of the same approved batch.

    It is deliberately boring: which policies are still missing, what the group really sends,
    what went wrong formally, and which edits are allowed. The model's earlier free-text note is
    not repeated, because that is where a read-only reinterpretation of an approved write policy
    came from.
    """
    policies = {policy.id: policy for policy in project.registry.definitions()}
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    rows = []
    for key in list(pending)[:MAX_PENDING_ROWS]:
        policy = policies.get(key)
        if policy is None:
            continue
        operation = operations.get(policy.operation_id)
        method = operation.method if operation is not None else "?"
        rows.append(f"{key}（{method} {policy.operation_id}，{_expectation_text(policy)}）")
    where = group.get("id") or "?"
    text = (f"[运行时修复约束 group={where} attempt={attempt}] 上一版方案在构造阶段被拒绝："
            f"{_diagnostic_text(diagnostic)}。本批次仍需测量的已批准策略："
            + "、".join(rows)
            + "。只修正形式问题：输出必须是单个合法 JSON 对象；from_step 只能引用本方案中"
            "已声明的步骤；保留上面全部策略及其已批准预期；不得新增、改名、只读化或删减策略；"
            "创建资源的用例仍须按已批准删除策略清理。")
    return text[:MAX_REPAIR_CHARS]


def continuation_text(project, row, pending, budget, *, diagnostic=None) -> str:
    """The instruction the gate sends back instead of accepting the closing answer."""
    policies = {policy.id: policy for policy in project.registry.definitions()}
    operations = {operation.operation_id: operation for operation in project.contract.operations}
    listed = []
    for key in list(pending)[:6]:
        policy = policies.get(key)
        if policy is None:
            continue
        operation = operations.get(policy.operation_id)
        listed.append(f"{key}（{(operation.method if operation else '?')} "
                      f"{policy.operation_id}）")
    group_id = row.get("group_id")
    note = (repair_constraint(project, {"id": group_id}, pending, diagnostic, attempt=2)
            if diagnostic else "")
    text = ("[运行时义务门禁] 你的结束请求被拒绝：下面这些已批准义务还没有执行证据："
            + "、".join(listed)
            + f"。现在直接调用 run_test_group，group_id=\"{group_id}\""
            + (", design_note=" + note if note else "")
            + "。不要结束、不要换成只读检查、不要新增或改动预期。剩余预算："
            + f"业务请求{int(budget.get('requests_left') or 0)}次，"
            + f"模型调用{int(budget.get('model_calls_left') or 0)}次，"
            + f"工具调用{int(budget.get('tool_calls_left') or 0)}次。")
    return text


# ---------------------------------------------------------------------- final account
def termination(project, summary, *, stop_reason: str, decisions=()) -> dict:
    """The run's own account: obligations, assertions, cleanup, strict completion, blocker."""
    from agenttest.product_obligations import anomalies

    state = obligation_state(project, summary)
    cleanup = cleanup_state(project, summary)
    cases = list(summary.get("cases") or ())
    executed = [case for case in cases
                if case.get("status") == "FINISHED" and (case.get("http_requests") or 0) > 0]
    confirmed, unproven = anomalies(summary)
    assertions = dict(
        executed_cases=len(executed), passed=sum(case.get("outcome") == "PASS"
                                                 for case in executed),
        failed=len(confirmed), undecided=len(unproven), cases=len(cases))
    last = decisions[-1] if decisions else {}
    remaining = [row["policy_id"] for row in state["pending"]]
    legitimate = stop_reason in LEGITIMATE_TERMINALS
    # Completion is an evidence question: the obligation ledger is either measured or it is not.
    # The loop's own end reason stays in ``stop_reason`` so a crash after the last measurement is
    # still visible without being turned into a fake "not complete".
    if state["pending_total"] == 0 and cleanup["proven"]:
        state_name, code, detail = "COMPLETE", "OBLIGATIONS_COMPLETE", ""
    elif state["pending_total"] == 0:
        state_name, code = "BLOCKED", "WRITE_OUTCOME_REQUIRES_REVIEW"
        detail = "义务已测量，但仍有写入结果需要复核。"
    elif stop_reason in MODEL_FAILURE_STOPS:
        state_name, code = "BLOCKED", "MODEL_SERVICE_UNAVAILABLE"
        detail = "模型服务持续不可用；已保留已发生的证据，剩余义务没有派发。"
    elif last.get("state") in TERMINAL_STATES and last.get("action") == "END":
        state_name, code = last["state"], last["code"]
        detail = last.get("detail") or ""
    elif not legitimate:
        state_name, code = "BLOCKED", f"STOPPED_{stop_reason}"
        detail = "运行时被外部条件终止；剩余义务没有派发，也没有被伪装成完成。"
    else:
        state_name, code = "PARTIAL", "OBLIGATION_NOT_DISPATCHED"
        detail = f"还有 {state['pending_total']} 项已批准义务没有执行证据。"
    return dict(
        schema="agenttest.obligation-termination.v1", state=state_name, code=code,
        detail=detail, stop_reason=stop_reason,
        obligations=dict(approved=state["approved"], measured=len(state["measured"]),
                         pending=state["pending"], pending_total=state["pending_total"],
                         failed=[row["policy_id"] for row in state["failed"]]),
        assertions=assertions, cleanup=cleanup,
        strict_complete=bool(state_name == "COMPLETE" and assertions["cases"]
                             and not confirmed and cleanup["proven"]),
        blocked=state_name == "BLOCKED", remaining=remaining,
        decisions=list(decisions)[-MAX_DECISIONS:])


__all__ = [
    "FORMAL_DESIGN_CODES",
    "MAX_CONTINUATIONS",
    "MAX_STALLED",
    "ObligationGate",
    "affordable",
    "cleanup_state",
    "continuation_text",
    "decide",
    "dispatch_plan",
    "formal_code",
    "obligation_state",
    "repair_constraint",
    "termination",
]


class ObligationGate:
    """Per-run supervision counters; they survive a durable resume.

    The counters are the bound the milestone asks for: a refusal must be followed by a newly
    measured obligation, and two refusals in a row end the run instead of looping. Nothing here
    grows a budget or restarts the plan.
    """

    def __init__(self, project, summary, *, max_continuations: int = MAX_CONTINUATIONS,
                 max_stalled: int = MAX_STALLED):
        self.project = project
        self.summary = summary
        self.max_continuations = int(max_continuations)
        self.max_stalled = int(max_stalled)
        self.continuations = 0
        self.stalled = 0
        self.measured = 0
        self.decisions: list[dict] = []
        self.last: dict | None = None

    # ------------------------------------------------------------------ durable state
    def public(self) -> dict:
        return dict(continuations=self.continuations, stalled=self.stalled,
                    measured=self.measured, decisions=self.decisions[-MAX_DECISIONS:])

    def restore(self, row) -> None:
        if not isinstance(row, dict):
            return
        self.continuations = int(row.get("continuations") or 0)
        self.stalled = int(row.get("stalled") or 0)
        self.measured = int(row.get("measured") or 0)
        self.decisions = list(row.get("decisions") or ())[-MAX_DECISIONS:]

    # ------------------------------------------------------------------ decision
    def evaluate(self, *, budget: dict, stop: str = "RUNNING",
                 execution_stopped: str | None = None, runs=None,
                 attempt_limit: int = 2, group_blocker=None) -> dict:
        """One verdict at the moment the model asks to finish."""
        state = obligation_state(self.project, self.summary)
        plan = dispatch_plan(self.project, self.summary, runs=runs,
                             attempt_limit=attempt_limit, group_blocker=group_blocker)
        cleanup = cleanup_state(self.project, self.summary)
        decision = decide(
            state=state, plan=plan, cleanup=cleanup, budget=dict(budget), stop=stop,
            execution_stopped=execution_stopped, continuations=self.continuations,
            stalled=self.stalled, measured=len(state["measured"]), measured_before=self.measured,
            max_continuations=self.max_continuations, max_stalled=self.max_stalled)
        if decision["action"] == "CONTINUE":
            self.continuations += 1
            self.stalled = 0 if len(state["measured"]) > self.measured else self.stalled + 1
            self.measured = len(state["measured"])
        self.last = decision
        self.decisions.append({key: value for key, value in decision.items()
                               if key in ("action", "state", "code", "detail")})
        self.decisions = self.decisions[-MAX_DECISIONS:]
        return decision

    def terminate(self, *, stop_reason: str) -> dict:
        return termination(self.project, self.summary, stop_reason=stop_reason,
                           decisions=self.decisions)
