"""Task completion must be proved separately from an executable approved plan."""

import json

import httpx
import pytest
from support import registry
from test_workflow import DOCUMENT, plan

pytest.importorskip("langgraph")

from agenttest.dsl import Step
from agenttest.policies import NeedsReview
from agenttest.report import Outcome, ReportStore
from agenttest.safety import TargetConfig
from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget


@pytest.mark.asyncio
async def test_generic_natural_language_cannot_skip_intent_validation(tmp_path):
    calls = []
    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(), FakeLLM(plan()),
        ReportStore(tmp_path), httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json={})),
        environ={}, user_task="验证所有订单状态和库存一致性",
    ).run()
    assert report.plan_valid and not report.intent_verified
    assert report.dsl_valid and report.policy_authorized
    assert report.outcome == Outcome.NEEDS_REVIEW
    assert report.diagnostic.code == "INTENT_VALIDATOR_REQUIRED"
    assert calls == [] and report.tool_calls == 0


@pytest.mark.asyncio
async def test_operator_supplied_generic_intent_validator_is_used(tmp_path):
    def reject(task, scenario):
        raise NeedsReview("INTENT_PATH_MISMATCH")

    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(), FakeLLM(plan()),
        ReportStore(tmp_path), httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        environ={}, user_task="必须查询两次", intent_validator=reject,
    ).run()
    assert report.outcome == Outcome.NEEDS_REVIEW
    assert report.diagnostic.code == "INTENT_PATH_MISMATCH" and report.tool_calls == 0


@pytest.mark.asyncio
async def test_omitting_task_does_not_implicitly_approve_model_plan(tmp_path):
    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(), FakeLLM(plan()),
        ReportStore(tmp_path), environ={},
    ).run()
    assert report.outcome == Outcome.NEEDS_REVIEW and report.tool_calls == 0
    assert report.task_mode == "natural_language" and report.task_satisfied is False


@pytest.mark.asyncio
async def test_explicit_fixed_operator_plan_pass_is_not_natural_language_success(tmp_path):
    store = ReportStore(tmp_path)
    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(), FakeLLM(plan()),
        store, httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        environ={}, task_mode="operator_plan",
    ).run()
    assert report.outcome == Outcome.PASS and report.workflow_completed
    assert not report.intent_verified and report.task_satisfied is None
    assert report.analysis_code == "APPROVED_PLAN_COMPLETED"
    assert report == store.read(report.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 500])
async def test_generic_goal_success_requires_intent_and_business_success(tmp_path, status):
    def check(task, scenario):
        assert task == "读取一次列表"
        assert len(scenario.steps) == 1 and scenario.steps[0].operation_id == "items"
        # Validator receives a copy, never a plan-repair hook.
        scenario.steps.clear()

    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(), FakeLLM(plan()),
        ReportStore(tmp_path), httpx.MockTransport(lambda request: httpx.Response(status, json={})),
        environ={}, user_task="读取一次列表", intent_validator=check,
    ).run()
    assert report.intent_verified and len(report.steps) == 1
    assert report.task_satisfied is (status == 200)
    assert report.outcome == (Outcome.PASS if status == 200 else Outcome.ASSERTION_FAILED)


def test_real_or_custom_planner_cannot_enter_fixed_plan_mode(tmp_path):
    class ModelPlanner:
        async def generate(self, context):
            pytest.fail("Must not call a model in operator plan mode")

    with pytest.raises(ValueError, match="OPERATOR_PLAN_REQUIRES_FIXED_PLANNER"):
        Workflow(DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(),
                 ModelPlanner(), ReportStore(tmp_path), task_mode="operator_plan")


def test_invalid_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="TASK_MODE_INVALID"):
        Workflow(DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(),
                 FakeLLM(plan()), ReportStore(tmp_path), task_mode="model_approved")


@pytest.mark.asyncio
async def test_policy_approval_and_plan_legality_have_separate_evidence(tmp_path):
    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(),
        FakeLLM(plan(policy_id="unapproved")), ReportStore(tmp_path), environ={},
        user_task="读取列表",
    ).run()
    assert report.dsl_valid and not report.policy_authorized and not report.plan_valid
    assert report.outcome == Outcome.NEEDS_REVIEW and report.tool_calls == 0
    assert not report.intent_verified and report.task_satisfied is False


@pytest.mark.asyncio
async def test_model_cannot_select_task_mode(tmp_path):
    raw = json.loads(plan())
    raw["task_mode"] = "operator_plan"
    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(),
        FakeLLM(json.dumps(raw)), ReportStore(tmp_path), environ={},
        user_task="验证所有订单状态和库存一致性",
    ).run()
    assert report.outcome == Outcome.TOOL_ERROR and report.tool_calls == 0
    assert report.task_satisfied is False


@pytest.mark.asyncio
async def test_report_publication_failure_cannot_claim_goal_satisfaction(tmp_path):
    class FailingStore(ReportStore):
        failed = False

        def save(self, report):
            if report.workflow_completed and not self.failed:
                self.failed = True
                raise OSError("private-publication-path")
            return super().save(report)

    store = FailingStore(tmp_path)
    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(), FakeLLM(plan()),
        store, httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        environ={}, user_task="读取一次列表", intent_validator=lambda task, plan: None,
    ).run()
    assert report.outcome == Outcome.TOOL_ERROR and not report.workflow_completed
    assert report.analysis_code == "FIX_PLAN_OR_TOOL"
    assert report.intent_verified and report.task_satisfied is False
    assert report == store.read(report.run_id)
    assert "private-publication-path" not in report.model_dump_json()


def test_legacy_report_does_not_invent_task_completion():
    from agenttest.report import RunReport

    report = RunReport.model_validate({
        "scenario_sha256": "old-plan", "contract_sha256": "old-contract", "environment": "local",
        "target_origin": "http://127.0.0.1:8080", "outcome": "PASS", "completed": True,
    })
    assert report.task_mode is None and report.task_satisfied is None


@pytest.mark.asyncio
@pytest.mark.parametrize("steps", [1, 6])
async def test_graph_limit_cannot_leave_a_stale_goal_success(tmp_path, steps):
    class TrackingStore(ReportStore):
        claims = []

        def save(self, report):
            super().save(report)
            self.claims.append((report.workflow_completed, report.task_satisfied,
                                report.analysis_code))

    store = TrackingStore(tmp_path)
    report = await Workflow(
        DOCUMENT, TargetConfig(allowed_operations=["items"]), registry(), FakeLLM(plan()),
        store, httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        environ={}, user_task="读取一次列表", intent_validator=lambda task, plan: None,
        budget=WorkflowBudget(graph_steps=steps),
    ).run()
    assert report.outcome == Outcome.TEST_INFRA_ERROR and report.reason == "GRAPH_STEP_LIMIT"
    assert report.analysis_code == "CHECK_TEST_ENVIRONMENT"
    assert not report.workflow_completed and report.task_satisfied is False
    assert report == store.read(report.run_id)
    assert not any(workflow or satisfied or code == "COMPLETED"
                   for workflow, satisfied, code in store.claims)


@pytest.mark.parametrize("command,task,code", [
    ("workflow", None, 0),
    ("workflow", "读取列表并核对库存", 1),
    ("agent", "读取列表并核对库存", 1),
    ("agent", "执行操作员提供的测试计划", 1),
])
def test_cli_fixed_plan_and_natural_language_modes(tmp_path, monkeypatch, capsys,
                                                 command, task, code):
    from agenttest.__main__ import main

    class OfflineWorkflow(Workflow):
        def __init__(self, *args, **kwargs):
            kwargs["environ"] = {}
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(200, json={}))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("agenttest.workflow.Workflow", OfflineWorkflow)
    contract, target, policies, fixed = [tmp_path / name for name in (
        "contract.json", "target.json", "policies.json", "plan.json")]
    contract.write_bytes(DOCUMENT)
    target.write_text(TargetConfig(allowed_operations=["items"]).model_dump_json(),
                      encoding="utf-8")
    policies.write_text(json.dumps([registry().resolve(
        Step(id="read", operation_id="items", policy_id="items_v1"),
        "default").model_dump()]), encoding="utf-8")
    fixed.write_text(plan(), encoding="utf-8")
    args = [command, "--contract", str(contract), "--target", str(target),
            "--policies", str(policies), "--runs-dir", str(tmp_path / "reports"),
            "--fake-plan" if command == "agent" else "--plan", str(fixed)]
    if task is not None:
        args.extend(["--user-task", task])
    assert main(args) == code
    output = json.loads(capsys.readouterr().out)
    report = ReportStore(tmp_path / "reports").read(output["run_id"])
    assert report.task_mode == ("operator_plan" if task is None else "natural_language")
    assert report.task_satisfied is (None if task is None else False)
    assert len(report.steps) == (1 if task is None else 0)
