"""The eight logical tools a harness model may call, and the session that owns them.

Every tool is a *narrow* door onto machinery that already exists:

======================================  ===================================================
tool                                    what it may do
======================================  ===================================================
``get_contract``                        read the approved operation catalogue
``get_approved_policies``               read the operator's assertions and lifecycle rules
``get_test_coverage``                   read what has been measured so far
``design_test_cases``                   ask the *design* model for cases (no HTTP)
``validate_test_plan``                  deterministic preflight, returns a plan id
``execute_approved_test``               execute one validated plan through the Executor
``read_test_report``                    read the report of one run
``investigate_failure``                 run the approved read-only probe
======================================  ===================================================

None of them accepts a URL, HTTP request, shell text or Python. ``execute_approved_test`` takes
only a *plan id* that ``validate_test_plan`` handed out, so the HTTP request is never improvised
at execution time and the deterministic Executor stays the single exit. The session is created
by a caller that already holds an authorised, isolated project; no tool can widen that
authority, reach another host or change an approved assertion.

The state kept here (plans, runs, request budget) is small and serialisable so an audit can
reconstruct exactly what the model chose to do, in order.
"""

import json
from dataclasses import dataclass, field

MAX_TOOL_RESULT_CHARS = 12_000


class ToolRefused(Exception):
    """A tool call the deterministic layer will not honour; the text is shown to the model."""


def bounded(payload) -> dict:
    """Tool results carry a text projection so a harness cannot be flooded by one call."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return payload
    return dict(truncated=True, preview=text[:MAX_TOOL_RESULT_CHARS],
                note="结果过长，已截断；需要更细的信息请缩小查询范围。")


@dataclass
class ProductSession:
    """One authorised product task, seen through the eight tools."""

    project: object
    store: object
    text: str
    environ: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    max_executed_plans: int = 4
    max_requests: int = 24
    plans: dict = field(default_factory=dict)          # plan id -> validated Scenario
    plan_meta: dict = field(default_factory=dict)      # plan id -> what was validated
    design_cases: dict = field(default_factory=dict)    # design id -> model-authored cases
    design_groups: dict = field(default_factory=dict)   # design id -> group scope
    runs: dict = field(default_factory=dict)           # plan id -> RunReport
    requests_used: int = 0
    calls: list = field(default_factory=list)
    designer: object = None                            # design model, injected by the caller

    # ---------------------------------------------------------------- lifecycle bookkeeping
    def record(self, name: str, **fields) -> None:
        """Append one audit row per tool call (name + safe fields only)."""
        self.calls.append(dict(tool=name, **fields))

    # ------------------------------------------------------------------ read-only surfaces
    def approved(self, operation_id: str, method: str) -> bool:
        """Whether the operator's target configuration permits this operation at all."""
        try:
            self.project.target.permit(operation_id, method)
        except Exception:
            return False
        return True

    def contract_view(self) -> dict:
        operations = []
        for operation in self.project.contract.operations:
            operations.append(dict(
                operation_id=operation.operation_id, method=operation.method,
                path=operation.path, summary=getattr(operation, "summary", None),
                requires_identity=bool(operation.security),
                approved=self.approved(operation.operation_id, operation.method)))
        return dict(project=self.project.registration.id, operations=operations,
                    target_origin=self.project.target.base_url)

    def policy_view(self) -> dict:
        approved = [policy for policy in self.project.registry.definitions()
                    if not policy.negative]
        return dict(count=len(approved), policies=[dict(
            id=policy.id, operation_id=policy.operation_id, roles=policy.roles,
            statuses=(sorted(policy.assertions.status.codes)
                      if policy.assertions.status is not None else None),
            fields=[dict(selector=item.select, operator=item.operator, expected=item.expected)
                    for item in policy.assertions.fields],
            binds=[dict(location=rule.location, name=rule.name,
                        source_policy_id=rule.source_policy_id)
                   for rule in policy.binding_rules],
            prerequisite_policy_ids=policy.prerequisite_policy_ids) for policy in approved],
            lifecycles=[dict(create=rule.create_operation, delete=rule.delete_operation,
                             path_parameter=rule.path_parameter)
                        for rule in self.project.target.resource_lifecycles],
            approved_negatives=sum(policy.negative is not None
                                   for policy in self.project.registry.definitions()))

    def coverage_view(self) -> dict:
        cases = self.summary.get("cases") or []
        return dict(requests_used=self.requests_used, request_budget=self.max_requests,
                    plans_validated=len(self.plans), runs_executed=len(self.runs),
                    cases=[dict(case_id=case.get("case_id"), title=case.get("title"),
                                status=case.get("status"), outcome=case.get("outcome"),
                                operations=case.get("executed_operations") or [],
                                fingerprint=case.get("scenario_fingerprint"),
                                duplicate_of=case.get("duplicate_of"))
                           for case in cases],
                    coverage=self.summary.get("coverage"),
                    duplicate_retests=self.summary.get("duplicate_retests") or [])

    def _method_of(self, operation_id: str) -> str:
        for operation in self.project.contract.operations:
            if operation.operation_id == operation_id:
                return operation.method
        return ""

    # ------------------------------------------------------------------ design + preflight
    async def design(self, operations, request="") -> dict:
        """Ask the *design* model for cases on approved operations. No HTTP to the target.

        The design call is the product's own ``propose_design``; the harness model chooses the
        operation scope and the wording, but it never writes the cases itself and never sees the
        policies' private material. Proposed cases are kept under a ``design_id`` so validation
        can only ever look at cases the design model actually produced.
        """
        if self.designer is None:
            raise ToolRefused("当前会话没有可用的设计模型。")
        operations = [str(item) for item in operations or ()]
        if not operations:
            raise ToolRefused("design_test_cases 需要至少一个已批准的 operation_id。")
        unknown = sorted({item for item in operations
                          if not self.approved(item, self._method_of(item))})
        if unknown:
            raise ToolRefused("未批准的操作：" + ", ".join(unknown))
        from agenttest.product_design import design_groups, propose_design

        groups = design_groups(self.project, operation_ids=operations)
        if not groups:
            return dict(designs=[], note="没有可设计的分组。")
        designs = []
        for group in groups:
            if not group["supported"]:
                designs.append(dict(group_id=group["id"], accepted=False,
                                    reason="GROUP_TOO_LARGE",
                                    operations=group["operations"]))
                continue
            try:
                cases = await propose_design(self.project, request or self.text, group,
                    self.designer, environ=self.environ,
                    remaining_requests=max(1, self.max_requests - self.requests_used),
                    inventory_total=len(self.project.contract.operations),
                    normal_operation_ids=group["primary"],
                    must_be_relevant=True)
            except Exception as exc:  # a design refusal is feedback for the model, not a crash
                designs.append(dict(group_id=group["id"], accepted=False,
                                    reason=type(exc).__name__, detail=str(exc)[:300],
                                    operations=group["operations"]))
                continue
            design_id = f"design_{len(self.design_cases) + 1}"
            self.summary.setdefault("_harness_groups", {})[group["id"]] = group
            self.design_groups[design_id] = group
            self.design_cases[design_id] = list(getattr(cases, "cases", []) or [])
            designs.append(dict(
                design_id=design_id, group_id=group["id"], accepted=True,
                operations=group["operations"],
                proposed=[dict(index=index, title=getattr(case, "title", None),
                               operations=[step.operation_id for step in case.plan.steps])
                          for index, case in enumerate(self.design_cases[design_id])]))
        return dict(request=request, designs=designs)

    async def validate(self, design_id: str, indexes=None) -> dict:
        """Run the deterministic preflight; returns the plan ids that may be executed."""
        from agenttest.product_design import validate_design_case

        group = self.design_groups.get(design_id)
        cases = self.design_cases.get(design_id)
        if group is None or cases is None:
            raise ToolRefused("未知的 design_id：" + str(design_id))
        chosen = [(index, case) for index, case in enumerate(cases)
                  if indexes is None or index in set(indexes or ())]
        if not chosen:
            raise ToolRefused("没有要校验的场景。")
        results = []
        for index, case in chosen:
            plan_id = f"{design_id}_plan_{index + 1}"
            try:
                scenario = validate_design_case(self.project, case, self.store,
                    cleanup_operations=group["primary"], environ=self.environ)
            except Exception as exc:
                results.append(dict(plan_id=None, index=index, accepted=False,
                                    reason=getattr(getattr(exc, "diagnostic", None),
                                                   "code", None) or type(exc).__name__,
                                    detail=str(exc)[:400]))
                continue
            self.plans[plan_id] = scenario
            self.plan_meta[plan_id] = dict(group_id=group["id"],
                                           title=getattr(case, "title", None),
                                           steps=len(scenario.steps),
                                           operations=[step.operation_id
                                                       for step in scenario.steps])
            results.append(dict(plan_id=plan_id, index=index, accepted=True,
                                steps=len(scenario.steps),
                                operations=[step.operation_id for step in scenario.steps],
                                title=getattr(case, "title", None)))
        return dict(design_id=design_id, plans=results,
                    accepted=sum(1 for item in results if item["accepted"]))

    # ------------------------------------------------------------------ run + read + probe
    async def execute(self, plan_id: str) -> dict:
        """Execute one validated plan; the only business-action exit in the session."""
        plan = self.plans.get(plan_id)
        if plan is None:
            raise ToolRefused("未知或未通过校验的计划：" + str(plan_id))
        if len(self.runs) >= self.max_executed_plans:
            raise ToolRefused("本轮已执行的计划数量达到上限，请先读取报告。")
        if self.requests_used + len(plan.steps) > self.max_requests:
            raise ToolRefused("剩余请求预算不足，未执行该计划。")
        from agenttest.product_fingerprint import (
            annotate_case,
            duplicate_attempt,
            duplicate_failure_reason,
            scenario_fingerprint,
        )
        from agenttest.product_preconditions import precondition_report

        audit = precondition_report(self.project, plan)
        digest = scenario_fingerprint(self.project, plan, audit)
        repeated = duplicate_attempt(self.summary.get("cases") or [], digest)
        if repeated is not None:
            # A follow-up round exists to add information; an equivalent retest does not.
            self.record("execute_approved_test", plan_id=plan_id, skipped="DUPLICATE")
            return dict(plan_id=plan_id, skipped=True, duplicate_of=repeated["case_id"],
                        reason=duplicate_failure_reason(repeated))
        report = await self._run(plan)
        case_row = dict(case_id=f"{plan_id}_{report.run_id[:8]}", status="FINISHED",
                        outcome=report.outcome.value, title=self.plan_meta[plan_id]["title"],
                        executed_operations=[step.operation_id for step in report.steps
                                             if step.attempts],
                        http_requests=sum(len(step.attempts) for step in report.steps))
        annotate_case(case_row, self.project, plan, audit, report)
        if audit["expectations"]:
            case_row["absence_expectations"] = audit["rows"]
        self.summary.setdefault("cases", []).append(case_row)
        self.runs[plan_id] = report
        self.requests_used += case_row["http_requests"]
        failed = [dict(step_id=step.step_id, operation_id=step.operation_id,
                       outcome=step.outcome.value, reason=step.reason,
                       checks=[dict(kind=check.kind, selector=check.selector,
                                    expected=check.expected, actual=check.actual)
                               for check in step.checks if not check.passed])
                  for step in report.steps if step.outcome.value != "PASS"]
        return dict(plan_id=plan_id, run_id=report.run_id, outcome=report.outcome.value,
                    completed=report.completed, requests=case_row["http_requests"],
                    failed=failed,
                    next_hint=(None if not failed else
                               "失败步骤可用 read_test_report 查看证据，"
                               "或用 investigate_failure 做只读复核。"))

    async def _run(self, plan):
        """The single execution path: the same Workflow/Executor the product uses."""
        from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

        project = self.project
        workflow = Workflow(project.document, project.target, project.registry, FakeLLM(""),
            self.store, environ=self.environ, task_mode="model_designed",
            user_task=self.text, prepared_plan=plan,
            plan_derivation=dict(source="HARNESS_DESIGN"),
            budget=WorkflowBudget(max_model_calls=0,
                                  workflow_timeout=min(300.0, project.target.run_timeout + 220)))
        return await workflow.run()

    def report_view(self, run_id: str) -> dict:
        for plan_id, report in self.runs.items():
            if report.run_id != run_id:
                continue
            return dict(plan_id=plan_id, run_id=run_id, outcome=report.outcome.value,
                        completed=report.completed, cleanup=report.cleanup,
                        resource_lifecycle=report.resource_lifecycle,
                        created_resources=report.created_resources,
                        failure_explanation=report.failure_explanation,
                        steps=[dict(step_id=step.step_id, operation_id=step.operation_id,
                                    method=step.method, path=step.path_template,
                                    outcome=step.outcome.value, reason=step.reason,
                                    input_validation=step.input_validation,
                                    checks=[dict(kind=check.kind, selector=check.selector,
                                                 expected=check.expected, actual=check.actual,
                                                 passed=check.passed)
                                            for check in step.checks])
                               for step in report.steps])
        raise ToolRefused("没有该 run_id 的报告：" + str(run_id))

    def investigate(self, run_id: str, operation_id: str | None = None) -> dict:
        """Approved read-only probe of one failed step; never mutates the target."""
        view = self.report_view(run_id)
        failed = [step for step in view["steps"] if step["outcome"] != "PASS"]
        if not failed:
            raise ToolRefused("该报告没有失败步骤，无需排查。")
        chosen = [step for step in failed
                  if operation_id is None or step["operation_id"] == operation_id]
        if not chosen:
            raise ToolRefused("该报告中失败步骤不包含：" + str(operation_id))
        step = chosen[0]
        from agenttest.product_probe import explain_failure

        report = next(item for item in self.runs.values() if item.run_id == run_id)
        explanation = explain_failure(report)
        return dict(run_id=run_id, operation_id=step["operation_id"], outcome=step["outcome"],
                    failing_checks=[check for check in step["checks"] if not check["passed"]],
                    conclusion=explanation,
                    read_only=True,
                    note="只读复核：不重发写请求，不改写已确认的预期。")


def tool_documents(session: ProductSession) -> list[dict]:
    """Plain descriptions of the eight tools (name, purpose, arguments).

    Kept as data so the harness wiring, the prompt and the audit all read one definition.
    """
    return [
        dict(name="get_contract", purpose="读取已批准的操作目录（方法、路径、是否需要身份）。",
             arguments={}, read_only=True),
        dict(name="get_approved_policies", purpose="读取操作员批准的断言与资源生命周期规则。",
             arguments={}, read_only=True),
        dict(name="get_test_coverage", purpose="读取已测量覆盖、已执行用例与请求预算。",
             arguments={}, read_only=True),
        dict(name="design_test_cases", purpose="请设计模型针对指定操作提出测试场景（不发请求）。",
             arguments={"operations": "已批准的 operation_id 列表",
                        "request": "补充要求（可选）"}, read_only=True),
        dict(name="validate_test_plan", purpose="对已提出的场景执行确定性预检，返回 plan_id。",
             arguments={"design_id": "design_test_cases 返回的 id",
                        "indexes": "只校验其中几个（可选）"}, read_only=True),
        dict(name="execute_approved_test", purpose="执行一个已通过预检的 plan_id（唯一写入口）。",
             arguments={"plan_id": "validate_test_plan 返回的 id"}, read_only=False),
        dict(name="read_test_report", purpose="读取某次运行的完整报告与检查证据。",
             arguments={"run_id": "execute_approved_test 返回的 id"}, read_only=True),
        dict(name="investigate_failure", purpose="对失败步骤做只读复核，不重发写请求。",
             arguments={"run_id": "运行 id", "operation_id": "失败步骤（可选）"}, read_only=True),
    ]
