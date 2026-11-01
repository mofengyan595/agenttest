"""Fresh fixed regressions on synthetic or explicitly configured read-only targets."""

import asyncio
from uuid import uuid4

import httpx

from agenttest.dsl import Scenario
from agenttest.fixed_plan import load_saved_contract, replay_scope
from agenttest.goal_cli import synthetic_runtime
from agenttest.goal_suite import BudgetTransport, SuiteLedger
from agenttest.regression import RegressionRuntime, load_regression, run_regression
from agenttest.report import Outcome, fingerprint
from agenttest.task_store import TERMINAL, TaskConflict


class GuardedSyntheticTransport(httpx.AsyncBaseTransport):
    def __init__(self, delegate, guard):
        self.delegate, self.guard = delegate, guard

    async def handle_async_request(self, request):
        self.guard()
        return await self.delegate.handle_async_request(request)


class SavedTests:
    def __init__(self, service):
        self.service = service
        self.inputs = {}

    def directory(self, task_id, run_id):
        return self.service.reports.root / "exports" / task_id / ("run_" + run_id)

    def executable(self, project, scenario):
        if self.service.synthetic:
            return True
        return (self.service.generic_intake
                and replay_scope(project.contract, project.target, scenario, project.registry)
                != "MUTATION_LIFECYCLE_REQUIRED")

    @staticmethod
    def lifecycle_reason(project, scenario):
        from agenttest.lifecycle import lifecycle_plan

        try:
            lifecycle_plan(project.contract, project.target, scenario, project.registry)
        except ValueError as exc:
            if str(exc) == "LIFECYCLE_READ_PREPARATION_REQUIRED":
                return "此场景仍读取未在本轮准备的资源，请改用本轮创建结果或单独保存只读检查。"
        return "此写场景缺少已确认的新数据准备、同轮资源绑定或清理，请先补齐。"

    @staticmethod
    def missing_negative(row, run_id):
        return any(c.get("run_id") == run_id and "negative" in c.get("kinds", [])
                   and c.get("approved_negative_steps") == 0
                   for c in row["summary"].get("cases", []))

    def asset(self, row, run_id, *, current_project=None):
        if self.missing_negative(row, run_id):
            raise TaskConflict("NEGATIVE_CHECK_NOT_EXECUTED")
        svc = self.service
        if (row["id"], run_id) in svc._assets:
            asset = svc._assets[(row["id"], run_id)]
            project = current_project or svc.project(row["project_id"])
            if project.identity() != asset[2].identity():
                raise TaskConflict("REGRESSION_PROJECT_VERSION_CHANGED")
            return asset
        if row["summary"].get("mode") == "MODEL_DESIGNED":
            identity, data = svc.db.product_asset(row["id"], run_id)
            project = current_project or svc.project(row["project_id"])
            if project.identity() != identity:
                raise TaskConflict("REGRESSION_PROJECT_VERSION_CHANGED")
            from agenttest.fixed_plan import FixedPlanSnapshot

            return (Scenario.model_validate(data["scenario"]),
                    FixedPlanSnapshot.model_validate(data["snapshot"]), project)
        saved = svc.db.saved_test(row["id"], run_id)
        if saved["project_id"] != row["project_id"]:
            raise TaskConflict("REGRESSION_PROJECT_MISMATCH")
        project = current_project or svc.project(saved["project_id"])
        if project.identity() != saved["identity"]:
            raise TaskConflict("REGRESSION_PROJECT_VERSION_CHANGED")
        _, scenario = load_regression(self.directory(row["id"], run_id))
        return scenario, load_saved_contract(saved["contract"]), project

    def list(self, owner):
        result = []
        for saved in self.service.db.saved_tests(owner):
            row = self.service.db.get(saved["task_id"], owner)
            available, reason = False, "网页再次运行仅支持服务端专用合成环境。"
            name = "已保存测试"
            if saved["project_id"].startswith("web_"):
                try:
                    name = self.service.intake.record(saved["project_id"])["name"]
                except (ValueError, KeyError):
                    pass
            try:
                scenario, _, project = self.asset(row, saved["run_id"])
                if not saved["project_id"].startswith("web_"):
                    name = " / ".join(project.catalog.resource_aliases) or name
                load_regression(self.directory(saved["task_id"], saved["run_id"]))
                available = self.executable(project, scenario)
                if available:
                    reason = ("每次准备本轮数据并清理，使用固定计划；模型禁用。"
                              if replay_scope(project.contract, project.target, scenario,
                                  project.registry) == "OWNED_WRITE_LIFECYCLE" else
                              "每次创建新资源，使用固定计划；模型禁用。"
                              if self.service.synthetic else
                              "每次重新登录，再执行已确认的只读检查；模型禁用。"
                              if replay_scope(project.contract, project.target, scenario)
                              == "AUTHENTICATED_READONLY" else
                              "独立执行已确认的只读接口与原断言；模型禁用，不恢复原请求。")
                elif self.service.generic_intake:
                    reason = self.lifecycle_reason(project, scenario)
            except (ValueError, OSError, KeyError):
                reason = "项目配置或回归文件已变化，请重新测试并保存。"
            if self.missing_negative(row, saved["run_id"]):
                available = False
                reason = "原场景未实际执行异常检查，请重新测试后保存。"
            result.append(dict(run_id=saved["run_id"], task_id=saved["task_id"],
                title=next((c.get("title") for c in row["summary"].get("cases", [])
                            if c.get("run_id") == saved["run_id"]), None) or name,
                project_id=saved["project_id"], project_name=name,
                available=available, reason=reason))
        return result

    async def submit(self, owner, source_task, run_id, submission):
        svc = self.service
        if not svc.synthetic and not svc.generic_intake:
            raise TaskConflict("FIXED_WEB_RUN_REQUIRES_DEDICATED_SYNTHETIC_TARGET")
        if svc._shutting_down or len(svc._workers) >= 8:
            raise TaskConflict("TASK_QUEUE_FULL")
        source = svc.db.get(source_task, owner)
        saved = svc.db.saved_test(source_task, run_id)
        if saved["owner"] != owner:
            raise KeyError("SAVED_TEST_NOT_FOUND")
        scenario, contract, project = self.asset(source, run_id)
        if not self.executable(project, scenario):
            raise TaskConflict("FIXED_WEB_RUN_REQUIRES_READONLY_OPERATIONS")
        directory = self.directory(source_task, run_id)
        load_regression(directory)
        state = "WAITING_REVIEW" if project.registration.require_plan_review else "QUEUED"
        summary = dict(mode="FIXED_REGRESSION", source_run_id=run_id,
            display_title=source["summary"].get("display_title", "已保存测试") + " · 固定回归",
            required_plan=[dict(key=s.id, operation_id=s.operation_id, role=s.role,
                                policy_id=s.policy_id) for s in scenario.steps])
        row, created = svc.db.create(uuid4().hex, owner, submission, source["project_id"],
            project.identity(), fingerprint(dict(source=source_task, run=run_id)), state, summary)
        if created:
            svc._owners[row["id"]] = owner
            svc._inputs[row["id"]] = (source["project_id"], "", False,
                                       SuiteLedger(project.target.max_requests, 0, 0))
            self.inputs[row["id"]] = (directory, contract, project.identity())
            svc.db.event(row["id"], state)
            if state == "QUEUED":
                svc._enqueue(row["id"], owner)
        return row

    async def run(self, task_id, owner):
        from agenttest.task_service import ProgressStore

        svc = self.service
        ledger = svc._inputs[task_id][3]
        try:
            async with svc._serial:
                row = svc.db.transition(task_id, owner, "QUEUED", "RUNNING")
                directory, contract, identity = self.inputs[task_id]
                project = svc.project(row["project_id"])

                def guard():
                    current = svc.project(row["project_id"])
                    _, scenario = load_regression(directory)
                    if (not self.executable(current, scenario) or current.identity() != identity):
                        raise ValueError("PROJECT_VERSION_CHANGED_RECHECK_REQUIRED")

                guard()
                env, transport = (synthetic_runtime(project) if svc.synthetic else
                    (dict(svc.environ), httpx.AsyncHTTPTransport(retries=0, trust_env=False)))
                if svc.synthetic and not isinstance(transport, httpx.ASGITransport):
                    raise ValueError("IN_MEMORY_TARGET_REQUIRED")
                bounded = GuardedSyntheticTransport(BudgetTransport(transport, ledger), guard)
                def progress(phase, run, requests):
                    current = svc.db.get(task_id, owner)
                    if current["summary"].get("run_id") != run:
                        svc.db.transition(task_id, owner, "RUNNING", "RUNNING",
                            summary=dict(current["summary"], run_id=run, model_source="DISABLED"))
                    svc.db.event(task_id, phase, run_id=run, requests=requests)

                store = ProgressStore(svc.reports.root, progress)
                try:
                    report = await run_regression(directory, RegressionRuntime(project.document,
                        project.target, project.registry, contract, env, bounded, store))
                finally:
                    await transport.aclose()
                summary = dict(row["summary"], run_id=report.run_id, outcome=report.outcome.value,
                    fixed_regression_passed=report.completed and report.outcome == Outcome.PASS,
                    task_satisfied=None, workflow_completed=report.workflow_completed,
                    model_source="DISABLED", context_usage=ledger.summary(),
                    resource_lifecycle=report.resource_lifecycle, cleanup=report.cleanup,
                    execution_target=("SYNTHETIC_MEMORY" if svc.synthetic
                                      else "AUTHORIZED_TEST_ENVIRONMENT"))
                svc.db.transition(task_id, owner, "RUNNING", "FINISHED", summary=summary)
                svc.db.event(task_id, "FINISHED")
        except asyncio.CancelledError:
            self.stop(task_id, owner, "INTERRUPTED" if svc._shutting_down else "CANCELLED")
        except Exception:
            self.stop(task_id, owner, "INTERRUPTED")

    def stop(self, task_id, owner, status):
        svc = self.service
        row = svc.db.get(task_id, owner)
        if row["status"] not in TERMINAL:
            svc.db.transition(task_id, owner, row["status"], status,
                summary=dict(row["summary"], mode="FIXED_REGRESSION",
                             reason="FIXED_RUN_STOPPED_NO_REPLAY", model_source="DISABLED",
                             context_usage=svc._inputs[task_id][3].summary()))
            svc.db.event(task_id, status)
