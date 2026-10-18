"""Bounded LangGraph decisions inside the original non-resumable Executor session."""

import asyncio
import time
from typing import TYPE_CHECKING, Callable, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from agenttest.credentials import CredentialVault
from agenttest.dsl import Step
from agenttest.feedback import FeedbackAction
from agenttest.llm_planner import PlannerFailure
from agenttest.plan_json import bounded_json
from agenttest.report import (
    FeedbackDecisionEvidence,
    FeedbackResult,
    InvestigationResult,
    ModelCallStats,
    Outcome,
    RunReport,
    StepResult,
    fingerprint,
)

if TYPE_CHECKING:
    from agenttest.executor import Executor


def evidence_summary(ref: str, result: StepResult, role: str | None) -> dict:
    """Transmit policy-owned check kinds and HTTP statuses, never bodies or field values."""
    return {"ref": ref, "operation_id": result.operation_id, "role": role,
            "outcome": result.outcome.value,
            "checks": [{"kind": check.kind, "selector": check.selector,
                        "passed": check.passed,
                        **({"expected": check.expected, "actual": check.actual}
                           if check.kind == "http_status" else {})}
                       for check in result.checks]}


def supported_finding(action: FeedbackAction, failed: StepResult, parent_role: str | None,
                      investigations: list[InvestigationResult],
                      source_roles: dict[str, str | None]) -> bool:
    """Validate observable symptoms only; never certify a guessed implementation cause."""
    refs = action.evidence_refs
    known = {result.step_id: result for result in investigations}
    if len(refs) != len(set(refs)) or any(ref != "original" and ref not in known for ref in refs):
        return False
    if action.finding == "EVIDENCE_INSUFFICIENT":
        return True
    if "original" not in refs:
        return False
    for ref in refs:
        result = known.get(ref)
        if (result is None or result.operation_id != failed.operation_id
                or not result.attempts or not result.checks):
            continue
        if action.finding == "ACCESS_BOUNDARY_VIOLATION":
            denied_success = any(not check.passed and check.kind == "http_status"
                and check.expected and all(code in {403, 404} for code in check.expected)
                and type(check.actual) is int and 200 <= check.actual < 300
                for check in failed.checks)
            if (denied_success and result.outcome == Outcome.PASS
                    and result.role == source_roles.get(result.source_step_id)
                    and result.role != parent_role):
                return True
        if action.finding == "PERSISTENT_READBACK_MISMATCH" and result.role == parent_role:
            original = {(c.kind, c.selector) for c in failed.checks
                        if not c.passed and c.kind != "http_status"}
            repeated = {(c.kind, c.selector) for c in result.checks
                        if not c.passed and c.kind != "http_status"}
            http_passed = all(any(c.kind == "http_status" and c.passed for c in item.checks)
                              and all(c.passed for c in item.checks if c.kind == "http_status")
                              for item in (failed, result))
            if original & repeated and http_passed and result.outcome == Outcome.ASSERTION_FAILED:
                return True
    return False


class FeedbackState(TypedDict):
    route: str


async def run_feedback(executor: "Executor", client: httpx.AsyncClient, report: RunReport,
                       state: dict, vault: CredentialVault, choices: list[dict], approved: dict,
                       failed: StepResult, parent: Step, source_roles: dict[str, str | None],
                       digest: Callable[[], str], frozen: str, last_request: float) -> None:
    """No retries, write replay, free-form tools, authority mutation or verdict replacement."""
    budget = executor.investigation.feedback.model_copy(deep=True)
    trace = FeedbackResult()
    report.feedback = trace
    started = time.perf_counter()
    remaining = max(0, executor.config.max_requests -
                    sum(len(step.attempts) for step in report.steps))
    requests = {"requests": 0, "last_request": last_request,
                "max_requests": min(budget.max_requests, remaining)}
    used, completed = set(), []
    calls = 0
    action = None
    entry = None
    finish_required = False
    pending = None

    def stop(reason, status="NEEDS_REVIEW"):
        trace.reason, trace.state = reason, status
        return {"route": "close"}

    def request_key(value):
        step, operation, policy, source_id = value
        return (operation.operation_id, step.role, source_id,
                tuple((name, binding.select) for name, binding in step.path_parameters.items()))

    async def decide(_):
        nonlocal action, calls, finish_required
        if calls >= budget.max_model_calls:
            return stop("FEEDBACK_MODEL_BUDGET_EXHAUSTED", "BUDGET_EXHAUSTED")
        if executor.analyzer is None:
            return stop("FEEDBACK_ANALYZER_UNAVAILABLE")
        if digest() != frozen:
            return stop("FEEDBACK_AUTHORITY_CHANGED")
        finish_required = (requests["requests"] >= requests["max_requests"] or
                           calls >= budget.max_model_calls - 1)
        available = [] if finish_required else [choice for choice in choices
            if request_key(approved[(choice["capability_id"], choice["role"],
                                     choice["resource_ref"])]) not in used]
        finish_required = finish_required or not available
        context = {"failure": evidence_summary("original", failed, parent.role),
            "choices": available, "finish_required": finish_required,
            "evidence": [evidence_summary(r.step_id, r, r.role) for r in completed],
            "remaining": {"model_calls": budget.max_model_calls - calls,
                          "requests": max(0, requests["max_requests"] - requests["requests"])}}
        if vault.contains_secret(context):
            return stop("FEEDBACK_CONTEXT_REJECTED")
        decision = FeedbackDecisionEvidence(index=calls)
        trace.decisions.append(decision)
        calls += 1
        trace.model_calls_used = calls
        executor.store.save(report)  # Journal the attempt before crossing the model boundary.
        before = fingerprint(report.investigations)
        call_started = time.perf_counter()
        raw = None
        try:
            raw = await executor.analyzer.generate(context)
            if digest() != frozen or fingerprint(report.investigations) != before:
                return stop("FEEDBACK_AUTHORITY_CHANGED")
            if vault.contains_secret(raw):
                return stop("FEEDBACK_ACTION_REJECTED")
            action = FeedbackAction.model_validate(bounded_json(raw, 8192))
            decision.action, decision.reason = action.action, "FEEDBACK_ACTION_RECEIVED"
            return {"route": "validate"}
        except PlannerFailure:
            decision.action, decision.reason = "rejected", "FEEDBACK_MODEL_ERROR"
            return stop("FEEDBACK_MODEL_ERROR", "TOOL_ERROR")
        except (ValueError, TypeError, RecursionError):
            decision.action, decision.reason = "rejected", "FEEDBACK_ACTION_REJECTED"
            return stop("FEEDBACK_ACTION_REJECTED")
        finally:
            raw = None
            decision.elapsed_ms = round((time.perf_counter() - call_started) * 1000, 3)
            stats = getattr(executor.analyzer, "stats", None)
            if isinstance(stats, ModelCallStats):
                decision.model_call = stats.model_copy(deep=True)
                if stats.calls and stats.total_tokens is None:
                    stop("FEEDBACK_MODEL_USAGE_UNKNOWN", "TOOL_ERROR")

    async def validate(_):
        nonlocal entry
        if trace.state != "PENDING":
            return {"route": "close"}
        if action.action == "finish":
            if not supported_finding(action, failed, parent.role, completed, source_roles):
                return stop("FEEDBACK_UNSUPPORTED_FINDING")
            trace.finding, trace.evidence_refs = action.finding, list(action.evidence_refs)
            if action.finding == "EVIDENCE_INSUFFICIENT":
                return stop("FEEDBACK_EVIDENCE_INSUFFICIENT")
            trace.basis = {
                "ACCESS_BOUNDARY_VIOLATION": "受限角色读取成功，同资源的所有者读取通过。",
                "PERSISTENT_READBACK_MISMATCH": "同角色对同一资源的复核重复出现字段断言失败。",
            }[action.finding]
            return stop("FEEDBACK_FINDING_SUPPORTED", "SUPPORTED")
        if finish_required:
            return stop("FEEDBACK_FINISH_REQUIRED", "BUDGET_EXHAUSTED")
        selection = action.selection
        entry = approved.get((selection.capability_id, selection.role, selection.resource_ref))
        if entry is None:
            return stop("FEEDBACK_SELECTION_REJECTED")
        if request_key(entry) in used:
            return stop("FEEDBACK_DUPLICATE_INVESTIGATION")
        return {"route": "execute"}

    async def execute(_):
        nonlocal pending
        if digest() != frozen:
            return stop("FEEDBACK_AUTHORITY_CHANGED")
        step, operation, policy, source_id = entry
        used.add(request_key(entry))
        pending = InvestigationResult(step_id=step.id, parent_step_id=failed.step_id,
            operation_id=step.operation_id, method=operation.method, path_template=operation.path,
            policy_sha256=fingerprint(policy.model_dump()),
            policy_template_sha256=fingerprint(policy.model_dump()),
            capability_id=action.selection.capability_id,
            role=step.role, source_step_id=source_id,
            resource_ref_sha256=fingerprint(action.selection.resource_ref),
            outcome=Outcome.NEEDS_REVIEW, reason="FEEDBACK_READ_PENDING",
            model_calls_used=1, rounds_used=1)
        report.investigations.append(pending)
        executor.store.save(report)
        read_started, before = time.perf_counter(), requests["requests"]
        try:
            vault.header(step.role)
            await executor._step(client, step, operation, state, requests, pending, report,
                                 policy, vault)
        finally:
            pending.elapsed_ms = round((time.perf_counter() - read_started) * 1000, 3)
            pending.requests_used = requests["requests"] - before
            trace.requests_used = requests["requests"]
        completed.append(pending.model_copy(deep=True))
        if digest() != frozen:
            return stop("FEEDBACK_AUTHORITY_CHANGED")
        if pending.outcome not in {Outcome.PASS, Outcome.ASSERTION_FAILED}:
            return stop("FEEDBACK_READ_FAILED", "TOOL_ERROR")
        executor.store.save(report)
        return {"route": "decide"}

    graph = StateGraph(FeedbackState)
    graph.add_node("decide", decide)
    graph.add_node("validate", validate)
    graph.add_node("execute", execute)
    graph.add_node("close", lambda _: {"route": "close"})
    graph.add_edge(START, "decide")
    for node in ("decide", "validate", "execute"):
        graph.add_conditional_edges(node, lambda value: value["route"],
            {"decide": "decide", "validate": "validate", "execute": "execute", "close": "close"})
    graph.add_edge("close", END)
    try:
        with tracing_context(enabled=False):
            async with asyncio.timeout(budget.timeout):
                await graph.compile().ainvoke({"route": "decide"}, config={
                    "recursion_limit": 12, "callbacks": []})
    except TimeoutError:
        stop("FEEDBACK_TIMEOUT", "TIMEOUT")
        if pending and pending.reason == "FEEDBACK_READ_PENDING":
            pending.outcome, pending.reason = Outcome.TEST_INFRA_ERROR, "ATTEMPT_OUTCOME_UNKNOWN"
    except asyncio.CancelledError:
        stop("FEEDBACK_CANCELLED", "CANCELLED")
        if pending and pending.reason == "FEEDBACK_READ_PENDING":
            pending.outcome, pending.reason = Outcome.TEST_INFRA_ERROR, "ATTEMPT_OUTCOME_UNKNOWN"
        raise
    except httpx.TransportError:
        stop("FEEDBACK_TRANSPORT_ERROR", "TEST_INFRA_ERROR")
        if pending:
            pending.outcome = Outcome.TEST_INFRA_ERROR
            pending.reason = "FEEDBACK_TRANSPORT_ERROR"
    except Exception:
        stop("FEEDBACK_TOOL_ERROR", "TOOL_ERROR")
        if pending and pending.reason == "FEEDBACK_READ_PENDING":
            pending.outcome, pending.reason = Outcome.TOOL_ERROR, "FEEDBACK_READ_FAILED"
    finally:
        trace.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        trace.requests_used = requests["requests"]
        action = entry = None
        used.clear()
