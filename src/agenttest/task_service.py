"""Local task runner around the existing engine; serial admission and no restart recovery."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from agenttest.credentials import CredentialVault
from agenttest.goal_cli import synthetic_runtime
from agenttest.goal_derivation import derive_goal
from agenttest.goal_runtime import run_goal
from agenttest.goal_suite import BudgetPlanner, BudgetTransport, SuiteLedger, build_suite, run_suite
from agenttest.planner_input import validate_user_task
from agenttest.project_goals import RegisteredProject, check_goal_credentials, load_goal_project
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.report_files import pinned_directory
from agenttest.task_store import TERMINAL, ServiceLease, TaskConflict, TaskStore


class ProgressStore(ReportStore):
    def __init__(self, root, callback):
        super().__init__(root)
        self.callback = callback

    def save(self, report):
        super().save(report)
        phase = {"plan": "PLANNING", "validate": "VALIDATING", "execute": "EXECUTING"}.get(
            report.workflow_nodes[-1] if report.workflow_nodes else "", "REPORT")
        self.callback(phase, report.run_id, sum(len(s.attempts) for s in
            [*report.steps, *report.investigations, *report.lifecycle_cleanup]))


class TaskService:
    def __init__(self, projects: dict[str, Path], db: TaskStore, reports: ReportStore,
                 planner_factory, *, synthetic=False, environ=None, analyzer_factory=None,
                 semantic_factory=None, expose_registered=True, model_status=None,
                 generic_intake=False, intake_authorized_ips=(), designer_factory=None,
                 focus_factory=None, private_input_guard=None, product_probe_factory=None):
        self.projects = dict(projects)
        for key, path in self.projects.items():
            if load_goal_project(path).registration.id != key:
                raise ValueError("PROJECT_REGISTRATION_ID_MISMATCH")
        self.db, self.reports, self.planner_factory = db, reports, planner_factory
        self._lease = ServiceLease(db.path.with_suffix(".service.lock"))
        self.db.interrupt_unfinished()
        self.synthetic = synthetic
        self.analyzer_factory = analyzer_factory
        self.semantic_factory = semantic_factory
        self.designer_factory = designer_factory
        self.focus_factory = focus_factory
        self.product_probe_factory = product_probe_factory
        self.private_input_guard = private_input_guard or (lambda _: False)
        self.environ = dict(os.environ if environ is None else environ)
        self._inputs, self._workers = {}, {}
        self._owners = {}
        self._assets = {}
        self._shutting_down = False
        self._serial = asyncio.Semaphore(1)
        self.expose_registered = expose_registered
        self.model_status = model_status or (lambda: "UNKNOWN")
        self.generic_intake = generic_intake and not synthetic
        self.intake_authorized_ips = tuple(intake_authorized_ips)
        from agenttest.project_intake import ProjectIntake

        self.intake = ProjectIntake(self)
        from agenttest.saved_tests import SavedTests

        self.saved = SavedTests(self)
        from agenttest.product_tasks import ProductTasks

        self.product = ProductTasks(self)

    def project(self, project_id):
        if project_id in self.projects:
            project = self.intake.profile(project_id)
            if not self.synthetic:
                from dataclasses import replace

                names = {name for role in project.target.roles.values() for name in
                         (role.username_env, role.password_env, role.bearer_env) if name}
                project = replace(project, intake_revision=fingerprint(dict(
                    project=project.identity(),
                    credentials={n: self.environ.get(n) for n in names})))
            return project
        if project_id.startswith("web_"):
            return self.intake.project(project_id)
        raise ValueError("PROJECT_NOT_REGISTERED")

    def project_list(self):
        return self.intake.registered() + self.intake.list()

    async def submit(self, owner, project_id, text, submission, *, suite=False, parent=None,
                     replace_pending=False):
        if self._shutting_down or len(self._workers) >= 8:
            raise TaskConflict("TASK_QUEUE_FULL")
        project = self.project(project_id)
        if self.db.model_usage_blocked():
            raise TaskConflict("MODEL_USAGE_REQUIRES_RECONCILIATION")
        if self.model_status() == "UNAVAILABLE":
            raise ValueError("MODEL_NOT_CONFIGURED")
        if project.target.business_scenario == "web_contract_v2" and not text.strip():
            from agenttest.product_tasks import DEFAULT_FOLLOWUP, DEFAULT_ROUND

            text = DEFAULT_FOLLOWUP if parent else DEFAULT_ROUND
        validate_user_task(text)
        if self.private_input_guard(text):
            raise ValueError("TASK_SECRET_REJECTED")
        vault = CredentialVault(project.target, self.environ)
        try:
            if vault.contains_secret(text):
                raise ValueError("TASK_SECRET_REJECTED")
        finally:
            vault.close()
        if project.target.business_scenario == "web_contract_v2":
            return await self.product.submit(owner, project, text, submission, parent)
        shared = None
        if parent:
            previous = self.db.get(parent, owner)
            if previous["project_id"] != project_id or parent not in self._inputs:
                raise TaskConflict("CONTEXT_EXPIRED_OR_WRONG_PROJECT")
            _, original, _, shared = self._inputs[parent]
            if replace_pending:
                if previous["status"] != "NEEDS_REVIEW":
                    raise TaskConflict("CLARIFICATION_REQUIRES_PENDING_TASK")
            else:
                text = original + "，" + text
                validate_user_task(text)
        goal = derive_goal(project, text)
        from agenttest.goal_spec import SEMANTIC_REASONS

        draft_allowed = (project.registration.semantic_goal_drafts
                         and self.semantic_factory is not None and goal.task is None
                         and goal.reason in SEMANTIC_REASONS)
        is_suite = suite or goal.scope == "project" or draft_allowed
        summary = (build_suite(project, text, environ=None if self.synthetic else self.environ)
                   .preview() if is_suite else goal.summary())
        labels = {g.id: g.aliases[0] for g in project.catalog.goals}
        summary["display_title"] = ("项目已授权核心接口" if goal.scope == "project" else
            "、".join(labels[k] for k in goal.requested_goals if k in labels)
            or "待补充的测试需求")
        if goal.reason == "INTENT_UNVERIFIED_BUSINESS_REQUIREMENT" and not is_suite:
            from agenttest.project_intake import clarification_question

            summary["question"] = clarification_question(project, text)
        summary["budget"] = dict(max_requests=project.target.max_requests,
            max_model_calls=project.registration.budget.suite_max_model_calls,
            max_total_tokens=project.registration.budget.suite_max_total_tokens)
        vault = CredentialVault(project.target, self.environ)
        try:
            if vault.contains_secret(summary):
                raise ValueError("PROJECT_PUBLIC_METADATA_REJECTED")
        finally:
            vault.close()
        if parent:
            summary["parent_task_id"] = parent
        ready = (any(c["status"] == "READY" for c in summary["cases"])
                 if is_suite else goal.task is not None)
        if ready and not is_suite and not self.synthetic:
            try:
                check_goal_credentials(project, goal.task, self.environ)
            except ValueError:
                ready = False
                summary.update(status="NEEDS_REVIEW", reason="PROJECT_CREDENTIAL_PREFLIGHT_FAILED",
                    question="请由操作员在服务端配置角色凭据；不要在测试需求中填写密码或令牌。")
        ready = ready or draft_allowed
        state = ("WAITING_REVIEW" if project.registration.require_plan_review else "QUEUED")
        if not ready:
            state = "NEEDS_REVIEW"
        task_id = uuid4().hex
        row, created = self.db.create(task_id, owner, submission, project_id, project.identity(),
                                     fingerprint(text), state, summary)
        if created:
            ledger = shared or SuiteLedger(project.target.max_requests,
                project.registration.budget.suite_max_model_calls,
                project.registration.budget.suite_max_total_tokens)
            self._inputs[task_id] = (project_id, text, is_suite, ledger)
            self._owners[task_id] = owner
            self.db.event(task_id, state)
            if state == "QUEUED":
                self._enqueue(task_id, owner)
        return row

    def _enqueue(self, task_id, owner):
        operation = (self.product.run(task_id, owner) if task_id in self.product.inputs else
                     self.saved.run(task_id, owner) if task_id in self.saved.inputs
                     else self._run(task_id, owner))
        worker = asyncio.create_task(operation)
        self._workers[task_id] = worker
        worker.add_done_callback(lambda _: self._workers.pop(task_id, None))

    async def start(self, task_id, owner, version):
        row = self.db.get(task_id, owner)
        if task_id not in self._inputs:
            raise TaskConflict("TASK_CONTEXT_UNAVAILABLE")
        if self.project(row["project_id"]).identity() != row["project_identity"]:
            raise TaskConflict("PLAN_PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")
        row = self.db.transition(task_id, owner, "WAITING_REVIEW", "QUEUED", version=version)
        self._enqueue(task_id, owner)
        return row

    async def _run(self, task_id, owner):
        row = None
        try:
            async with self._serial:
                row = self.db.transition(task_id, owner, "QUEUED", "RUNNING")
                project_id, text, suite, shared = self._inputs[task_id]
                project = self.project(project_id)
                if project.identity() != row["project_identity"]:
                    self.db.transition(task_id, owner, "RUNNING", "NEEDS_REVIEW",
                                       summary=dict(reason="PROJECT_VERSION_CHANGED_RECHECK_REQUIRED"))
                    return

                def guard():
                    if self.project(project_id).identity() != row["project_identity"]:
                        raise ValueError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")
                env, transport = (synthetic_runtime(project) if self.synthetic
                                  else (dict(self.environ), None))
                own_transport = transport is None
                if own_transport:
                    import httpx

                    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
                bounded = BudgetTransport(transport, shared)
                store = ProgressStore(self.reports.root, lambda phase, run, requests:
                    self.db.event(task_id, phase, run_id=run, requests=requests))
                try:
                    shared.max_requests = min(shared.max_requests, project.target.max_requests)
                    shared.max_model_calls = min(shared.max_model_calls,
                        project.registration.budget.suite_max_model_calls)
                    shared.max_total_tokens = min(shared.max_total_tokens,
                        project.registration.budget.suite_max_total_tokens)
                    from agenttest.llm_planner import OpenAICompatiblePlanner
                    from agenttest.regression import RegressionCapture

                    raw_planner = self.planner_factory(project)
                    model_source = self.model_status()
                    if model_source == "UNKNOWN":
                        model_source = ("CONFIGURED_MODEL" if isinstance(raw_planner,
                                        OpenAICompatiblePlanner) else "SIMULATED")
                    planner = RegressionCapture(raw_planner)
                    analyzer = (self.analyzer_factory(project) if self.analyzer_factory
                                and project.capabilities.entries() else None)
                    from agenttest.workflow import WorkflowBudget

                    timeout = getattr(getattr(raw_planner, "_settings", None), "timeout", 5.0)
                    budget = WorkflowBudget(model_timeout=timeout + 1,
                                            workflow_timeout=min(4 * timeout + 30, 300))

                    validated_plans = {}

                    def retain_plan(goal, plan):
                        validated_plans[fingerprint(goal.task.model_dump())] = plan

                    def retain(goal, report):
                        selected_plan = validated_plans.get(fingerprint(goal.task.model_dump()),
                                                            planner.scenario)
                        if (report.outcome == Outcome.PASS and report.task_satisfied
                                and selected_plan is not None
                                and fingerprint(selected_plan.model_dump())
                                == report.scenario_sha256):
                            self._assets[(task_id, report.run_id)] = (
                                selected_plan.model_copy(deep=True), goal.task, project)

                    if suite:
                        result = await run_suite(project, text, planner, store, environ=env,
                            transport=transport, ledger=shared, budget=budget, analyzer=analyzer,
                            on_report=retain, execution_guard=guard,
                            on_validated_plan=retain_plan,
                            semantic_model=self.semantic_factory(project)
                                if (self.semantic_factory
                                    and project.registration.semantic_goal_drafts)
                                else None)
                    else:
                        derived = derive_goal(project, text)
                        if (shared.requests + len(derived.task.requirements.steps)
                                > shared.max_requests or shared.usage_unknown):
                            self.db.transition(task_id, owner, "RUNNING", "NEEDS_REVIEW",
                                summary=dict(reason="CONTEXT_BUDGET_OR_USAGE_STOP"))
                            return
                        goal, report = await run_goal(project, text, BudgetPlanner(planner, shared),
                                                      store, environ=env,
                            transport=bounded, budget=budget, execution_guard=guard,
                            analyzer=BudgetPlanner(analyzer, shared) if analyzer else None)
                        result = dict(goal.summary(), run_id=report.run_id,
                            outcome=report.outcome.value, task_satisfied=report.task_satisfied)
                        retain(goal, report)
                    result["context_usage"] = shared.summary()
                    result["budget"] = row["summary"]["budget"]
                    result["display_title"] = row["summary"].get("display_title")
                    result["model_source"] = model_source
                    result["execution_target"] = ("SYNTHETIC_MEMORY" if self.synthetic
                                                   else "AUTHORIZED_TEST_ENVIRONMENT")
                    terminal = "NEEDS_REVIEW" if result.get("goal_spec") else "FINISHED"
                    self.db.transition(task_id, owner, "RUNNING", terminal, summary=result)
                    self.db.event(task_id, terminal)
                finally:
                    if own_transport:
                        await transport.aclose()
        except asyncio.CancelledError:
            current = self.db.get(task_id, owner)
            if current["status"] not in TERMINAL:
                state = "INTERRUPTED" if self._shutting_down else "CANCELLED"
                evidence = self.db.events(task_id, owner)
                refs = [e["run_id"] for e in evidence if e["run_id"]]
                self.db.transition(task_id, owner, current["status"], state,
                    summary=dict(reason="CANCELLED_NO_ROLLBACK_OR_REPLAY",
                        context_usage=self._inputs[task_id][3].summary(),
                        run_id=refs[-1] if refs else None, evidence=evidence,
                        sent_requests_may_have_completed=self._inputs[task_id][3].requests > 0))
                self.db.event(task_id, state)
        except Exception:
            current = self.db.get(task_id, owner)
            if current["status"] not in TERMINAL:
                self.db.transition(task_id, owner, current["status"], "INTERRUPTED",
                                   summary=dict(reason="TASK_EXECUTION_STOPPED_NO_REPLAY"))
                self.db.event(task_id, "INTERRUPTED")

    async def cancel(self, task_id, owner):
        row = self.db.get(task_id, owner)
        if row["status"] in TERMINAL:
            return row
        worker = self._workers.get(task_id)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        # A just-created task can be cancelled before its coroutine first enters try.
        row = self.db.get(task_id, owner)
        if row["status"] not in TERMINAL:
            summary = {**row["summary"], "reason": "CANCELLED_NO_ROLLBACK_OR_REPLAY",
                       "complete": False}
            if task_id in self._inputs:
                summary["context_usage"] = self._inputs[task_id][3].summary()
            if summary.get("mode") == "MODEL_DESIGNED":
                self.product.coverage(summary)
            row = self.db.transition(task_id, owner, row["status"], "CANCELLED",
                                     summary=summary)
            self.db.event(task_id, "CANCELLED")
        return row

    def export_regression(self, task_id, owner, run_id):
        """Export only a completed owned run; browser cannot supply code or a filesystem path."""
        from agenttest.regression import RegressionRuntime, export_regression

        row = self.db.get(task_id, owner)
        if row["status"] != "FINISHED":
            raise TaskConflict("REGRESSION_SOURCE_NOT_AVAILABLE")
        scenario, task, project = self.saved.asset(row, run_id)
        if (row["summary"].get("mode") == "MODEL_DESIGNED"
                and not self.saved.executable(project, scenario)):
            raise TaskConflict("FIXED_WRITE_LIFECYCLE_REQUIRED")
        if self.project(row["project_id"]).identity() != project.identity():
            raise TaskConflict("REGRESSION_PROJECT_VERSION_CHANGED")
        output = self.reports.root / "exports" / task_id / ("run_" + run_id)
        if output.exists():
            from agenttest.regression import load_regression

            manifest, _ = load_regression(output)
            if manifest.source_report_sha256 != fingerprint(
                    self.reports.read(run_id).model_dump(mode="json")):
                raise TaskConflict("REGRESSION_SOURCE_CHANGED")
            self.db.save_test(run_id, task_id, owner, row["project_id"], project.identity(),
                              task.model_dump_json())
            return output
        env = synthetic_runtime(project)[0] if self.synthetic else dict(self.environ)
        runtime = RegressionRuntime(project.document, project.target, project.registry,
                                    task, env, None, self.reports)
        with pinned_directory(self.reports.root, "exports", create=True) as exports:
            with pinned_directory(exports.path, task_id, create=True):
                export_regression(output, runtime, scenario, self.reports.read(run_id))
        self.db.save_test(run_id, task_id, owner, row["project_id"], project.identity(),
                          task.model_dump_json())
        return output

    def task_view(self, row):
        """Publish export eligibility without producing files or running requests."""
        summary = row["summary"]
        runs = [summary["run_id"]] if summary.get("run_id") else []
        runs += [c["run_id"] for c in summary.get("cases", []) if c.get("run_id")]
        if row["status"] in {"CANCELLED", "INTERRUPTED"}:
            for run_id in runs:
                try:
                    report = self.reports.read(run_id)
                except (ValueError, OSError):
                    continue
                evidence = dict(resource_lifecycle=report.resource_lifecycle,
                    created_resources=report.created_resources,
                    cleanup=report.cleanup, failure_explanation=report.failure_explanation)
                if summary.get("mode") == "FIXED_REGRESSION":
                    summary.update(evidence)
                for case in summary.get("cases", []):
                    if case.get("run_id") == run_id:
                        case.update(evidence)
        exports = {}
        try:
            current_project = (self.project(row["project_id"])
                               if row["status"] == "FINISHED" else None)
        except (ValueError, OSError, KeyError):
            current_project = None
        for run_id in runs:
            try:
                asset = (self.saved.asset(row, run_id, current_project=current_project)
                         if row["status"] == "FINISHED" and current_project else None)
            except (KeyError, ValueError, OSError):
                asset = None
            reason = "仅完整通过且满足固定复测条件的场景可以保存。"
            case = next((c for c in summary.get("cases", []) if c.get("run_id") == run_id), {})
            if not asset and case.get("save_reason"):
                reason = case["save_reason"]
            available = False
            if row["status"] == "FINISHED" and asset:
                scenario, _, project = asset
                if (summary.get("mode") == "MODEL_DESIGNED"
                        and not self.saved.executable(project, scenario)):
                    reason = self.saved.lifecycle_reason(project, scenario)
                elif any(s.upload or s.poll or s.read_retries for s in scenario.steps):
                    reason = "此场景含上传、轮询或读取重试，暂不支持固定回归导出。"
                else:
                    try:
                        available = current_project.identity() == project.identity()
                    except (ValueError, OSError, KeyError):
                        available = False
                    reason = "" if available else "项目配置已变化，请重新执行独立测试后导出。"
            if self.saved.missing_negative(row, run_id):
                available = False
                reason = "此场景没有实际受控异常检查，不能保存为异常测试。"
            exports[run_id] = dict(available=available, reason=reason)
        summary["exports"] = exports
        if summary.get("mode") == "MODEL_DESIGNED":
            try:
                record = self.intake.record(row["project_id"])
                answers = {a.get("source_question"): a for op in
                    record.get("generic", {}).get("operations", [])
                    for a in op.get("access_checks", []) if a.get("source_question")}
                for question in summary.get("questions", []):
                    answer = answers.get(question["id"])
                    if answer and answer["role"] == question.get("role"):
                        question["confirmed_statuses"] = answer["statuses"]
            except (ValueError, KeyError, OSError):
                pass
        return row

    async def close(self):
        self._shutting_down = True
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for task_id, owner in self._owners.items():
            row = self.db.get(task_id, owner)
            if row["status"] not in TERMINAL:
                self.db.transition(task_id, owner, row["status"], "INTERRUPTED")
        self._lease.close()


def load_projects(path: Path):
    """Server-owned local registry; the browser can submit only one of its IDs."""
    from agenttest.plan_json import bounded_json
    from agenttest.project import _read

    values = bounded_json(_read(path, 16_000).decode("utf-8"), 16_000)
    if not isinstance(values, dict) or len(values) > 20:
        raise ValueError("PROJECT_DIRECTORY_INVALID")
    result = {}
    for key, reference in values.items():
        RegisteredProject.local_reference(reference)
        result[key] = path.absolute().parent / reference
    return result
