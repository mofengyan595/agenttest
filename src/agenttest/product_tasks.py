"""Product orchestration around the existing single Workflow and deterministic Executor."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

import httpx
from pydantic import ValidationError

from agenttest.credentials import CredentialVault, sensitive_field
from agenttest.diagnostics import presentation_language
from agenttest.fixed_plan import FixedPlanSnapshot
from agenttest.goal_suite import BudgetPlanner, BudgetTransport, SuiteLedger
from agenttest.llm_planner import PlannerFailure
from agenttest.product_design import design_groups, propose_design, validate_design_case
from agenttest.report import ModelCallStats, Outcome, aggregate, fingerprint
from agenttest.requirement_ledger import (
    explicit_data_references,
    explicit_http_statuses,
    explicit_response_checks,
    record_requirement_evidence,
    unresolved_data_operations,
    unresolved_http_status_operations,
    unresolved_response_operations,
)
from agenttest.task_store import TERMINAL, TaskConflict

DEFAULT_ROUND = "安排一轮测试，优先正常请求、参数边界、已批准的身份检查与跨接口流程，并说明遗漏。"
DEFAULT_FOLLOWUP = "沿用上次测试重点，按最新已确认的信息安排尚未完成的检查。"


def request_planning_ir(project, selection, atoms, text):
    """Build the normalized planning representation from the validated Focus output."""
    from agenttest.planning_ir import build_planning_ir

    requirements = [item.model_dump() if hasattr(item, "model_dump") else item
                    for item in selection.requirements]
    inputs = [item.model_dump() if hasattr(item, "model_dump") else item
              for item in selection.input_requirements]
    return build_planning_ir(
        requirements, atoms, operations=[dict(id=op.operation_id, method=op.method,
                                              path=op.path)
                                         for op in project.contract.operations],
        policies=[policy.model_dump() for policy in project.registry.definitions()],
        target_lifecycles=[rule.model_dump() for rule in project.target.resource_lifecycles],
        read_operations={op.operation_id for op in project.contract.operations
                         if op.method in {"GET", "HEAD", "OPTIONS"}},
        input_requirements=inputs)


def aggregate_usage(records):
    """Sum every known model call; a single unknown amount keeps the total UNKNOWN."""
    calls = [record.get("model_call") for record in records or ()
             if isinstance(record.get("model_call"), dict) and record["model_call"].get("calls")]
    if not calls:
        return 0, None
    usage = ModelCallStats(calls=sum(call["calls"] for call in calls))
    if all(call.get("total_tokens") is not None for call in calls):
        usage.total_tokens = sum(call["total_tokens"] for call in calls)
        if all(call.get("input_tokens") is not None for call in calls):
            usage.input_tokens = sum(call["input_tokens"] for call in calls)
        if all(call.get("output_tokens") is not None for call in calls):
            usage.output_tokens = sum(call["output_tokens"] for call in calls)
    usage.input_token_upper_bound = sum(call.get("input_token_upper_bound") or 0
                                       for call in calls)
    usage.request_bytes = sum(call.get("request_bytes") or 0 for call in calls)
    usage.response_bytes = sum(call.get("response_bytes") or 0 for call in calls)
    usage.elapsed_ms = round(sum(call.get("elapsed_ms") or 0 for call in calls), 3)
    names = {call.get("model_name") for call in calls}
    usage.model_name = names.pop() if len(names) == 1 else None
    digests = {call.get("model_sha256") for call in calls}
    usage.model_sha256 = digests.pop() if len(digests) == 1 else None
    return usage.calls, usage


IR_ENGINE = "NORMALIZED_IR_COMPILER_V1"
LEGACY_ENGINE = "LEGACY"
NOT_EXECUTED_ENGINE = "NOT_EXECUTED"
DEFAULT_TASK_SECONDS = 600.0


def task_max_seconds(service):
    """Production stays at 600s; an isolated development runner may set a smaller cap."""
    return float(getattr(service, "dev_max_seconds", DEFAULT_TASK_SECONDS))


def unresolved_ir_clauses(planning_ir, focus_requirements):
    """Clauses where normalization hid a request the Design path would still have to hold.

    Status-only, data-capture and explicit ``constraint`` clauses are intentionally not
    requests. A ``needs_review`` clause with actions, or a multi-action required clause whose
    identity is not resolved, must keep the legacy path so no obligation silently disappears.
    """
    if planning_ir is None:
        return []
    original = {item.get("source_id"): item for item in focus_requirements or ()}
    unresolved = []
    for clause in planning_ir.clauses:
        role = getattr(clause.role, "value", str(clause.role))
        if role != "CONSTRAINT":
            continue
        requirement = original.get(clause.source_id) or {}
        actions = requirement.get("actions") or []
        if not actions:
            continue
        if (requirement.get("disposition") == "needs_review"
                or requirement.get("disposition") == "required" and len(actions) > 1):
            unresolved.append(clause.source_id)
    return unresolved


def planning_engine_conditions(planning_ir, *, group, questions, requested_checks,
                               permission_requirements, unresolved):
    """The exact, auditable values behind the production compiler choice."""
    instances = getattr(planning_ir, "instances", []) if planning_ir is not None else []
    operations = {item.operation_id for item in instances}
    return dict(
        planning_ir=planning_ir is not None,
        normalization_applied=bool(planning_ir is not None
                                   and planning_ir.normalization_applied),
        no_ambiguities=not bool(planning_ir is not None and planning_ir.ambiguities),
        no_pending_permissions=not bool(permission_requirements),
        no_pending_questions=not bool(questions),
        no_requested_checks=not bool(requested_checks),
        has_instances=bool(instances),
        all_instances_in_group=bool(instances and operations <= set(group.get("primary") or [])),
        no_unresolved_request_clauses=not unresolved,
        instances=len(instances), group_operations=sorted(group.get("primary") or []),
        unresolved_clauses=list(unresolved))


def planning_engine_choice(conditions):
    """First concrete reason the normalized compiler cannot own this group."""
    for reason, key in (
            ("NO_PLANNING_IR", "planning_ir"),
            ("NO_NORMALIZATION", "normalization_applied"),
            ("IR_AMBIGUITIES", "no_ambiguities"),
            ("PENDING_PERMISSION", "no_pending_permissions"),
            ("PENDING_QUESTIONS", "no_pending_questions"),
            ("REQUESTED_CHECKS", "no_requested_checks"),
            ("NO_INSTANCES", "has_instances"),
            ("UNRESOLVED_REQUEST_CLAUSES", "no_unresolved_request_clauses"),
            ("PARTIAL_GROUP_COVERAGE", "all_instances_in_group")):
        if not conditions.get(key):
            return dict(engine=LEGACY_ENGINE, reason=reason, conditions=conditions)
    return dict(engine=IR_ENGINE, reason="IR_SELECTED", conditions=conditions)


SUPERVISION_IR_SCOPED = "SUPERVISION_IR_SCOPED"
SUPERVISION_IR_UNAVAILABLE = "SUPERVISION_IR_UNAVAILABLE"
SUPERVISION_IR_EMPTY = "SUPERVISION_IR_EMPTY"


def scoped_supervision_ir(planning_ir, group):
    """The Planning IR a follow-up round plans from, and why it had to fall back.

    Returning ``None`` here is a *recorded* downgrade, never a silent one: the caller puts the
    reason in the round's design record, so a reader can tell an IR-planned follow-up from a
    legacy redesign. The IR itself is narrowed, not copied blindly: instances outside the
    obligation would make ``all_instances_in_group`` false and drop the compiler path anyway.
    """
    if planning_ir is None:
        return None, dict(code=SUPERVISION_IR_UNAVAILABLE,
                          reason="本轮原始需求的 Planning IR 不可用，"
                                 "补测只能走既有 Legacy 设计路径。")
    scoped = planning_ir.scope_to(group.get("primary") or ())
    if not scoped.instances:
        return None, dict(code=SUPERVISION_IR_EMPTY,
                          reason="补测义务在 Planning IR 中没有对应实例，"
                                 "补测只能走既有 Legacy 设计路径。",
                          operations=sorted(group.get("primary") or ()))
    closure = sorted({item.operation_id for item in scoped.instances})
    return scoped, dict(code=SUPERVISION_IR_SCOPED,
                        instances=len(scoped.instances),
                        operations=closure,
                        requested=sorted(group.get("primary") or ()),
                        closure_added=sorted(set(closure)
                                             - set(group.get("primary") or ())),
                        resources=[item.resource_id for item in scoped.resources],
                        edges=len(scoped.edges))


def actual_planning_engine(construction):
    """Derive the engine from the construction records that actually ran, not from intent."""
    encodings = {item.get("encoding") for item in construction or ()
                 if isinstance(item, dict) and item.get("encoding")}
    if not encodings:
        return NOT_EXECUTED_ENGINE
    if encodings == {IR_ENGINE}:
        return IR_ENGINE
    if IR_ENGINE not in encodings:
        return LEGACY_ENGINE
    return "MIXED"


def planning_engine_evidence(engine, choice, construction):
    """Report what the compiler actually did, including a fallback reason."""
    compiled = [item for item in construction or ()
                if isinstance(item, dict) and item.get("encoding") == IR_ENGINE]
    mapped = all(len(item.get("instance_steps") or {}) == len(item.get("instances") or [])
                 for item in compiled)
    conditions = (choice or {}).get("conditions") or {}
    chosen = (choice or {}).get("engine")
    reason = (choice or {}).get("reason") or "UNKNOWN"
    if engine == IR_ENGINE:
        reason = "IR_SELECTED"
    elif engine == "MIXED":
        reason = "MIXED_COMPILER_PATHS"
    elif engine == NOT_EXECUTED_ENGINE:
        reason = "NOT_EXECUTED"
    elif chosen == IR_ENGINE:
        reason = "IR_SELECTED_BUT_LEGACY_CASE"
    return dict(
        planning_engine=engine,
        planning_engine_reason=reason,
        planning_engine_choice_engine=chosen,
        planning_engine_choice_reason=(choice or {}).get("reason"),
        planning_engine_conditions=conditions,
        planning_instance_count=int(conditions.get("instances") or 0),
        planning_instance_mapping_complete=mapped if compiled else None,
        planning_dependency_edges=sum(item.get("dependency_edges") or 0
                                      for item in construction or ()
                                      if isinstance(item, dict)))


class JournalPlanner:
    """A crash or cancellation during a call leaves a durable usage stop, never a retry."""

    def __init__(self, delegate, db, task_id):
        self.delegate, self.db, self.task_id = delegate, db, task_id

    @property
    def stats(self):
        return getattr(self.delegate, "stats", ModelCallStats(calls=1))

    @property
    def token_reservation(self):
        return getattr(self.delegate, "token_reservation", 0)

    async def generate(self, context):
        call_id = uuid4().hex
        self.db.begin_model_call(call_id, self.task_id)
        try:
            return await self.delegate.generate(context)
        finally:
            self.db.finish_model_call(call_id, self.stats)


@dataclass
class RunScope:
    """References one product round needs; it carries no business rule of its own.

    The same object reaches every group of the deterministic round and every round
    supervision action, so an extra round cannot accidentally run against a different
    project, budget, transport or authority guard.
    """

    svc: object
    task_id: str
    owner: str
    row: dict
    project: object
    project_record: dict
    summary: dict
    context: dict
    text: str
    ledger: object
    transport: object
    guard: Callable[[], None]
    checkpoint: Callable[[], None]
    executable_checks: list
    planning_ir: object | None
    requirements: list
    state: dict = field(default_factory=dict)


class ProductTasks:
    def __init__(self, service):
        self.service = service
        self.inputs = set()

    async def submit(self, owner, project, text, submission, parent=None):
        svc = self.service
        text = text.strip() or (DEFAULT_FOLLOWUP if parent else DEFAULT_ROUND)
        current_request, previous_requests, previous_result = text, [], None
        if parent:
            previous = svc.db.get(parent, owner)
            if previous["project_id"] != project.registration.id:
                raise TaskConflict("CONTEXT_EXPIRED_OR_WRONG_PROJECT")
            context = svc.db.context(parent, owner)
            previous_requests = context.get("requests", [context["text"]])
            previous_result = dict(status=previous["status"],
                complete=previous["summary"].get("complete", False),
                executed_operations=sorted({operation
                    for case in previous["summary"].get("cases", [])
                    for operation in case.get("executed_operations", [])}),
                untested_operations=[dict(operation_id=o["operation_id"], reason=o["reason"])
                    for o in previous["summary"].get("untested_operations", [])],
                questions=previous["summary"].get("questions", []),
                input_requirements=previous["summary"].get("focus", {}).get(
                    "input_requirements", []),
                input_questions=previous["summary"].get("pending_input_questions",
                    previous["summary"].get("focus", {}).get("input_questions", [])),
                requested_checks=previous["summary"].get("focus", {}).get("requested_checks", []),
                expectation_questions=previous["summary"].get("focus", {}).get(
                    "expectation_questions", []),
                normal_operation_ids=previous["summary"].get("focus", {}).get(
                    "normal_operation_ids", []),
                executed_policy_ids=sorted({key for case in previous["summary"].get("cases", [])
                    for key in case.get("executed_policy_ids", [])})
                    if previous["project_identity"] == project.identity() else [],
                coverage_basis_changed=previous["project_identity"] != project.identity(),
                not_covered=previous["summary"].get("not_covered", []))
            from agenttest.planner_input import validate_user_task

            text = validate_user_task(context["text"] + "\n补充：" + text)
        if svc.private_input_guard(text):
            raise ValueError("TASK_SECRET_REJECTED")
        vault = CredentialVault(project.target, svc.environ)
        try:
            if vault.contains_secret(text) or any(value and value in text for name, value
                    in svc.environ.items() if name.startswith("AGENTTEST_TARGET_")
                    and sensitive_field(name)):
                raise ValueError("TASK_SECRET_REJECTED")
        finally:
            vault.close()
        inventory = svc.intake.inspect(svc.intake.record(project.registration.id))["inventory"]
        budget = dict(max_requests=project.target.max_requests,
            max_model_calls=project.registration.budget.suite_max_model_calls,
            max_total_tokens=project.registration.budget.suite_max_total_tokens,
            max_steps_per_case=project.target.max_steps,
            max_seconds=task_max_seconds(svc),
            max_readonly_investigations=2)
        summary = dict(mode="MODEL_DESIGNED", display_title="一轮接口测试", user_request=text,
            scope="project", inventory=inventory, budget=budget, cases=[], design_records=[],
            questions=[], not_covered=[], groups=design_groups(project),
            model_source=svc.model_status(), execution_target="AUTHORIZED_TEST_ENVIRONMENT",
            complete=False, task_satisfied=None)
        from agenttest.request_requirements import request_atoms

        summary['requirement_sources'] = request_atoms(current_request)
        summary["negative_check_catalog"] = [dict(policy_id=p.id, operation_id=p.operation_id,
            field=p.negative.name, kind=p.negative.kind,
            example=(f"长度 {len(p.negative.value)}" if isinstance(p.negative.value, (str, list))
                else str(p.negative.value)) if p.negative.kind == "boundary_outside" else None)
            for p in project.registry.definitions() if p.negative]
        state = "WAITING_REVIEW" if project.registration.require_plan_review else "QUEUED"
        row, created = svc.db.create(uuid4().hex, owner, submission, project.registration.id,
            project.identity(), fingerprint(text), state, summary)
        if created:
            ledger = SuiteLedger(budget["max_requests"], budget["max_model_calls"],
                                 budget["max_total_tokens"])
            svc._inputs[row["id"]] = (project.registration.id, text, False, ledger)
            svc._owners[row["id"]] = owner
            self.inputs.add(row["id"])
            svc.db.save_context(row["id"], owner, dict(text=text, current_request=current_request,
                requests=[*previous_requests, current_request], previous_result=previous_result,
                usage=ledger.summary()))
            svc.db.event(row["id"], state)
            if state == "QUEUED":
                svc._enqueue(row["id"], owner)
        return row

    async def run(self, task_id, owner):
        svc = self.service
        _, text, _, ledger = svc._inputs[task_id]
        summary = svc.db.get(task_id, owner)["summary"]
        context = svc.db.context(task_id, owner)
        transport = None
        executable_checks = []
        started = time.perf_counter()

        def record_timing():
            summary["timings"] = dict(total_ms=round((time.perf_counter() - started) * 1000, 3),
                planning_ms=round(sum(r.get("elapsed_ms", 0)
                    for r in summary.get("design_records", [])), 3),
                execution_ms=round(sum(c.get("elapsed_ms", 0)
                    for c in summary.get("cases", [])), 3))

        def checkpoint():
            summary["context_usage"] = ledger.summary()
            record_timing()
            svc.db.transition(task_id, owner, "RUNNING", "RUNNING", summary=summary)
            svc.db.save_context(task_id, owner, {**context, "usage": ledger.summary()})

        try:
            async with svc._serial:
                row = svc.db.transition(task_id, owner, "QUEUED", "RUNNING")
                project = svc.project(row["project_id"])
                project_record = svc.intake.record(row["project_id"])
                planning_ir = None

                def guard():
                    if svc.project(row["project_id"]).identity() != row["project_identity"]:
                        raise ValueError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")

                guard()
                if svc.designer_factory is None:
                    raise ValueError("PRODUCT_DESIGNER_NOT_CONFIGURED")
                transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
                bounded_transport = BudgetTransport(transport, ledger, guard)
                async with asyncio.timeout(summary["budget"]["max_seconds"]):
                    if context.get("current_request", text) == DEFAULT_ROUND:
                        self.note_unready(project, summary, [op["operation_id"]
                            for op in summary["inventory"]["operations"] if op["state"] != "READY"],
                            ask_unauthorized=False)
                        checkpoint()
                    if (context.get("current_request", text) != DEFAULT_ROUND
                            and svc.focus_factory is not None):
                        from agenttest.product_focus import (
                            permission_review_scope,
                            review_only_operations,
                            rule_review_scope,
                            select_focus,
                        )

                        status_requirements = explicit_http_statuses(context["current_request"])
                        if status_requirements:
                            summary["http_status_requirements"] = status_requirements
                        response_requirements = explicit_response_checks(context["current_request"])
                        if response_requirements:
                            summary["response_requirements"] = response_requirements
                        data_references = explicit_data_references(context['current_request'])
                        if data_references:
                            summary['data_references'] = data_references
                        raw_focus = svc.focus_factory(project)
                        focus_model = BudgetPlanner(JournalPlanner(raw_focus, svc.db, task_id),
                                                    ledger, guard)
                        focus_record = dict(group_id="focus", status="PLANNING")
                        summary["design_records"].append(focus_record)
                        svc.db.event(task_id, "PLANNING", requests=ledger.requests)
                        checkpoint()
                        selection = None
                        focus_started = time.perf_counter()
                        try:
                            selection = await select_focus(project, focus_model, context,
                                environ=svc.environ, notes=project_record.get("business_notes", ""),
                                inventory=summary["inventory"],
                                available_identity_aliases=[r["alias"] for r in
                                    project_record.get("generic", {}).get("roles", [])],
                                timeout=getattr(getattr(raw_focus, "_settings", None),
                                                "timeout", 90.0) + 1)
                            summary["focus"] = selection.model_dump()
                            try:
                                planning_ir = request_planning_ir(
                                    project, selection, summary["requirement_sources"],
                                    context["current_request"])
                                summary["planning_ir"] = planning_ir.public_summary()
                            except Exception as exc:
                                # The legacy path stays available for an input shape the
                                # normalizer does not cover; nothing is guessed or applied.
                                planning_ir = None
                                summary["planning_ir_error"] = type(exc).__name__
                            summary["focus_presentation"] = presentation_language(
                                selection.interpretation)
                            summary["requirement_ledger"] = selection.requirement_ledger
                            required_sources = {r.source_id for r in selection.requirements
                                                if r.disposition == 'required'}
                            for entry in status_requirements:
                                if (entry['source_id'] in required_sources
                                        and entry['reason'] == 'HTTP_STATUS_UNWITNESSED'):
                                    entry['status'] = 'NOT_EXECUTED'
                            summary["requested_checks"] = [r.model_dump()
                                                            for r in selection.requested_checks]
                            for question in selection.expectation_questions:
                                summary["questions"].append(dict(
                                    id="question_" + fingerprint(question.model_dump())[:20],
                                    operation_id=question.operation_id, question=question.question,
                                    requested_behavior=question.requested_behavior,
                                    kind="permission" if question.kind == "permission"
                                        else "clarification", role=question.role,
                                    status_choices=question.status_choices))
                            from agenttest.product_checks import partition_requested_checks

                            executable_checks, pending_values = partition_requested_checks(
                                project, summary["requested_checks"],
                                input_requirements=[r.model_dump()
                                    for r in selection.input_requirements],
                                input_questions=[q.model_dump() for q in selection.input_questions])
                            summary["pending_negative_values"] = pending_values
                            for pending in pending_values:
                                reason = {
                                    "NEGATIVE_VALUE_VARIANT_NOT_AUTHORIZED":
                                        "尚未授权同类异常取值变化。请在项目的补充信息中开启该范围。",
                                    "NEGATIVE_VALUE_BOUNDARY_DIRECTION_CHANGED":
                                        "本项方案的边界方向与指定值不一致，不能按此方案执行。",
                                    "NEGATIVE_QUERY_VARIANT_UNREPRESENTABLE":
                                        "当前查询参数异常值只支持标量，不会把数组、对象或空值改成字符串。",
                                    "NEGATIVE_MISSING_FIELD_HAS_NO_REPLACEMENT_VALUE":
                                        "缺失字段检查不接收替代值，请明确是漏传字段还是传入错误值。",
                                    "NEGATIVE_LITERAL_NOT_PRESERVED":
                                        "方案没有保留需求中的明确字符串，不能改用默认错误值。"
                                        "本项等待重新安排，其他已授权检查继续。",
                                }.get(pending["reason"],
                                    "该值的约束偏差超出本项批准范围，不能改用其他值代替。")
                                summary["questions"].append(dict(
                                    id="negative_value_" + fingerprint(pending)[:20],
                                    operation_id=pending["operation_id"],
                                    question=f"{pending['field']} 的指定异常值尚未执行。{reason}",
                                    requested_behavior="保留指定异常值，不用默认错误值替换",
                                    kind="clarification", role=None, status_choices=[]))
                            from agenttest.product_inputs import (
                                input_blocked_operations,
                                unresolved_input_questions,
                            )

                            relevant = {op for g in design_groups(project, operation_ids=(
                                None if selection.mode == "all_ready" else selection.operation_ids))
                                for op in g["operations"]}
                            input_questions = unresolved_input_questions(
                                (context.get("previous_result") or {}).get("input_questions", []),
                                selection.input_questions, selection.input_requirements,
                                context["current_request"], relevant)
                            summary["pending_input_questions"] = [q.model_dump()
                                                                  for q in input_questions]
                            check_operations = {p.id: p.operation_id
                                                for p in project.registry.definitions()}
                            pending_only = {p['operation_id'] for p in pending_values
                                if p['reason'] == 'NEGATIVE_VALUE_VARIANT_NOT_AUTHORIZED'
                                and type(p['requested_value']) in {int, float}}
                            pending_only.difference_update(selection.normal_operation_ids,
                                (check_operations[c['policy_id']] for c in executable_checks))
                            rule_reads, rule_logins = rule_review_scope(project, selection)
                            permission_blocked, planning_requirements, action_scope, holds = (
                                permission_review_scope(selection))
                            summary['planning_requirements'] = planning_requirements
                            summary['permission_action_scope'] = action_scope
                            if holds:
                                summary['permission_requirements'] = holds
                            response_blocked = unresolved_response_operations(response_requirements,
                                selection, project, executable_checks)
                            # A held assertion keeps its action running, so the pending
                            # check must stay visible even when no model question exists.
                            known_questions = {(question.operation_id, question.source_quote)
                                               for question in selection.expectation_questions}
                            for entry in response_requirements:
                                for operation_id in entry.get('held_operations') or []:
                                    if (operation_id, entry['source_quote']) in known_questions:
                                        continue
                                    known_questions.add((operation_id, entry['source_quote']))
                                    selector = entry.get('selector') or '响应内容'
                                    summary["questions"].append(dict(
                                        id="held_" + fingerprint([operation_id,
                                            entry['source_quote']])[:20],
                                        operation_id=operation_id,
                                        question=(f"原文要求核对 {selector}，但缺少已确认的规则；"
                                            "这条核对待确认，相关读取仍会照常执行。"),
                                        requested_behavior="确认响应内容规则",
                                        kind="clarification", role=None, status_choices=[]))
                            status_blocked = unresolved_http_status_operations(
                                status_requirements, selection, project)
                            data_blocked = unresolved_data_operations(data_references,
                                                                     selection, project)
                            if rule_reads:
                                summary['expectation_blocked_operations'] = sorted(
                                    rule_reads | rule_logins)
                            blocked_inputs = input_blocked_operations(project, input_questions,
                                operation_ids=[*selection.source_conflicts, *pending_only,
                                               *rule_reads, *rule_logins, *response_blocked,
                                               *status_blocked,
                                               *permission_blocked,
                                               *data_blocked,
                                               *review_only_operations(selection)],
                                requirement_links=selection.requirements)
                            summary["input_blocked_operations"] = blocked_inputs
                            summary["source_blocked_operations"] = input_blocked_operations(project,
                                [], operation_ids=selection.source_conflicts)
                            for key in selection.source_conflicts:
                                operation = next(o for o in project.contract.operations
                                                 if o.operation_id == key)
                                label = operation.summary or operation.method + " " + operation.path
                                summary["questions"].append(dict(
                                    id="source_" + fingerprint(key)[:20], operation_id=key,
                                    question=f"您指定了已有资源，但“{label}”当前要求使用前置接口的数据。"
                                        "能否先在项目配置中补充允许的已有资源输入来源？",
                                    requested_behavior="确认已有资源的数据来源",
                                    kind="clarification",
                                    role=None, status_choices=[]))
                            for question in input_questions:
                                summary["questions"].append(dict(
                                    id="input_" + fingerprint(question.model_dump())[:20],
                                    operation_id=question.operation_id, question=question.question,
                                    requested_behavior="确认本次输入取值", kind="clarification",
                                    role=None, status_choices=[], input_choices=question.choices,
                                    input_label=".".join([question.name, *question.path])))
                            self.note_unready(project, summary, selection.blocked_operation_ids)
                            for question in selection.pending_questions:
                                summary["questions"].append(dict(
                                    id="focus_" + fingerprint(question)[:20], operation_id=None,
                                    question=question, requested_behavior="本次受阻的测试范围",
                                    kind="clarification", role=None, status_choices=[]))
                            summary["not_covered"].extend(selection.not_covered)
                            focus_record.update(status="SELECTED",
                                                interpretation=selection.interpretation)
                        except (PlannerFailure, ValueError, TimeoutError) as exc:
                            if hasattr(exc, "requirement_ledger"):
                                summary["requirement_ledger"] = exc.requirement_ledger
                            diagnostic = getattr(exc, 'diagnostic', None)
                            if diagnostic is not None:
                                focus_record['diagnostic'] = diagnostic.model_dump(mode='json')
                            focus_record.update(status="REJECTED", reason=getattr(
                                exc, "reason", None) or getattr(
                                    getattr(exc, "diagnostic", None), "code", None)
                                or "FOCUS_NOT_VALIDATED")
                        finally:
                            focus_record["elapsed_ms"] = round(
                                (time.perf_counter() - focus_started) * 1000, 3)
                            focus_record["model_call"] = focus_model.stats.model_dump(mode="json")
                            focus_record['proposed_question_count'] = getattr(
                                raw_focus, 'proposed_question_count', None)
                            checkpoint()
                        if selection is None or selection.mode == "needs_information":
                            if selection:
                                summary["questions"].append(dict(id="focus_question",
                                    operation_id=None, question=selection.question,
                                    requested_behavior="测试范围", kind="clarification",
                                    role=None, status_choices=[]))
                            else:
                                summary["reason"] = (
                                    "模型请求未返回可核对的用量，已停止新的模型调用。"
                                    "需先核对服务端记录；本次没有发送业务请求。"
                                    if ledger.usage_unknown else
                                    "模型未给出通过校验的测试范围，本次没有发送业务请求。"
                                    "请查看方案原因，再安排新的独立测试。")
                                code = getattr(focus_model.stats.diagnostic, "code", None)
                                summary["reason"] = {
                                    "MODEL_TOKEN_BUDGET": "规划输入超过本任务预算，未调用模型，"
                                        "也未发送业务请求。请查看输入预算记录。",
                                    "FOCUS_INPUT_TEXT_CHANGED": "模型提取的文字没有保留原文，"
                                        "本次没有发送业务请求。请核对需求后安排新的独立测试。",
                                    "FOCUS_INPUT_NUMBER_CHANGED": "模型提取的数字与原文不一致，"
                                        "本次没有发送业务请求。请核对需求后安排新的独立测试。",
                                    "FOCUS_INPUT_VALUE_UNAPPROVED": "模型选择的输入值不在已批准"
                                        "取值范围内，本次没有发送业务请求；允许的取值由操作员批准。",
                                    "FOCUS_INPUT_FIELD_CHANGED": "模型把明确输入关联到了其他字段，"
                                        "未发送业务请求；原文保留供核对。",
                                    "FOCUS_INPUT_QUESTION_CONFLICT":
                                        "模型对同一输入同时确认和提问，"
                                        "本次没有发送业务请求。请重新交代需要检查的内容。",
                                    "FOCUS_WIRE_FLAGS_INCOMPLETE": "模型遗漏了部分接口的处理决定，"
                                        "本次没有发送业务请求。请安排新的独立测试。",
                                }.get(code, summary["reason"])
                            for group in summary["groups"]:
                                group.update(status="NOT_EXECUTED", reason="测试重点尚未可靠确认。")
                            self.coverage(summary)
                            svc.db.transition(task_id, owner, "RUNNING", "NEEDS_REVIEW",
                                              summary=summary)
                            return
                        if selection.mode == 'no_current_action':
                            unresolved = (status_requirements or response_requirements
                                          or data_references)
                            summary.update(complete=not unresolved,
                                checks_passed=False,
                                reason='本轮仅包含未来动作或范围约束，没有安排当前业务请求。',
                                groups=[])
                            self.coverage(summary)
                            checkpoint()
                            svc.db.transition(task_id, owner, 'RUNNING', 'FINISHED',
                                              summary=summary)
                            svc.db.event(task_id, 'FINISHED', requests=ledger.requests)
                            return
                        held_elsewhere = bool(
                            summary.get("focus", {}).get("blocked_operation_ids")
                            or summary.get("focus", {}).get("source_conflicts")
                            or summary.get("focus", {}).get("blocked_operations")
                            or summary.get("input_blocked_operations")
                            or summary.get("questions")
                            or summary.get("http_status_requirements")
                            or summary.get("requested_checks"))
                        if (planning_ir is not None and planning_ir.normalization_applied
                                and planning_ir.ambiguities
                                and len(summary["groups"]) == 1
                                and not held_elsewhere):
                            fields = [field for item in planning_ir.ambiguities
                                      for field in item.fields][:20]
                            summary.update(
                                complete=False, checks_passed=False,
                                reason="原文中的请求与约束还不能唯一确定执行实例；"
                                       "本轮没有发送业务请求，请查看规划诊断。",
                                diagnostic=dict(code="PLANNING_IR_AMBIGUOUS", layer="MODEL",
                                                fields=fields))
                            for group in summary["groups"]:
                                group.update(status="NOT_EXECUTED",
                                             reason="请求实例存在待确认的歧义。")
                            self.coverage(summary)
                            checkpoint()
                            svc.db.transition(task_id, owner, "RUNNING", "NEEDS_REVIEW",
                                              summary=summary)
                            return
                        selected_ids = (selection.operation_ids if selection.mode == "selected"
                                        else project.target.allowed_operations)
                        if selection.mode == 'selected':
                            selected_ids = [op for op in selected_ids if
                                selection.operation_sources.get(op) != 'TRUSTED_PREREQUISITE']
                        summary["groups"] = design_groups(project, operation_ids=[
                            op for op in selected_ids if op not in blocked_inputs
                            and op not in rule_logins],
                            requirement_links=selection.requirements)
                        if rule_reads and not summary['groups']:
                            summary['reason'] = '业务规则待确认。本轮没有安排业务请求。'
                        checkpoint()
                    design_requirements = summary.get('planning_requirements',
                        summary.get('focus', {}).get('requirements', []))
                    scope = RunScope(svc=svc, task_id=task_id, owner=owner, row=row,
                        project=project, project_record=project_record, summary=summary,
                        context=context, text=text, ledger=ledger,
                        transport=bounded_transport, guard=guard, checkpoint=checkpoint,
                        executable_checks=executable_checks, planning_ir=planning_ir,
                        requirements=design_requirements)
                    for position, group in enumerate(summary["groups"]):
                        await self.run_group(scope, group, position=position,
                                             future_groups=summary["groups"][position + 1:])
                    await self.supervise(scope)
                rejected = [entry for entry in summary["design_records"]
                            if entry.get("status") == "REJECTED"]
                if rejected and not summary["cases"] and not ledger.requests:
                    # A design proposal that never passed validation is an unresolved round,
                    # never a finished one: keep the diagnostic and publish a failure report.
                    diagnostic = rejected[0].get("diagnostic") or {}
                    code = rejected[0].get("reason") or diagnostic.get("code") or \
                        "DESIGN_REJECTED"
                    report = self.failure_report(project, text, code, diagnostic,
                                                 records=summary["design_records"])
                    summary.update(
                        complete=False, checks_passed=False, report_id=report.run_id,
                        model_calls=report.planner_calls,
                        model_usage=(report.model_call.model_dump(mode="json")
                                     if report.model_call else None),
                        reason="方案未通过校验，本轮没有发送任何业务请求。请查看诊断后重新安排测试。",
                        diagnostic=dict(code=code, layer=diagnostic.get("layer"),
                                        fields=diagnostic.get("fields") or []))
                    self.coverage(summary)
                    checkpoint()
                    svc.db.transition(task_id, owner, "RUNNING", "NEEDS_REVIEW", summary=summary)
                    svc.db.event(task_id, "REPORT", run_id=report.run_id, requests=ledger.requests)
                    return
                status_states = {item['status'] for key in (
                    'http_status_requirements', 'response_requirements')
                    for item in summary.get(key, [])}
                summary["complete"] = bool(summary["cases"]) and all(
                    c.get("outcome") in {"PASS", "ASSERTION_FAILED"}
                    and c.get("executed_steps") == c.get("planned_steps")
                    for c in summary["cases"]
                ) and not summary["questions"] and not summary["not_covered"] and all(
                    g.get("status") in {"DESIGNED", "OUTSIDE_FOCUS"} for g in summary["groups"]
                ) and status_states <= {'PASS', 'ASSERTION_FAILED'} and all(
                    entry['status'] == 'EXECUTED' for entry in summary.get('data_references', []))
                summary["checks_passed"] = bool(summary["cases"]) and all(
                    c.get("outcome") == "PASS" for c in summary["cases"]
                ) and status_states <= {'PASS'}
                self.coverage(summary)
                checkpoint()
                svc.db.transition(task_id, owner, "RUNNING", "FINISHED", summary=summary)
                svc.db.event(task_id, "FINISHED", requests=ledger.requests)
        except asyncio.CancelledError:
            record_timing()
            self.stop(task_id, owner, summary, ledger,
                      "INTERRUPTED" if svc._shutting_down else "CANCELLED",
                      "已停止后续动作；已发送请求不自动回滚或重放。")
        except TimeoutError:
            record_timing()
            limit = summary.get("budget", {}).get("max_seconds", DEFAULT_TASK_SECONDS)
            self.stop(task_id, owner, summary, ledger, "INTERRUPTED",
                      f"本轮达到 {limit:g} 秒时限。")
        except Exception:
            record_timing()
            self.stop(task_id, owner, summary, ledger, "INTERRUPTED",
                      "执行条件发生变化或工具停止；保留已发生的证据，不重放请求。")
        finally:
            if transport is not None:
                await transport.aclose()

    async def run_group(self, scope, group, *, position=None, future_groups=(),
                        supervision=None):
        """Design, preflight and execute one group; the only design/execute action.

        Both the first deterministic round and a supervisor ``DESIGN_MORE`` round call this
        method, so extra tests reuse the same Planning IR/compiler, the same
        ``validate_design_case`` preflight and the same single ``Workflow``/``Executor``
        HTTP exit. ``supervision`` narrows the design scope to one open obligation; it never
        widens authority, skips validation or changes a terminal verdict.
        """
        svc, summary, ledger = scope.svc, scope.summary, scope.ledger
        project, project_record = scope.project, scope.project_record
        guard, checkpoint = scope.guard, scope.checkpoint
        task_id, row = scope.task_id, scope.row
        context, text = scope.context, scope.text
        bounded_transport, executable_checks = scope.transport, scope.executable_checks
        future_groups = list(future_groups)
        # A supervision round owns one obligation, not the round-1 clause map: those IR
        # instances and clause witnesses belong to groups that already ran. The round still
        # plans through Planning IR -- it receives the *scoped* representation of its own
        # obligation, so preconditions (delete -> 404) stay visible instead of being redesigned
        # from text. Dropping the IR here (P0) silently switched these rounds to the legacy
        # path, which is exactly the downgrade P1 forbids; if a scope cannot be built the
        # reason is recorded on the round instead of disappearing.
        planning_ir = scope.planning_ir
        supervision_downgrade = None
        if supervision is not None:
            planning_ir, supervision_downgrade = scoped_supervision_ir(
                scope.planning_ir, group)
        design_requirements = [] if supervision is not None else scope.requirements
        witness_requirements = design_requirements
        design_text = text if supervision is None else supervision["text"]
        proposal_context = context
        if supervision is not None:
            proposal_context = {**context, "current_request": design_text,
                "requests": [*(context.get("requests") or ()), design_text]}
        if supervision is not None:
            required_normal_operations = list(group["primary"])
        elif text == DEFAULT_ROUND or summary.get("focus", {}).get("mode") == "all_ready":
            required_normal_operations = project.target.allowed_operations
        else:
            required_normal_operations = summary.get("focus", {}).get(
                "normal_operation_ids", [])
        must_be_relevant = bool(supervision is not None or text == DEFAULT_ROUND
                                or summary.get("focus", {}).get("mode") in {"selected",
                                                                            "all_ready"})
        guard()
        if supervision is None:
            reserved_requests = (sum(len(g["primary"]) for g in future_groups
                                     if g["supported"]) if text == DEFAULT_ROUND else 0)
            reserved_requests = min(reserved_requests, max(0,
                ledger.max_requests - ledger.requests - len(group["primary"])))
        else:
            # A supervision round owns the remaining budget; nothing is reserved after it.
            reserved_requests = 0
        group_request_limit = max(ledger.requests,
            ledger.max_requests - reserved_requests)
        if (not group["supported"] or ledger.requests >= ledger.max_requests
                or ledger.model_calls >= ledger.max_model_calls
                or ledger.usage_unknown or svc.db.model_usage_blocked()):
            reason = ("本组依赖超过一次可处理的 12 个接口，需拆分测试范围。"
                if not group["supported"] else
                "本轮 HTTP 请求预算已用尽。"
                if ledger.requests >= ledger.max_requests else
                "本轮模型调用次数已达上限。"
                if ledger.model_calls >= ledger.max_model_calls else
                "此前模型调用的用量未知，本轮没有继续调用模型。")
            group.update(status="NOT_EXECUTED", reason=reason)
            checkpoint()
            return
        svc.db.event(task_id, "PLANNING", requests=ledger.requests)
        raw_model = svc.designer_factory(project)
        model = BudgetPlanner(JournalPlanner(raw_model, svc.db, task_id),
                              ledger, guard)
        record = dict(group_id=group["id"], status="PLANNING")
        suggested = None
        if supervision is None and text == DEFAULT_ROUND:
            future = [group, *future_groups]
            primary = sum(len(g["primary"]) for g in future)
            spare = max(0, ledger.max_requests - ledger.requests - primary)
            suggested = min(ledger.max_requests - ledger.requests,
                            len(group["primary"]) + spare // len(future))
            record["suggested_group_requests"] = suggested
        summary["design_records"].append(record)
        checkpoint()
        design_started = time.perf_counter()
        group_requirements = design_requirements
        group_witness_requirements = witness_requirements
        group_ir = None
        unresolved = unresolved_ir_clauses(
            planning_ir, summary.get("focus", {}).get("requirements", []))
        # A supervision round's coverage is the dependency closure it must realize (e.g. the
        # create/delete that establish the phase before a 404 read), not just the obligation's
        # own operations; otherwise the compiler would always look "partially covering".
        engine_group = group
        if supervision is not None and planning_ir is not None:
            engine_group = {**group, "primary": sorted({item.operation_id
                                                       for item in planning_ir.instances})}
        engine_conditions = planning_engine_conditions(
            planning_ir, group=engine_group, questions=summary.get("questions"),
            requested_checks=summary.get("requested_checks"),
            permission_requirements=summary.get("permission_requirements"),
            unresolved=unresolved)
        choice = planning_engine_choice(engine_conditions)
        record["planning_engine_choice"] = choice
        if supervision is not None:
            # Auditable: which round asked for this extra group and for which obligation.
            record["supervision_round"] = supervision["round"]
            record["supervision_target"] = supervision.get("target")
            record["supervision_planning_ir"] = supervision_downgrade
        if choice["engine"] == IR_ENGINE:
            # Focus turned status/data constraints into request obligations;
            # the Design model sees only real requests in this group, and the
            # deterministic compiler realizes them as instances. A group that
            # covers only part of the request keeps the unchanged legacy path.
            group_requirements = planning_ir.normalized_requirements
            group_witness_requirements = summary.get('focus', {}).get(
                'requirements', [])
            group_ir = planning_ir
        try:
            design = await propose_design(project, design_text, group, model,
                environ=svc.environ,
                remaining_requests=group_request_limit - ledger.requests,
                inventory_total=summary["inventory"]["total"],
                pending_checks=summary["inventory"].get("pending_checks", []),
                handled_questions=(summary["questions"]
                    if context.get("current_request", text) != DEFAULT_ROUND
                    else []),
                task_context=proposal_context,
                allow_new_questions="focus" not in summary,
                normal_operation_ids=required_normal_operations,
                input_requirements=summary.get("focus", {}).get(
                    "input_requirements", []),
                requested_checks=executable_checks,
                request_requirements=group_requirements,
                witness_requirements=group_witness_requirements,
                planning_ir=group_ir,
                permission_action_scope=summary.get('permission_action_scope'),
                data_references=summary.get('data_references', []),
                http_status_requirements=summary.get(
                    'http_status_requirements', []),
                response_requirements=summary.get('response_requirements', []),
                pending_negative_values=summary.get("pending_negative_values", []),
                project_notes=project_record.get("business_notes", ""),
                group_http_budget=suggested,
                allow_optional_negatives=(summary.get("focus", {}).get(
                    "mode") != "selected"),
                must_be_relevant=must_be_relevant,
                available_identity_aliases=[r["alias"] for r in svc.intake.record(
                    row["project_id"])["generic"]["roles"]],
                timeout=getattr(getattr(raw_model, "_settings", None),
                                "timeout", 30.0) + 1)
            record.update(status="PROPOSED", interpretation=design.interpretation,
                          presentation=presentation_language(
                              design.interpretation))
            if design._coalesced_read_cases:
                record['coalesced_read_cases'] = design._coalesced_read_cases
            if not design.relevant:
                record["outside_focus_notes"] = design.not_covered
            group["status"] = "DESIGNED" if design.relevant else "OUTSIDE_FOCUS"
        except (PlannerFailure, ValueError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None) or getattr(
                getattr(exc, "diagnostic", None), "code", "DESIGN_REJECTED")
            if exc.args and isinstance(exc.args[0], str) and exc.args[0] in {
                    "DESIGN_EMPTY_RESPONSE", "DESIGN_SELECTED_SCOPE_NOT_COVERED",
                    "IRRELEVANT_DESIGN_HAS_REQUESTED_WORK",
                    "DESIGN_OPERATION_OUTSIDE_GROUP", "DESIGN_STEP_BUDGET_EXCEEDED",
                    "DESIGN_QUESTION_OUTSIDE_GROUP",
                    "DESIGN_EXISTING_QUESTION_REF_INVALID",
                    "DESIGN_PERMISSION_QUESTION_REJECTED", "DRAFT_UNSAFE_METADATA",
                    "DESIGN_EXPLICIT_INPUT_NOT_PRESERVED",
                    "DESIGN_CHECK_BATCH_LIMIT", "DESIGN_DUPLICATE_RESOURCE_SETUP",
                    "DESIGN_UNAPPROVED_NEGATIVE_VALUE",
                    "DESIGN_REQUESTED_OPERATIONS_MISSING",
                    "DESIGN_REQUESTED_CHECKS_MISSING",
                    "DESIGN_REQUESTED_CHECK_NOT_APPROVED",
                    "DESIGN_EXPLICIT_INPUT_REQUIRES_SOURCE_REVIEW",
                    "DESIGN_ABSENCE_PRECONDITION_MISSING"}:
                reason = exc.args[0]
            diagnostic = getattr(exc, "diagnostic", None)
            if diagnostic is not None:
                record['diagnostic'] = diagnostic.model_dump(mode='json')
            if isinstance(exc, ValidationError):
                from agenttest.diagnostics import Layer, schema_diagnostic

                reason = "DESIGN_SCENARIO_INVALID"
                record["diagnostic"] = schema_diagnostic(
                    exc, Layer.DSL).model_dump(mode="json")
            if diagnostic and diagnostic.code == "DESIGN_REQUESTED_CHECKS_MISSING":
                reason = diagnostic.code
            if reason in {'DESIGN_REQUIRED_HTTP_STATUS_MISSING',
                          'DESIGN_REQUIRED_RESPONSE_WITNESS_MISSING'}:
                for entry in summary.get('http_status_requirements', []):
                    if f"requirement_map.{entry['source_id']}" in diagnostic.fields:
                        entry['status'] = 'NEEDS_REVIEW'
            record.update(status="REJECTED", reason=reason)
            group.update(status="NOT_EXECUTED", reason=reason)
            return
        finally:
            record["elapsed_ms"] = round(
                (time.perf_counter() - design_started) * 1000, 3)
            record["model_call"] = model.stats.model_dump(mode="json")
            if hasattr(raw_model, 'construction_records'):
                record['plan_construction'] = raw_model.construction_records
                record['construction_ms'] = raw_model.construction_elapsed_ms
                construction = list(raw_model.construction_records or [])
                record.update(planning_engine_evidence(
                    actual_planning_engine(construction), choice, construction))
            checkpoint()
        for question in design.questions:
            if question.existing_question_ref is not None:
                continue
            data = question.model_dump()
            data["id"] = "question_" + fingerprint(data)[:20]
            if data not in summary["questions"]:
                summary["questions"].append(data)
        if design.relevant:
            summary["not_covered"].extend(design.not_covered)
        summary.setdefault('suggestions', []).extend(design.suggestions)
        clarification_operations = {q.operation_id for q in design.questions
            if q.kind == "clarification" and q.existing_question_ref is None}
        negative_policies = {p.id for p in project.registry.definitions()
                             if p.negative}
        ordered_cases = sorted(design.cases, key=lambda c: not any(
        s.operation_id in group["primary"] and s.policy_id not in
        negative_policies for s in c.plan.steps)) \
        if supervision is None and text == DEFAULT_ROUND else design.cases
        # Validate the whole group before any login or business write.
        # Independent valid cases may run; rejected cases are never repaired.
        preflight = []
        for case in ordered_cases:
            try:
                preflight.append(validate_design_case(project, case, svc.reports,
                    cleanup_operations=group["primary"],
                    requested_checks=executable_checks,
                    environ=svc.environ, transport=bounded_transport))
            except ValueError as exc:
                preflight.append(exc)
        for index, case in enumerate(ordered_cases):
            case_row = dict(case_id=f"{group['id']}_case_{index + 1}",
                title=case.title, kinds=[case.kind], operation_id=None,
                planned_operations=list(dict.fromkeys(
                    s.operation_id for s in case.plan.steps)),
                planned_steps=len(case.plan.steps),
                planned_policy_ids=[s.policy_id for s in case.plan.steps],
                status="NOT_EXECUTED", max_business_requests=len(case.plan.steps),
                plan_source="MODEL_SUGGESTION", task_satisfied=None)
            if case._http_status_steps:
                case_row['http_status_steps'] = case._http_status_steps
            if case._response_steps:
                case_row['response_steps'] = case._response_steps
            if case._data_source_steps:
                case_row['data_source_steps'] = case._data_source_steps
            record_requirement_evidence(summary, case_row)
            summary["cases"].append(case_row)
            checkpoint()
            if any(s.operation_id in clarification_operations
                   and s.policy_id in negative_policies for s in case.plan.steps):
                case_row["reason"] = (
                    "本场景的异常输入或判断依据仍待补充，未发送请求。")
                continue
            if ledger.requests + len(case.plan.steps) > group_request_limit:
                case_row["reason"] = (
                    "保留后续接口的基本请求预算，未执行此额外场景。"
                    if reserved_requests else
                    "剩余 HTTP 预算不足，未执行此场景。")
                continue
            svc.db.event(task_id, "VALIDATING", requests=ledger.requests)
            try:
                scenario = preflight[index]
                if isinstance(scenario, ValueError):
                    raise scenario
            except ValueError as exc:
                case_row.update(status="REJECTED", reason=getattr(
                    getattr(exc, "diagnostic", None), "code", "PLAN_REJECTED"))
                continue
            # A supervision round exists to add information. If this plan is semantically the
            # same test as an earlier non-passing attempt, running it again would only repeat
            # the same writes and the same conclusion, so it is refused before any HTTP.
            from agenttest.product_fingerprint import (
                annotate_case,
                duplicate_attempt,
                duplicate_failure_reason,
                scenario_fingerprint,
            )
            from agenttest.product_preconditions import precondition_report

            precondition_audit = precondition_report(project, scenario)
            if supervision is not None:
                digest = scenario_fingerprint(project, scenario, precondition_audit)
                case_row["scenario_fingerprint"] = digest
                repeated = duplicate_attempt(summary["cases"][:-1], digest)
                if repeated is not None:
                    case_row.update(status="SKIPPED_DUPLICATE",
                                    duplicate_of=repeated["case_id"],
                                    reason=duplicate_failure_reason(repeated))
                    summary.setdefault("duplicate_retests", []).append(
                        dict(case_id=case_row["case_id"], duplicate_of=repeated["case_id"],
                             outcome=repeated["outcome"],
                             fingerprint=digest))
                    checkpoint()
                    continue
            from agenttest.investigation import FeedbackBudget, InvestigationConfig
            from agenttest.task_service import ProgressStore
            from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

            def progress(phase, run_id, _requests):
                if case_row.get("run_id") != run_id:
                    case_row.update(run_id=run_id, status="RUNNING")
                    checkpoint()
                svc.db.event(task_id, phase, run_id=run_id,
                             requests=ledger.requests)

            store = ProgressStore(svc.reports.root, progress)
            probe_remaining = max(0, 2 - sum(i.get("requests", 0)
                for previous in summary["cases"]
                for i in previous.get("investigations", [])))
            probe_allowed = (probe_remaining > 0 and ledger.requests
                             + len(scenario.steps) < group_request_limit)
            probe_model = (BudgetPlanner(JournalPlanner(
                svc.product_probe_factory(project), svc.db, task_id), ledger, guard)
                if probe_allowed and svc.product_probe_factory else None)
            case_started = time.perf_counter()
            try:
                report = await Workflow(project.document, project.target,
                    project.registry, FakeLLM(""), store,
                    transport=bounded_transport, environ=svc.environ,
                    task_mode="model_designed", user_task=design_text,
                    prepared_plan=scenario, analyzer=probe_model,
                    investigation=InvestigationConfig(enabled=probe_allowed,
                        product_read_probe=probe_allowed, feedback=FeedbackBudget(
                            max_requests=probe_remaining,
                            max_model_calls=probe_remaining)),
                    execution_guard=guard,
                    plan_derivation=dict(source="MODEL_DESIGN", group=group["id"],
                        model_request_sha256=model.stats.request_sha256),
                    budget=WorkflowBudget(max_model_calls=0,
                        workflow_timeout=min(300.,
                            project.target.run_timeout + 220))).run()
            finally:
                case_row["elapsed_ms"] = round(
                    (time.perf_counter() - case_started) * 1000, 3)
            # Situation 2 of the absence-expectation rule: if the approved deletion never
            # established "deleted", a failing 404 check is a plan/state problem, not an
            # independent business defect. Only a satisfied precondition keeps it as
            # ASSERTION_FAILED evidence; nothing is ever upgraded to PASS.
            from agenttest.product_preconditions import reclassify_unestablished

            precondition_changes = reclassify_unestablished(report, scenario, project)
            if precondition_changes:
                directions = {item["direction"] for item in precondition_changes}
                reasons = {item["reason"] for item in precondition_changes}
                plan_induced = reasons == {"PRECONDITION_NOT_ESTABLISHED"}
                report.outcome = aggregate(report.steps)
                report.reason = ("PRECONDITION_NOT_ESTABLISHED" if plan_induced
                                 else "PRECONDITION_UNPROVEN")
                report.failure_explanation = dict(
                    mismatch=("删除前置条件未成立，因此未把后续读取判为业务缺陷。"
                              if plan_induced else
                              "前置条件无法证明，因此未把响应差异判为业务缺陷。"),
                    evidence=[item["step_id"] for item in precondition_changes],
                    additional_checks=[], confirmed_observation=(
                        "响应与预期不符，但计划没有证明该资源在请求时处于预期状态。"),
                    cause_confirmed=False,
                    uncertainty="前置条件未成立或无法证明时，不能据此认定服务存在缺陷。",
                    precondition=dict(changes=precondition_changes,
                                      directions=sorted(directions),
                                      rule="an expectation needs its resource phase: an absent "
                                           "claim needs an approved deletion of the same "
                                           "resource, and an exists claim needs the plan not "
                                           "to have deleted it"))
                store.save(report)
                case_row["precondition_changes"] = precondition_changes
                case_row["precondition_rule"] = "PHASE_MUST_BE_ESTABLISHED"
            if (report.steps and report.outcome != Outcome.PASS
                    and report.failure_explanation is None):
                from agenttest.product_probe import explain_failure

                report.failure_explanation = explain_failure(report)
                store.save(report)
            plan_roles = {p.step_id: p.role for p in report.plan_structure}
            plan_policies = {p.step_id: p.policy_id for p in report.plan_structure}
            case_row.update(status="FINISHED", run_id=report.run_id,
                outcome=report.outcome.value, task_satisfied=None,
                planned_steps=len(scenario.steps),
                executed_steps=sum(bool(s.attempts) for s in report.steps),
                executed_operations=[s.operation_id for s in report.steps
                                     if s.attempts],
                http_requests=sum(len(s.attempts) for s in report.steps),
                resource_lifecycle=report.resource_lifecycle,
                created_resources=report.created_resources,
                cleanup=report.cleanup,
                failure_explanation=report.failure_explanation,
                probe_decisions=report.probe_decisions,
                cleanup_requests=sum(len(s.attempts)
                    for s in report.lifecycle_cleanup),
                approved_negative_steps=sum(
                    s.input_validation == "APPROVED_NEGATIVE"
                    for s in report.steps),
                negative_rejections_passed=sum(
                    s.negative_rejected_as_expected is True for s in report.steps),
                investigations=[dict(operation_id=i.operation_id,
                    role=i.role,
                    method=i.method, path=i.path_template, outcome=i.outcome.value,
                    requests=i.requests_used, reason=i.reason,
                    checks=[c.model_dump() for c in i.checks],
                    cause_confirmed=False) for i in report.investigations],
                evidence=[dict(operation_id=s.operation_id, method=s.method,
                    step_id=s.step_id, role=plan_roles.get(s.step_id),
                    policy_id=plan_policies.get(s.step_id),
                    executed=bool(s.attempts),
                    path=s.path_template, outcome=s.outcome.value,
                    request=s.request_evidence,
                    checks=[c.model_dump() for c in s.checks])
                    for s in report.steps],
                # A lifecycle cleanup the executor really ran is execution evidence for its
                # own approved policy; it is kept apart from the scenario steps.
                cleanup_evidence=[dict(operation_id=s.operation_id, method=s.method,
                    step_id=s.step_id, role=plan_roles.get(s.step_id),
                    policy_id=plan_policies.get(s.step_id),
                    executed=bool(s.attempts), cleanup=True,
                    path=s.path_template, outcome=s.outcome.value,
                    request=s.request_evidence,
                    checks=[c.model_dump() for c in s.checks])
                    for s in report.lifecycle_cleanup])
            from agenttest.product_checks import executed_policies

            case_row["executed_policy_ids"] = executed_policies(report)
            # Record what was actually tested so a later round can refuse an equivalent retest
            # without a model's opinion about whether the two plans "look the same".
            annotate_case(case_row, project, scenario, precondition_audit, report)
            if precondition_audit["expectations"]:
                # Evidence, not a verdict: which absence expectations this case carried and
                # whether an approved deletion established each one.
                case_row["absence_expectations"] = precondition_audit["rows"]
            record_requirement_evidence(summary, case_row, report)
            if report.outcome != Outcome.PASS:
                case_row["reason"] = {
                    Outcome.ASSERTION_FAILED: "响应不符合已确认的预期。",
                    Outcome.TEST_INFRA_ERROR:
                        "环境或传输异常，不能据此认定业务缺陷。",
                    Outcome.TOOL_ERROR: "计划或执行参数未能通过校验。",
                    Outcome.NEEDS_REVIEW: "缺少可靠的执行或判断依据。",
                }[report.outcome]
                case_row["diagnostic"] = (report.diagnostic.code
                    if report.diagnostic else report.reason)
            if report.outcome == Outcome.PASS and report.completed:
                self.retain(row, design_text, scenario, project, report, case_row)
            checkpoint()

    async def supervise(self, scope):
        """Let the cyclic LangGraph supervisor decide whether one more bounded action is due.

        This is the only place where a model chooses the next business action. The choice
        cannot widen authority, skip validation or write a verdict: every action returns
        through ``run_group``/``Workflow`` and the terminal ``complete``/``checks_passed``
        values stay in ``run()``. With ``supervisor_max_rounds == 0`` (the product default)
        this method returns immediately and the round behaves exactly as before.
        """
        svc, summary, ledger, project = scope.svc, scope.summary, scope.ledger, scope.project
        rounds = int(getattr(project.registration.budget, "supervisor_max_rounds", 0) or 0)
        factory = getattr(svc, "supervisor_factory", None)
        if rounds < 1 or factory is None:
            return None
        # A base install has no langgraph; only the enabled path may need it.
        from agenttest.product_obligations import supervision_facts
        from agenttest.product_supervisor import RoundSupervisor

        allow_negatives = bool(scope.text == DEFAULT_ROUND
                               or summary.get("focus", {}).get("mode") != "selected")

        def facts():
            attempts = list(scope.state.get("attempts") or ())
            return supervision_facts(project=project, summary=summary, text=scope.text,
                remaining=dict(requests=max(0, ledger.max_requests - ledger.requests),
                               model_calls=max(0, ledger.max_model_calls - ledger.model_calls),
                               rounds=rounds, usage_known=not ledger.usage_unknown),
                allow_negatives=allow_negatives, attempts=attempts,
                attempted=[item.get("target") for item in attempts if item.get("target")])

        supervisor = RoundSupervisor(BudgetPlanner(JournalPlanner(
                factory(project), svc.db, scope.task_id), ledger, scope.guard), facts,
            design_and_execute=lambda candidate: self.supervise_design(scope, candidate),
            read_probe=lambda candidate: self.supervise_read(scope, candidate),
            ask=lambda candidate, question, reason: self.supervise_ask(scope, candidate,
                                                                      question, reason),
            guard=scope.guard, max_rounds=rounds,
            timeout=max(30.0, min(240.0, project.target.run_timeout + 120.0)),
            secret_in=svc.private_input_guard,
            cancelled=lambda: bool(getattr(svc, "_shutting_down", False)))
        svc.db.event(scope.task_id, "SUPERVISING", requests=ledger.requests)
        try:
            outcome = await supervisor.run()
        except asyncio.CancelledError:
            summary["supervision"] = self.supervision_payload(
                supervisor.interrupted_outcome(), scope)
            raise
        summary["supervision"] = self.supervision_payload(outcome, scope)
        scope.checkpoint()
        return outcome

    @staticmethod
    def supervision_payload(outcome, scope):
        """Report-safe record: the real graph shape, decisions, actions and probe evidence."""
        payload = outcome.public()
        payload["trace"] = list(outcome.trace)
        payload["probes"] = list(scope.state.get("probes") or ())
        return payload

    @staticmethod
    def next_supervision_round(scope):
        scope.state["round"] = int(scope.state.get("round") or 0) + 1
        return scope.state["round"]

    @staticmethod
    def round_evidence(summary):
        """Measured counters only: a design record is never counted as an executed request."""
        cases = summary.get("cases") or ()
        return dict(cases=len(cases),
                    requests=sum(case.get("http_requests") or 0 for case in cases),
                    assertions=sum(len(step.get("checks") or ())
                                   for case in cases for step in case.get("evidence") or ()
                                   if step.get("executed")))

    @staticmethod
    def supervision_text(scope, candidate):
        """Server-composed scope note; it names one approved obligation, never a new rule."""
        operations = "、".join(candidate.get("operations") or ())
        why = candidate.get("why") or "该义务仍缺少执行证据。"
        note = f"\n[本轮只补测一个已批准的检查] 目标接口：{operations}；原因：{why}"
        if candidate.get("cleanup_policies"):
            note += "如果该检查会创建资源，必须在同一场景中按已批准的删除策略清理。"
        return f"{scope.text}{note}"[:2000]

    async def supervise_design(self, scope, candidate):
        """DESIGN_MORE: one new group for one open obligation, through the normal chain."""
        from agenttest.product_design import design_groups
        from agenttest.product_supervisor import ActionOutcome

        summary, project = scope.summary, scope.project
        operations = [operation for operation in candidate.get("operations") or ()
                      if operation in project.target.allowed_operations]
        groups = [group for group in design_groups(project, operation_ids=operations)
                  if set(operations) & set(group["primary"])]
        if not groups:
            return ActionOutcome(executed=False, note="SUPERVISION_TARGET_NOT_GROUPABLE",
                                 detail=dict(target=candidate.get("id")))
        round_number = self.next_supervision_round(scope)
        group = dict(groups[0])
        group["id"] = f"supervision_{round_number}_{group['id']}"
        group["status"] = "PENDING"
        summary["groups"].append(group)
        attempt = dict(round=round_number, action="DESIGN_MORE", target=candidate.get("id"),
                       operations=operations, policies=list(candidate.get("policies") or ()),
                       group_id=group["id"], status="PENDING")
        scope.state.setdefault("attempts", []).append(attempt)
        before = self.round_evidence(summary)
        await self.run_group(scope, group, supervision=dict(round=round_number,
            target=candidate.get("id"), text=self.supervision_text(scope, candidate)))
        after = self.round_evidence(summary)
        status = group.get("status")
        attempt.update(status=status, cases=after["cases"] - before["cases"],
                       requests=after["requests"] - before["requests"],
                       assertions=after["assertions"] - before["assertions"])
        executed = status in {"DESIGNED", "OUTSIDE_FOCUS"}
        return ActionOutcome(executed=executed, cases=attempt["cases"],
            requests=attempt["requests"], assertions=attempt["assertions"],
            detail=dict(group_id=group["id"], status=status, operations=operations,
                        reason=(group.get("reason") or "")[:160] or None,
                        target=candidate.get("id")),
            note=None if executed else "SUPERVISION_GROUP_NOT_DESIGNED")

    async def supervise_read(self, scope, candidate):
        """READ_PROBE: one approved read-only step; it can never write or replay a write."""
        from agenttest.dsl import validate_plan
        from agenttest.executor import Executor
        from agenttest.investigation import InvestigationConfig
        from agenttest.product_design import validate_cleanup_dependencies
        from agenttest.product_obligations import probe_scenario
        from agenttest.product_supervisor import ActionOutcome
        from agenttest.task_service import ProgressStore
        from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

        svc, project, ledger = scope.svc, scope.project, scope.ledger
        round_number = self.next_supervision_round(scope)
        attempt = dict(round=round_number, action="READ_PROBE", target=candidate.get("id"),
                       operations=list(candidate.get("operations") or ()), status="PENDING")
        scope.state.setdefault("attempts", []).append(attempt)
        try:
            scenario = probe_scenario(project, candidate, round_number)
            validate_plan(scenario, project.contract, project.target, svc.environ)
            store = ProgressStore(svc.reports.root, lambda phase, run_id, _requests:
                                  svc.db.event(scope.task_id, phase, run_id=run_id,
                                               requests=ledger.requests))
            Executor(project.target, project.contract, store, scope.transport, svc.environ,
                     registry=project.registry).validate(scenario)
            validate_cleanup_dependencies(project, scenario, ())
        except (PlannerFailure, ValueError, TimeoutError) as exc:
            attempt.update(status="REJECTED",
                           reason=getattr(getattr(exc, "diagnostic", None), "code", None)
                           or str(exc)[:120])
            return ActionOutcome(executed=False, note="SUPERVISION_PROBE_REJECTED",
                                 detail=dict(target=candidate.get("id"),
                                             reason=attempt["reason"]))
        step = scenario.steps[0]
        report = await Workflow(project.document, project.target, project.registry,
            FakeLLM(""), store, transport=scope.transport, environ=svc.environ,
            task_mode="model_designed", user_task=self.supervision_text(scope, candidate),
            prepared_plan=scenario, investigation=InvestigationConfig(enabled=False),
            execution_guard=scope.guard,
            plan_derivation=dict(source="SUPERVISION_READ_PROBE", group=step.operation_id),
            budget=WorkflowBudget(max_model_calls=0,
                workflow_timeout=min(300.0, project.target.run_timeout + 220.0))).run()
        requests = sum(len(item.attempts) for item in report.steps)
        checks = [dict(kind=check.kind, selector=check.selector, expected=check.expected,
                       actual=check.actual, passed=check.passed)
                  for item in report.steps for check in item.checks][:6]
        scope.state.setdefault("probes", []).append(dict(round=round_number,
            target=candidate.get("id"), run_id=report.run_id, operation_id=step.operation_id,
            policy_id=step.policy_id, outcome=report.outcome.value, requests=requests,
            checks=checks, reason=(report.reason or "")[:160] or None))
        attempt.update(status="EXECUTED", run_id=report.run_id,
                       outcome=report.outcome.value, requests=requests)
        executed = bool(report.steps)
        return ActionOutcome(executed=executed, requests=requests, assertions=len(checks),
            detail=dict(run_id=report.run_id, operation_id=step.operation_id,
                        outcome=report.outcome.value, target=candidate.get("id")),
            note=None if executed else "SUPERVISION_PROBE_NOT_EXECUTED")

    def supervise_ask(self, scope, candidate, question, reason):
        """ASK_QUESTION reuses the pending-question channel; no expectation is invented."""
        from agenttest.product_supervisor import question_record

        record = question_record(candidate, question, reason)
        scope.summary["questions"].append(record)
        scope.state.setdefault("attempts", []).append(dict(
            round=int(scope.state.get("round") or 0), action="ASK_QUESTION",
            target=(candidate or {}).get("id"), question=record["id"], status="ASKED"))
        return record

    def failure_report(self, project, text, code, diagnostic, records=()):
        """Publish a no-step failure report so a rejected round still has auditable evidence."""
        from agenttest.diagnostics import Diagnostic
        from agenttest.report import RunReport, fingerprint

        payload = Diagnostic.model_validate(diagnostic) if diagnostic else None
        calls, usage = aggregate_usage(records)
        report = RunReport(
            scenario_sha256=fingerprint({"task": text, "design": "REJECTED"}),
            contract_sha256=fingerprint(project.document),
            environment=project.target.environment,
            target_origin=project.target.base_url,
            target_config_sha256=fingerprint(project.target.model_dump(mode="json")),
            policy_registry_sha256=project.registry.digest(),
            outcome=Outcome.NEEDS_REVIEW, reason=code, diagnostic=payload,
            task_mode="model_designed", plan_valid=False, dsl_valid=False,
            policy_authorized=False, planner_calls=calls, model_call=usage)
        self.service.reports.save(report)
        return report

    def retain(self, row, text, scenario, project, report, case_row):
        """Persist export-safe plans; the user still explicitly chooses to save a test."""
        from agenttest.regression import RegressionRuntime, _validated

        snapshot = FixedPlanSnapshot(user_task=text, source_run_id=report.run_id,
            scenario_sha256=report.scenario_sha256, contract_sha256=report.contract_sha256,
            target_config_sha256=report.target_config_sha256,
            policy_sha256=project.registry.digest())
        svc = self.service
        try:
            _validated(RegressionRuntime(project.document, project.target, project.registry,
                snapshot, svc.environ, None, svc.reports), scenario)
        except ValueError as exc:
            case_row["save_reason"] = {
                "REGRESSION_SENSITIVE_DATA":
                    "场景含受保护的输入字段，不能写入固定回归包；凭据请使用服务端变量。",
                "REGRESSION_JSON_SCOPE_REQUIRED":
                    "场景含上传、轮询或读取重试，当前固定回归尚未支持这些执行参数。",
            }.get(str(exc), "固定回归校验未通过，请检查本轮记录及已确认的输入与规则。")
            return
        svc._assets[(row["id"], report.run_id)] = (scenario, snapshot, project)
        svc.db.save_product_asset(report.run_id, row["id"], project.identity(), dict(
            scenario=scenario.model_dump(), snapshot=snapshot.model_dump()))

    @staticmethod
    def note_unready(project, summary, identifiers, *, ask_unauthorized=True):
        """Keep known intake gaps visible without generating permissions or another model call."""
        entries = {op["operation_id"]: op for op in summary["inventory"]["operations"]}
        operations = {op.operation_id: op for op in project.contract.operations}
        for key in identifiers:
            entry, operation = entries[key], operations[key]
            label = operation.summary or operation.method + " " + operation.path
            if entry["state"] == "NEEDS_INPUT" or (
                    ask_unauthorized and entry["state"] == "UNAUTHORIZED"):
                next_step = entry["next_step"].removeprefix("请").rstrip("。")
                summary["questions"].append(dict(id="focus_" + fingerprint([key, entry])[:20],
                    operation_id=key, question=f"继续检查“{label}”前，能否先{next_step}？",
                    requested_behavior=entry["reason"], kind="clarification",
                    role=None, status_choices=[]))
            else:
                summary["not_covered"].append(f"{label}：{entry['reason']} {entry['next_step']}")

    @staticmethod
    def coverage(summary):
        cases = summary["cases"]
        executed = {o for c in cases for o in c.get("executed_operations", [])}
        summary["coverage"] = dict(operation_denominator=summary["inventory"]["total"],
            scenario_denominator=len(cases), executed_operations=len(executed),
            executed_scenarios=sum(bool(c.get("http_requests")) for c in cases),
            started_scenarios=sum(bool(c.get("http_requests")) for c in cases),
            completed_scenarios=sum(c.get("status") == "FINISHED"
                and c.get("outcome") in {"PASS", "ASSERTION_FAILED"}
                and c.get("planned_steps", 0) > 0
                and c.get("executed_steps") == c.get("planned_steps") for c in cases),
            assertion_passed_scenarios=sum(c.get("outcome") == "PASS" for c in cases))
        design_records = summary.get("design_records") or []
        engines = [record.get("planning_engine") for record in design_records
                   if record.get("planning_engine")]
        if not engines:
            engine = NOT_EXECUTED_ENGINE
        elif set(engines) == {IR_ENGINE}:
            engine = IR_ENGINE
        elif set(engines) == {LEGACY_ENGINE}:
            engine = LEGACY_ENGINE
        elif set(engines) == {NOT_EXECUTED_ENGINE}:
            engine = NOT_EXECUTED_ENGINE
        else:
            engine = "MIXED"
        reasons = [record.get("planning_engine_reason") for record in design_records
                   if record.get("planning_engine_reason")]
        summary.update(
            planning_engine=engine,
            planning_engine_reason=("IR_SELECTED" if engine == IR_ENGINE else
                                    "MIXED_COMPILER_PATHS" if engine == "MIXED" else
                                    reasons[0] if reasons else "NO_DESIGN_RECORD"),
            planning_engine_groups=[dict(
                group_id=record.get("group_id"), engine=record.get("planning_engine"),
                choice=record.get("planning_engine_choice"),
                choice_reason=record.get("planning_engine_choice_reason"),
                conditions=record.get("planning_engine_conditions"),
                instances=record.get("planning_instance_count"),
                mapping_complete=record.get("planning_instance_mapping_complete"),
                dependency_edges=record.get("planning_dependency_edges"))
                for record in design_records
                if record.get("planning_engine_choice") or record.get("planning_engine")],
            planning_instance_count=max((record.get("planning_instance_count") or 0
                                         for record in design_records), default=0),
            planning_instance_mapping_complete=(all(
                record.get("planning_instance_mapping_complete") is True
                for record in design_records if record.get("planning_engine") == IR_ENGINE)
                if any(record.get("planning_engine") == IR_ENGINE for record in design_records)
                else None),
            planning_engine_conditions=next(
                (record.get("planning_engine_conditions") for record in design_records
                 if record.get("planning_engine_conditions")), None),
            planning_dependency_edges=sum(record.get("planning_dependency_edges") or 0
                                          for record in design_records))
        required = {r["policy_id"] for r in summary.get("requested_checks", [])}
        checked = {key for c in cases for key in c.get("executed_policy_ids", [])}
        summary["coverage"].update(requested_check_denominator=len(required),
                                   executed_requested_checks=len(required & checked))
        if "negative_check_catalog" in summary:
            catalog = summary["negative_check_catalog"]
            available = {item["policy_id"] for item in catalog}
            planned = required | {key for case in cases
                                  for key in case.get("planned_policy_ids", [])}
            summary["coverage"].update(approved_check_denominator=len(available),
                attempted_approved_checks=len(available & checked))
            summary["uncovered_negative_checks"] = [dict(item,
                reason="本轮已要求或安排，但没有实际请求；请查看对应的等待或停止原因。"
                    if item["policy_id"] in planned else "本轮未安排，可在下一轮指定这项检查。")
                for item in catalog if item["policy_id"] not in checked]
        reasons = {op: "本次输入取值尚有歧义，等待补充；依赖该输入的请求没有发送。"
                   for op in summary.get("input_blocked_operations", [])}
        reasons.update({op: "本次指定了已有资源，当前配置要求使用前置接口产生的数据；"
                            "相关请求暂停，请补充允许的数据来源。"
                        for op in summary.get("source_blocked_operations", [])})
        reasons.update({op: "原文指定的响应检查缺少已确认的规则；该读取暂停。"
                        for item in summary.get('response_requirements', [])
                        for op in item.get('blocked_operations', [])})
        reasons.update({op: "原文指定的 HTTP 状态码缺少已确认的断言；该读取暂停。"
                        for item in summary.get('http_status_requirements', [])
                        for op in item.get('blocked_operations', [])})
        reasons.update({op: "原文指定的数据来源与已批准的接口、身份或字段绑定不一致；相关读取暂停。"
                        for item in summary.get('data_references', [])
                        for op in item.get('blocked_operations', [])})
        reasons.update({item['operation_id']: "该原文分项的身份权限预期待确认；不能用其他身份代替。"
                        for item in summary.get('permission_requirements', [])
                        if not summary.get('permission_action_scope', {}).get(
                            item['operation_id'])})
        messages = dict(MODEL_TIMEOUT="模型生成方案时超时，此组没有执行。",
            MODEL_INVALID_RESPONSE="模型方案未通过格式校验，此组没有执行。",
            MODEL_TOKEN_BUDGET="本组资料超过模型输入预算，未发送模型或业务请求。",
            MODEL_SERVICE_ERROR="模型服务暂不可用，此组没有执行。",
            MODEL_API_ERROR="模型服务拒绝请求，此组没有执行。",
            DESIGN_EMPTY_RESPONSE="模型没有提出可执行方案，此组没有执行。",
            DESIGN_DUPLICATE_RESOURCE_SETUP="方案在不同普通场景中重复创建同样的资源，此组未执行；请重新安排独立测试。",
            DESIGN_NEGATIVE_UNNEEDED_WRITE=(
                "异常场景包含检查不依赖的额外写入，此组未执行；请移除独立写入，只保留必要准备和清理。"),
            DESIGN_UNSCOPED_QUESTION=(
                "模型新增的问题没有本轮需求来源，此组没有执行；已有待补问题仍保留。"),
            DESIGN_SCENARIO_INVALID="模型场景未满足执行结构要求，此组没有执行。",
            DESIGN_REQUESTED_OPERATIONS_MISSING="模型方案遗漏了指定接口，此组没有执行。",
            DESIGN_NORMAL_REQUEST_MISSING="模型遗漏了要求中的正常请求，此组没有执行。",
            DESIGN_NEGATIVE_CHECK_MISSING="模型标为异常的场景没有实际异常检查，此组没有执行。",
            DESIGN_NEGATIVE_CASE_OUTSIDE_REQUIRED=(
                "模型在普通场景中混入异常方案，此组没有执行。"),
            DESIGN_DUPLICATE_READ_SCENARIO="模型重复安排了相同的只读场景，此组没有执行。",
            DESIGN_REQUIRED_READ_COUNT_MISMATCH="方案没有保留原文指定的读取次数，此组没有执行。",
            DESIGN_REQUIRED_READ_INPUT_CHANGED="重复读取的策略或输入不一致，此组没有执行。",
            DESIGN_REQUESTED_CHECKS_MISSING="模型方案遗漏了明确指定的异常检查，此组没有执行。",
            DESIGN_CHECK_BATCH_LIMIT="本组指定的异常检查超过12项，请分批安排；此组没有执行。",
            DESIGN_REQUESTED_CHECK_NOT_APPROVED="指定检查没有对应的已批准能力，此组没有执行。",
            DESIGN_EXPLICIT_INPUT_NOT_PRESERVED="模型方案没有保留本次指定的输入，此组没有执行。",
            DESIGN_EXPLICIT_INPUT_REQUIRES_SOURCE_REVIEW=(
                "本次指定了已有资源，但项目配置要求使用前置接口产生的数据。"
                "请先核对允许的数据来源，此组没有发送请求。"))
        for group in summary.get("groups", []):
            if group.get("status") == "NOT_EXECUTED":
                reason = group.get("reason", "本组尚未形成可执行方案。")
                public = messages.get(reason, "模型方案未通过本组校验，此组没有执行。"
                    if reason.isascii() and reason.replace("_", "").isalnum() else reason)
                for operation in group["primary"]:
                    reasons[operation] = public
        for case in cases:
            if case["status"] in {"REJECTED", "NOT_EXECUTED"}:
                for operation in case.get("planned_operations", []):
                    reasons.setdefault(operation, "模型方案未通过执行校验，相关请求没有发送。"
                        if case["status"] == "REJECTED" else
                        case.get("reason", "场景未执行，请核对本轮预算与停止原因。"))
            elif (case["status"] in {"CANCELLED", "INTERRUPTED"}
                  or case["status"] == "FINISHED"
                  and case.get("executed_steps", 0) < case.get("planned_steps", 0)):
                reason = ("场景已取消，后续计划请求没有发送。" if case["status"] == "CANCELLED"
                    else "场景执行中断，后续计划请求没有发送。"
                    if case["status"] == "INTERRUPTED" else
                    "场景在前序步骤失败后停止，此接口虽已安排，但请求没有发送。")
                for operation in case.get("planned_operations", []):
                    reasons.setdefault(operation, reason)
        requested = {op for group in summary.get("groups", []) for op in group["primary"]}
        summary["untested_operations"] = [dict(o, reason=o["reason"] if o["state"] != "READY"
            else reasons.get(o["operation_id"], "本组方案没有覆盖此接口，未发送请求。"
                if o["operation_id"] in requested else "本次重点未包含此接口，未发送请求。"))
            for o in summary["inventory"]["operations"] if o["operation_id"] not in executed]

    def stop(self, task_id, owner, summary, ledger, status, reason):
        svc = self.service
        row = svc.db.get(task_id, owner)
        if row["status"] not in TERMINAL:
            for case in summary["cases"]:
                if case.get("run_id") and case["status"] == "RUNNING":
                    try:
                        report = svc.reports.read(case["run_id"])
                    except (ValueError, OSError):
                        case.update(status=status, reason="已停止，部分执行证据暂时无法读取。")
                        continue
                    case.update(status=status, outcome=report.outcome.value,
                        reason="已停止后续动作，以下保留已发送请求的证据。",
                        executed_operations=[s.operation_id for s in report.steps if s.attempts],
                        http_requests=sum(len(s.attempts) for s in report.steps),
                        resource_lifecycle=report.resource_lifecycle,
                        created_resources=report.created_resources, cleanup=report.cleanup,
                        executed_steps=sum(bool(s.attempts) for s in report.steps),
                        evidence=[dict(operation_id=s.operation_id, method=s.method,
                            path=s.path_template, outcome=s.outcome.value,
                            request=s.request_evidence,
                            checks=[c.model_dump() for c in s.checks]) for s in report.steps])
                    from agenttest.product_checks import executed_policies

                    case["executed_policy_ids"] = executed_policies(report)
                    record_requirement_evidence(summary, case, report)
            summary.update(reason=reason, complete=False, context_usage=ledger.summary(),
                           sent_requests_may_have_completed=ledger.requests > 0)
            self.coverage(summary)
            svc.db.transition(task_id, owner, row["status"], status, summary=summary)
            svc.db.event(task_id, status, requests=ledger.requests)
