"""Product orchestration around the existing single Workflow and deterministic Executor."""

import asyncio
from uuid import uuid4

import httpx
from pydantic import ValidationError

from agenttest.credentials import CredentialVault, sensitive_field
from agenttest.fixed_plan import FixedPlanSnapshot
from agenttest.goal_suite import BudgetPlanner, BudgetTransport, SuiteLedger
from agenttest.llm_planner import PlannerFailure
from agenttest.product_design import design_groups, propose_design, validate_design_case
from agenttest.report import ModelCallStats, Outcome, fingerprint
from agenttest.task_store import TERMINAL, TaskConflict

DEFAULT_ROUND = "安排一轮测试，优先正常请求、参数边界、已批准的身份检查与跨接口流程，并说明遗漏。"
DEFAULT_FOLLOWUP = "沿用上次测试重点，按最新已确认的信息安排尚未完成的检查。"


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
            max_steps_per_case=project.target.max_steps, max_seconds=600)
        summary = dict(mode="MODEL_DESIGNED", display_title="一轮接口测试", user_request=text,
            scope="project", inventory=inventory, budget=budget, cases=[], design_records=[],
            questions=[], not_covered=[], groups=design_groups(project),
            model_source=svc.model_status(), execution_target="AUTHORIZED_TEST_ENVIRONMENT",
            complete=False, task_satisfied=None)
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

        def checkpoint():
            summary["context_usage"] = ledger.summary()
            svc.db.transition(task_id, owner, "RUNNING", "RUNNING", summary=summary)
            svc.db.save_context(task_id, owner, {**context, "usage": ledger.summary()})

        try:
            async with svc._serial:
                row = svc.db.transition(task_id, owner, "QUEUED", "RUNNING")
                project = svc.project(row["project_id"])
                project_record = svc.intake.record(row["project_id"])

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
                        from agenttest.product_focus import select_focus

                        raw_focus = svc.focus_factory(project)
                        focus_model = BudgetPlanner(JournalPlanner(raw_focus, svc.db, task_id),
                                                    ledger, guard)
                        focus_record = dict(group_id="focus", status="PLANNING")
                        summary["design_records"].append(focus_record)
                        svc.db.event(task_id, "PLANNING", requests=ledger.requests)
                        checkpoint()
                        selection = None
                        try:
                            selection = await select_focus(project, focus_model, context,
                                environ=svc.environ, notes=project_record.get("business_notes", ""),
                                inventory=summary["inventory"],
                                available_identity_aliases=[r["alias"] for r in
                                    project_record.get("generic", {}).get("roles", [])],
                                timeout=getattr(getattr(raw_focus, "_settings", None),
                                                "timeout", 90.0) + 1)
                            summary["focus"] = selection.model_dump()
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
                            blocked_inputs = input_blocked_operations(project,
                                input_questions, operation_ids=selection.source_conflicts)
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
                            focus_record.update(status="REJECTED", reason=getattr(
                                exc, "reason", None) or "FOCUS_NOT_VALIDATED")
                        finally:
                            focus_record["model_call"] = focus_model.stats.model_dump(mode="json")
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
                                    "FOCUS_INPUT_TEXT_CHANGED": "模型提取的文字没有保留原文，"
                                        "本次没有发送业务请求。请核对需求后安排新的独立测试。",
                                    "FOCUS_INPUT_NUMBER_CHANGED": "模型提取的数字与原文不一致，"
                                        "本次没有发送业务请求。请核对需求后安排新的独立测试。",
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
                        selected_ids = (selection.operation_ids if selection.mode == "selected"
                                        else project.target.allowed_operations)
                        summary["groups"] = design_groups(project, operation_ids=[
                            op for op in selected_ids if op not in blocked_inputs])
                        checkpoint()
                    for position, group in enumerate(summary["groups"]):
                        guard()
                        reserved_requests = (sum(len(g["primary"]) for g in
                            summary["groups"][position + 1:] if g["supported"])
                            if text == DEFAULT_ROUND else 0)
                        reserved_requests = min(reserved_requests, max(0,
                            ledger.max_requests - ledger.requests - len(group["primary"])))
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
                            continue
                        svc.db.event(task_id, "PLANNING", requests=ledger.requests)
                        raw_model = svc.designer_factory(project)
                        model = BudgetPlanner(JournalPlanner(raw_model, svc.db, task_id),
                                              ledger, guard)
                        record = dict(group_id=group["id"], status="PLANNING")
                        suggested = None
                        if text == DEFAULT_ROUND:
                            future = summary["groups"][position:]
                            primary = sum(len(g["primary"]) for g in future)
                            extra = max(0, ledger.max_requests - ledger.requests - primary)
                            suggested = min(ledger.max_requests - ledger.requests,
                                            len(group["primary"]) + extra // len(future))
                            record["suggested_group_requests"] = suggested
                        summary["design_records"].append(record)
                        checkpoint()
                        try:
                            design = await propose_design(project, text, group, model,
                                environ=svc.environ,
                                remaining_requests=group_request_limit - ledger.requests,
                                inventory_total=summary["inventory"]["total"],
                                pending_checks=summary["inventory"].get("pending_checks", []),
                                handled_questions=(summary["questions"]
                                    if context.get("current_request", text) != DEFAULT_ROUND
                                    else []),
                                task_context=context,
                                allow_new_questions="focus" not in summary,
                                normal_operation_ids=(project.target.allowed_operations
                                    if text == DEFAULT_ROUND or summary.get("focus", {}).get(
                                        "mode") == "all_ready" else summary.get("focus", {}).get(
                                        "normal_operation_ids", [])),
                                input_requirements=summary.get("focus", {}).get(
                                    "input_requirements", []),
                                requested_checks=executable_checks,
                                pending_negative_values=summary.get("pending_negative_values", []),
                                project_notes=project_record.get("business_notes", ""),
                                group_http_budget=suggested,
                                allow_optional_negatives=(summary.get("focus", {}).get(
                                    "mode") != "selected"),
                                must_be_relevant=(text == DEFAULT_ROUND
                                    or summary.get("focus", {}).get("mode") in {
                                        "selected", "all_ready"}),
                                available_identity_aliases=[r["alias"] for r in svc.intake.record(
                                    row["project_id"])["generic"]["roles"]],
                                timeout=getattr(getattr(raw_model, "_settings", None),
                                                "timeout", 30.0) + 1)
                            record.update(status="PROPOSED", interpretation=design.interpretation)
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
                                    "DESIGN_EXPLICIT_INPUT_REQUIRES_SOURCE_REVIEW"}:
                                reason = exc.args[0]
                            diagnostic = getattr(exc, "diagnostic", None)
                            if isinstance(exc, ValidationError):
                                from agenttest.diagnostics import Layer, schema_diagnostic

                                reason = "DESIGN_SCENARIO_INVALID"
                                record["diagnostic"] = schema_diagnostic(
                                    exc, Layer.DSL).model_dump(mode="json")
                            if diagnostic and diagnostic.code == "DESIGN_REQUESTED_CHECKS_MISSING":
                                reason = diagnostic.code
                            record.update(status="REJECTED", reason=reason)
                            group.update(status="NOT_EXECUTED", reason=reason)
                            continue
                        finally:
                            record["model_call"] = model.stats.model_dump(mode="json")
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
                        clarification_operations = {q.operation_id for q in design.questions
                            if q.kind == "clarification" and q.existing_question_ref is None}
                        negative_policies = {p.id for p in project.registry.definitions()
                                             if p.negative}
                        ordered_cases = sorted(design.cases, key=lambda c: not any(
                            s.operation_id in group["primary"] and s.policy_id not in
                            negative_policies for s in c.plan.steps)) if text == DEFAULT_ROUND \
                            else design.cases
                        for index, case in enumerate(ordered_cases):
                            case_row = dict(case_id=f"{group['id']}_case_{index + 1}",
                                title=case.title, kinds=[case.kind], operation_id=None,
                                planned_operations=list(dict.fromkeys(
                                    s.operation_id for s in case.plan.steps)),
                                planned_steps=len(case.plan.steps),
                                planned_policy_ids=[s.policy_id for s in case.plan.steps],
                                status="NOT_EXECUTED", max_business_requests=len(case.plan.steps),
                                plan_source="MODEL_SUGGESTION", task_satisfied=None)
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
                                scenario = validate_design_case(project, case, svc.reports,
                                    cleanup_operations=group["primary"],
                                    requested_checks=executable_checks,
                                    environ=svc.environ, transport=bounded_transport)
                            except ValueError as exc:
                                case_row.update(status="REJECTED", reason=getattr(
                                    getattr(exc, "diagnostic", None), "code", "PLAN_REJECTED"))
                                continue
                            from agenttest.investigation import InvestigationConfig
                            from agenttest.task_service import ProgressStore
                            from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

                            def progress(phase, run_id, _requests):
                                if case_row.get("run_id") != run_id:
                                    case_row.update(run_id=run_id, status="RUNNING")
                                    checkpoint()
                                svc.db.event(task_id, phase, run_id=run_id,
                                             requests=ledger.requests)

                            store = ProgressStore(svc.reports.root, progress)
                            probe_allowed = (ledger.requests + len(scenario.steps)
                                             < group_request_limit)
                            report = await Workflow(project.document, project.target,
                                project.registry, FakeLLM(""), store,
                                transport=bounded_transport, environ=svc.environ,
                                task_mode="model_designed", user_task=text, prepared_plan=scenario,
                                investigation=InvestigationConfig(enabled=probe_allowed,
                                    product_read_probe=probe_allowed),
                                execution_guard=guard,
                                plan_derivation=dict(source="MODEL_DESIGN", group=group["id"],
                                    model_request_sha256=model.stats.request_sha256),
                                budget=WorkflowBudget(max_model_calls=0,
                                    workflow_timeout=project.target.run_timeout + 15)).run()
                            case_row.update(status="FINISHED", run_id=report.run_id,
                                outcome=report.outcome.value, task_satisfied=None,
                                planned_steps=len(scenario.steps),
                                executed_steps=sum(bool(s.attempts) for s in report.steps),
                                executed_operations=[s.operation_id for s in report.steps
                                                     if s.attempts],
                                http_requests=sum(len(s.attempts) for s in report.steps),
                                resource_lifecycle=report.resource_lifecycle,
                                cleanup=report.cleanup,
                                cleanup_requests=sum(len(s.attempts)
                                    for s in report.lifecycle_cleanup),
                                approved_negative_steps=sum(
                                    s.input_validation == "APPROVED_NEGATIVE"
                                    for s in report.steps),
                                negative_rejections_passed=sum(
                                    s.negative_rejected_as_expected is True for s in report.steps),
                                investigations=[dict(operation_id=i.operation_id,
                                    method=i.method, path=i.path_template, outcome=i.outcome.value,
                                    requests=i.requests_used, reason=i.reason,
                                    checks=[c.model_dump() for c in i.checks],
                                    cause_confirmed=False) for i in report.investigations],
                                evidence=[dict(operation_id=s.operation_id, method=s.method,
                                    path=s.path_template, outcome=s.outcome.value,
                                    request=s.request_evidence,
                                    checks=[c.model_dump() for c in s.checks])
                                    for s in report.steps])
                            from agenttest.product_checks import executed_policies

                            case_row["executed_policy_ids"] = executed_policies(report)
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
                                self.retain(row, text, scenario, project, report, case_row)
                            checkpoint()
                summary["complete"] = bool(summary["cases"]) and all(
                    c.get("outcome") in {"PASS", "ASSERTION_FAILED"}
                    and c.get("executed_steps") == c.get("planned_steps")
                    for c in summary["cases"]
                ) and not summary["questions"] and not summary["not_covered"] and all(
                    g.get("status") in {"DESIGNED", "OUTSIDE_FOCUS"} for g in summary["groups"])
                summary["checks_passed"] = bool(summary["cases"]) and all(
                    c.get("outcome") == "PASS" for c in summary["cases"])
                self.coverage(summary)
                checkpoint()
                svc.db.transition(task_id, owner, "RUNNING", "FINISHED", summary=summary)
                svc.db.event(task_id, "FINISHED", requests=ledger.requests)
        except asyncio.CancelledError:
            self.stop(task_id, owner, summary, ledger,
                      "INTERRUPTED" if svc._shutting_down else "CANCELLED",
                      "已停止后续动作；已发送请求不自动回滚或重放。")
        except TimeoutError:
            self.stop(task_id, owner, summary, ledger, "INTERRUPTED", "本轮达到 600 秒时限。")
        except Exception:
            self.stop(task_id, owner, summary, ledger, "INTERRUPTED",
                      "执行条件发生变化或工具停止；保留已发生的证据，不重放请求。")
        finally:
            if transport is not None:
                await transport.aclose()

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
        except ValueError:
            case_row["save_reason"] = "场景包含敏感输入或不支持的复测参数，暂不能保存。"
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
                        resource_lifecycle=report.resource_lifecycle, cleanup=report.cleanup,
                        executed_steps=sum(bool(s.attempts) for s in report.steps),
                        evidence=[dict(operation_id=s.operation_id, method=s.method,
                            path=s.path_template, outcome=s.outcome.value,
                            request=s.request_evidence,
                            checks=[c.model_dump() for c in s.checks]) for s in report.steps])
                    from agenttest.product_checks import executed_policies

                    case["executed_policy_ids"] = executed_policies(report)
            summary.update(reason=reason, complete=False, context_usage=ledger.summary(),
                           sent_requests_may_have_completed=ledger.requests > 0)
            self.coverage(summary)
            svc.db.transition(task_id, owner, row["status"], status, summary=summary)
            svc.db.event(task_id, status, requests=ledger.requests)
