"""Small keyless LangGraph workflow, with trusted authority outside graph state."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol, TypedDict

import httpx
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import Field, ValidationError

from agenttest.contracts import ContractSummary, load_openapi
from agenttest.credentials import CredentialVault
from agenttest.diagnostics import Diagnostic, Layer, ValidationFailure, schema_diagnostic
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.intent import validate_intent
from agenttest.investigation import InvestigationConfig, ReadOnlyCapabilityRegistry
from agenttest.llm_planner import PlannerFailure
from agenttest.plan_json import bounded_json
from agenttest.planner_input import planning_context, validate_user_task
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.report import Outcome, PlanStepEvidence, ReportStore, RunReport, fingerprint
from agenttest.safety import StrictModel, TargetConfig


class WorkflowBudget(StrictModel):
    # This milestone deliberately permits one plan and one deterministic execution.
    max_model_calls: int = Field(default=1, ge=0, le=1)
    max_tool_calls: int = Field(default=1, ge=0, le=1)
    max_plan_bytes: int = Field(default=100_000, ge=1, le=1_000_000)
    graph_steps: int = Field(default=10, ge=1, le=20)
    model_timeout: float = Field(default=5.0, gt=0, le=125)
    workflow_timeout: float = Field(default=90.0, gt=0, le=300)


class Planner(Protocol):
    async def generate(self, context: dict) -> str: ...


@dataclass
class FakeLLM:
    """A fixture response is still untrusted model output, never authorization."""

    response: str
    calls: int = 0
    contexts: list[dict] = field(default_factory=list)

    async def generate(self, context: dict) -> str:
        self.calls += 1
        self.contexts.append(json.loads(json.dumps(context)))
        return self.response


class GraphState(TypedDict):
    # Raw model output, account environment and HTTP bodies never enter graph state.
    ready: bool


def parse_plan(raw: str, limit: int) -> Scenario:
    return Scenario.model_validate(bounded_json(raw, limit))


class Workflow:
    """NL plans need a deterministic goal validator; fixed approved plans opt in explicitly."""

    def __init__(
        self, contract_document: bytes, config: TargetConfig, registry: PolicyRegistry,
        planner: Planner, store: ReportStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None, environ: dict[str, str] | None = None,
        budget: WorkflowBudget | None = None,
        *, user_task: str = "执行操作员提供的测试计划",
        task_mode: Literal["natural_language", "operator_plan"] = "natural_language",
        investigation: InvestigationConfig | None = None,
        capabilities: ReadOnlyCapabilityRegistry | None = None, analyzer=None,
        intent_validator: Callable[[str, Scenario], None] | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        if task_mode not in {"natural_language", "operator_plan"}:
            raise ValueError("TASK_MODE_INVALID")
        if task_mode == "operator_plan" and type(planner) is not FakeLLM:
            raise ValueError("OPERATOR_PLAN_REQUIRES_FIXED_PLANNER")
        self._document = bytes(contract_document)
        self._config = TargetConfig.model_validate(config.model_dump())
        self._registry = registry.snapshot()
        self._planner = planner
        self._store = store or ReportStore(Path("runs"))
        self._transport = transport
        # Executor captures environment once; a planner is never given this mapping.
        import os

        self._environ = dict(os.environ if environ is None else environ)
        self._budget = (budget or WorkflowBudget()).model_copy(deep=True)
        self._user_task = user_task
        self._task_mode = task_mode
        self._investigation = (investigation or InvestigationConfig()).model_copy(deep=True)
        self._capabilities = (capabilities or ReadOnlyCapabilityRegistry()).snapshot()
        self._analyzer = analyzer
        self._intent_validator = intent_validator
        self._execution_guard = execution_guard

    async def run(self) -> RunReport:
        report = RunReport(
            scenario_sha256=fingerprint(None), contract_sha256=fingerprint(self._document.hex()),
            environment=self._config.environment, target_origin=self._config.base_url,
            workflow_completed=False,
            task_mode=self._task_mode,
            task_satisfied=False if self._task_mode == "natural_language" else None,
        )
        executor: Executor | None = None
        contract: ContractSummary | None = None
        scenario: Scenario | None = None
        raw_plan: str | None = None

        def enter(node: str) -> None:
            report.workflow_nodes.append(node)
            self._store.save(report)

        def reject(outcome: Outcome, reason: str) -> dict:
            if report.investigations:
                # Post-execution tool/report failures cannot rewrite the original business result.
                report.investigation_error = reason
            else:
                report.outcome, report.reason = outcome, reason
            return {"ready": False}

        def summarize() -> None:
            report.analysis_code = {
                Outcome.PASS: (("COMPLETED" if report.intent_verified
                                else "APPROVED_PLAN_COMPLETED")
                               if report.workflow_completed else "POLICY_CHECKS_PASSED"),
                Outcome.ASSERTION_FAILED: "REVIEW_FAILURE_EVIDENCE",
                Outcome.TEST_INFRA_ERROR: "CHECK_TEST_ENVIRONMENT",
                Outcome.TOOL_ERROR: "FIX_PLAN_OR_TOOL",
                Outcome.NEEDS_REVIEW: "APPROVE_POLICY_OR_SCHEMA",
            }[report.outcome]

        async def load_contract(state: GraphState) -> dict:
            nonlocal executor, contract
            enter("load_contract")
            try:
                try:
                    validate_user_task(self._user_task)
                except ValueError:
                    return reject(Outcome.TOOL_ERROR, "USER_TASK_REJECTED")
                if CredentialVault(self._config, self._environ).contains_secret(self._user_task):
                    return reject(Outcome.TOOL_ERROR, "USER_TASK_REJECTED")
                contract = load_openapi(self._document)
                report.contract_sha256 = fingerprint(contract.model_dump())
                executor = Executor(self._config, contract, self._store, self._transport,
                                    self._environ, registry=self._registry,
                                    investigation=self._investigation,
                                    capabilities=self._capabilities, analyzer=self._analyzer)
                return {"ready": True}
            except ValidationFailure as exc:
                report.diagnostic = exc.diagnostic
                return reject(Outcome.TOOL_ERROR, "INVALID_CONTRACT")
            except ValueError:
                report.diagnostic = Diagnostic(layer=Layer.OPENAPI, code="OPENAPI_INVALID_CONTRACT")
                return reject(Outcome.TOOL_ERROR, "INVALID_CONTRACT")

        async def plan(state: GraphState) -> dict:
            nonlocal raw_plan
            enter("plan")
            if report.planner_calls >= self._budget.max_model_calls:
                return reject(Outcome.TEST_INFRA_ERROR, "MODEL_CALL_LIMIT")
            report.planner_calls += 1
            try:
                context = planning_context(contract, self._config, self._registry,
                                           self._user_task, Scenario.model_json_schema())
                if CredentialVault(self._config, self._environ).contains_secret(context):
                    return reject(Outcome.TOOL_ERROR, "PLANNER_INPUT_REJECTED")
                report.user_task_sha256 = fingerprint(self._user_task)
                async with asyncio.timeout(self._budget.model_timeout):
                    raw_plan = await self._planner.generate(context)
                return {"ready": True}
            except TimeoutError:
                return reject(Outcome.TEST_INFRA_ERROR, "MODEL_TIMEOUT")
            except PlannerFailure as exc:
                report.diagnostic = exc.diagnostic
                return reject(exc.outcome, exc.reason)
            except Exception:
                return reject(Outcome.TOOL_ERROR, "PLANNER_ERROR")
            finally:
                from agenttest.report import ModelCallStats

                stats = getattr(self._planner, "stats", None)
                if isinstance(stats, ModelCallStats):
                    report.model_call = stats.model_copy(deep=True)

        async def validate(state: GraphState) -> dict:
            nonlocal scenario, raw_plan
            enter("validate")
            try:
                scenario = parse_plan(raw_plan, self._budget.max_plan_bytes)
                raw_plan = None
                # Detect direct secret literals as well as prohibited binding mechanisms.
                vault = CredentialVault(self._config, self._environ)
                if vault.contains_secret(scenario.model_dump()):
                    raise ValidationFailure(Layer.DSL, "DSL_SECRET_LITERAL")
                executor.validate(scenario, report=report)
                report.plan_valid = True
                report.selected_policy_ids = [s.policy_id for s in scenario.steps]
                report.plan_structure = [PlanStepEvidence(
                    step_id=s.id, operation_id=s.operation_id, role=s.role, policy_id=s.policy_id,
                    argument_sha256={f"{loc}.{key}": fingerprint(b.value)
                                     for loc in ("body", "query", "path_parameters")
                                     for key, b in getattr(s, loc).items() if b.from_step is None},
                    dependencies={f"{loc}.{key}": b.from_step
                                  for loc in ("body", "query", "path_parameters")
                                  for key, b in getattr(s, loc).items() if b.from_step is not None},
                ) for s in scenario.steps]
                report.scenario_sha256 = fingerprint(scenario.model_dump())
                if self._task_mode == "natural_language":
                    validator = (validate_intent
                                 if self._config.business_scenario == "mini_target_nl_v1"
                                 else self._intent_validator)
                    if validator is None:
                        raise NeedsReview("INTENT_VALIDATOR_REQUIRED")
                    # A validator checks a copy; it cannot repair the executable plan.
                    validator(self._user_task, scenario.model_copy(deep=True))
                    report.intent_verified = True
                return {"ready": True}
            except NeedsReview as exc:
                report.diagnostic = exc.diagnostic
                report.validation_issue = "POLICY_OR_SCHEMA_REVIEW"
                return reject(Outcome.NEEDS_REVIEW, "REVIEW_REQUIRED")
            except ValidationFailure as exc:
                report.diagnostic = exc.diagnostic
                report.validation_issue = exc.diagnostic.code
                return reject(Outcome.TOOL_ERROR, "PLAN_REJECTED")
            except ValidationError as exc:
                report.diagnostic = schema_diagnostic(exc, Layer.DSL)
                report.validation_issue = report.diagnostic.code
                return reject(Outcome.TOOL_ERROR, "PLAN_REJECTED")
            except (ValueError, TypeError, RecursionError):
                report.diagnostic = Diagnostic(layer=Layer.DSL, code="DSL_INVALID_PLAN")
                report.validation_issue = report.diagnostic.code
                return reject(Outcome.TOOL_ERROR, "PLAN_REJECTED")
            finally:
                raw_plan = None

        async def execute(state: GraphState) -> dict:
            enter("execute")
            if report.tool_calls >= self._budget.max_tool_calls:
                return reject(Outcome.TEST_INFRA_ERROR, "TOOL_CALL_LIMIT")
            report.tool_calls += 1
            if self._execution_guard is not None:
                try:
                    self._execution_guard()
                except Exception:
                    return reject(Outcome.TEST_INFRA_ERROR, "EXECUTION_PREFLIGHT_FAILED")
            try:
                await executor.run(scenario, report=report)
                return {"ready": True}
            except Exception:
                return reject(Outcome.TOOL_ERROR, "EXECUTOR_ERROR")

        async def analyze_or_finish(state: GraphState) -> dict:
            enter("analyze_or_finish")
            # Analysis cannot change an expectation, replan or replay a mutation.
            summarize()
            return {"ready": True}

        async def finish_report(state: GraphState) -> dict:
            enter("report")
            return {"ready": True}

        builder = StateGraph(GraphState)
        for name, node in (("load_contract", load_contract), ("plan", plan),
                           ("validate", validate), ("execute", execute),
                           ("analyze_or_finish", analyze_or_finish), ("report", finish_report)):
            builder.add_node(name, node)
        builder.add_edge(START, "load_contract")
        for name, following in (("load_contract", "plan"), ("plan", "validate"),
                                ("validate", "execute")):
            builder.add_conditional_edges(name, lambda s: s["ready"],
                                          {True: following, False: "analyze_or_finish"})
        builder.add_edge("execute", "analyze_or_finish")
        builder.add_edge("analyze_or_finish", "report")
        builder.add_edge("report", END)
        graph = builder.compile()
        graph_completed = False
        try:
            # Overrides inherited tracing environment without mutating process globals.
            with tracing_context(enabled=False):
                async with asyncio.timeout(self._budget.workflow_timeout):
                    await graph.ainvoke({"ready": True}, config={
                        "recursion_limit": self._budget.graph_steps, "callbacks": [],
                    })
            # Publish success only after the entire graph returns, never in its last node.
            report.completed = report.workflow_completed = True
            report.task_satisfied = (report.intent_verified and report.outcome == Outcome.PASS
                                     if self._task_mode == "natural_language" else None)
            summarize()
            self._store.save(report)
            graph_completed = True
        except GraphRecursionError:
            reject(Outcome.TEST_INFRA_ERROR, "GRAPH_STEP_LIMIT")
        except TimeoutError:
            reject(Outcome.TEST_INFRA_ERROR, "WORKFLOW_TIMEOUT")
        except Exception:
            reject(Outcome.TOOL_ERROR, "WORKFLOW_ERROR")
        finally:
            raw_plan = None
            if not graph_completed:
                report.completed = True
                report.workflow_completed = False
                report.task_satisfied = (False if self._task_mode == "natural_language" else None)
                summarize()
                try:
                    self._store.save(report)
                except OSError:
                    if not report.investigations:
                        raise
                    report.investigation_error = "INVESTIGATION_REPORT_ERROR"
        # Graph nodes share a single event loop; no cancellable thread may keep changing a report.
        return report.model_copy(deep=True)
