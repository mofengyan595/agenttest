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
        self.callback(phase, report.run_id, sum(len(s.attempts) for s in report.steps))


class TaskService:
    def __init__(self, projects: dict[str, Path], db: TaskStore, reports: ReportStore,
                 planner_factory, *, synthetic=False, environ=None, analyzer_factory=None,
                 semantic_factory=None):
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
        self.environ = dict(os.environ if environ is None else environ)
        self._inputs, self._workers = {}, {}
        self._owners = {}
        self._assets = {}
        self._shutting_down = False
        self._serial = asyncio.Semaphore(1)

    def project(self, project_id):
        if project_id not in self.projects:
            raise ValueError("PROJECT_NOT_REGISTERED")
        return load_goal_project(self.projects[project_id])

    def project_list(self):
        return [dict(id=key, identity=self.project(key).identity())
                for key in sorted(self.projects)]

    async def submit(self, owner, project_id, text, submission, *, suite=False, parent=None,
                     replace_pending=False):
        if self._shutting_down or len(self._workers) >= 8:
            raise TaskConflict("TASK_QUEUE_FULL")
        project = self.project(project_id)
        validate_user_task(text)
        vault = CredentialVault(project.target, self.environ)
        try:
            if vault.contains_secret(text):
                raise ValueError("TASK_SECRET_REJECTED")
        finally:
            vault.close()
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
        worker = asyncio.create_task(self._run(task_id, owner))
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
                    model_source = ("CONFIGURED_MODEL" if isinstance(raw_planner,
                                    OpenAICompatiblePlanner) else "SIMULATED")
                    planner = RegressionCapture(raw_planner)
                    analyzer = (self.analyzer_factory(project) if self.analyzer_factory
                                and project.capabilities.entries() else None)
                    from agenttest.workflow import WorkflowBudget

                    timeout = getattr(getattr(raw_planner, "_settings", None), "timeout", 5.0)
                    budget = WorkflowBudget(model_timeout=timeout + 1,
                                            workflow_timeout=min(4 * timeout + 30, 300))

                    def retain(goal, report):
                        if (report.outcome == Outcome.PASS and report.task_satisfied
                                and planner.scenario is not None
                                and fingerprint(planner.scenario.model_dump())
                                == report.scenario_sha256):
                            self._assets[(task_id, report.run_id)] = (
                                planner.scenario.model_copy(deep=True), goal.task, project)

                    if suite:
                        result = await run_suite(project, text, planner, store, environ=env,
                            transport=transport, ledger=shared, budget=budget, analyzer=analyzer,
                            on_report=retain, execution_guard=guard,
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
                    result["model_source"] = model_source
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
            row = self.db.transition(task_id, owner, row["status"], "CANCELLED",
                                    summary=dict(reason="CANCELLED_NO_ROLLBACK_OR_REPLAY"))
            self.db.event(task_id, "CANCELLED")
        return row

    def export_regression(self, task_id, owner, run_id):
        """Export only a completed owned run; browser cannot supply code or a filesystem path."""
        from agenttest.regression import RegressionRuntime, export_regression

        row = self.db.get(task_id, owner)
        if row["status"] != "FINISHED" or (task_id, run_id) not in self._assets:
            raise TaskConflict("REGRESSION_SOURCE_NOT_AVAILABLE")
        scenario, task, project = self._assets[(task_id, run_id)]
        if self.project(row["project_id"]).identity() != project.identity():
            raise TaskConflict("REGRESSION_PROJECT_VERSION_CHANGED")
        output = self.reports.root / "exports" / task_id / ("run_" + run_id)
        if output.exists():
            raise TaskConflict("REGRESSION_ALREADY_EXPORTED")
        env = synthetic_runtime(project)[0] if self.synthetic else dict(self.environ)
        runtime = RegressionRuntime(project.document, project.target, project.registry,
                                    task, env, None, self.reports)
        with pinned_directory(self.reports.root, "exports", create=True) as exports:
            with pinned_directory(exports.path, task_id, create=True):
                export_regression(output, runtime, scenario, self.reports.read(run_id))
        return output

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
