"""Serial planning measurements around the trusted mini workflow; no oracle fallback."""

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field

from agenttest.composed_demo import ComposedDraftPlanner, composed_inputs
from agenttest.composed_evaluation import (
    CompositionPlanEvidence,
    capture_composition,
    reference_draft,
    score_composed_report,
)
from agenttest.composed_intent import (
    is_composed_task_preflight,
    requires_composed_task_preflight,
    validate_composed_intent,
)
from agenttest.composed_planning_protocol import (
    PlanningConfig,
    PlanningFreeze,
    PlanningLedger,
    planning_inputs,
    planning_settings,
    verify_live_proposal,
)
from agenttest.composed_review import PlanningSuite
from agenttest.development_ab import private_output
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.runner import ContextPlanner, ObservedTransport, load_responses
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerFailure
from agenttest.policies import PolicyRegistry, RegisteredPolicy
from agenttest.report import ModelCallStats, Outcome, ReportStore, RunReport, fingerprint
from agenttest.safety import StrictModel


class PlanningRow(StrictModel):
    case_id: str = Field(pattern=r"^P[0-9]{3}$")
    source: Literal["fake", "mock_transport", "real"]
    review_status: Literal["UNREVIEWED", "HUMAN_REVIEWED"]
    reserved: bool = False
    attempted: bool = False
    run_id: str | None = None
    report_sha256: str | None = None
    plan: CompositionPlanEvidence | None = None
    grade: dict | None = None
    classification: str = "NOT_ATTEMPTED_BUDGET"
    reason: str | None = None
    model_calls: int = Field(default=0, ge=0, le=1)
    real_model_calls: int = Field(default=0, ge=0, le=1)
    model_stats: ModelCallStats | None = None
    usage_status: Literal["NOT_APPLICABLE", "SIMULATED", "OBSERVED", "UNKNOWN"] = "NOT_APPLICABLE"
    workflow_elapsed_ms: float | None = None


class FrozenContextPlanner(ContextPlanner):
    def __init__(self, delegate, context, guard):
        super().__init__(delegate, context, context)
        self.guard = guard

    async def generate(self, context):
        try:
            self.guard()
        except (ValueError, OSError):
            raise PlannerFailure(Outcome.TEST_INFRA_ERROR, "PLANNING_FREEZE_CHANGED") from None
        return await super().generate(context)


def result_classification(report, grade):
    if grade["rejection_kind"] == "task_preflight":
        return "TASK_PREFLIGHT_REJECTED"
    return (
        "SAFE_REJECTION"
        if grade["safe_rejection"]
        else "PASS"
        if grade["end_to_end_success"]
        else "TASK_MISMATCH"
        if report.outcome == Outcome.PASS
        else report.outcome.value
    )


def report_digest(report: RunReport) -> str:
    # Default 0 and parsed 0.0 must have the same typed JSON identity.
    normalized = RunReport.model_validate_json(report.model_dump_json())
    return fingerprint(normalized.model_dump(mode="json"))


def planning_grade(case, report: RunReport, evidence, registry, source: str) -> dict:
    result = score_composed_report(case, report, evidence, registry)
    result.update(source=source, review_status=case.review_status, rejection_kind=None)
    code = report.diagnostic.code if report.diagnostic else report.reason
    planner_rejected = (
        case.rejection_code is not None
        and code in {"MODEL_NO_APPROVED_PLAN", "MODEL_REFUSAL"}
        and report.user_task_sha256 == fingerprint(case.user_task)
        and report.outcome == Outcome.NEEDS_REVIEW
        and report.workflow_completed is True
        and not report.steps
        and report.tool_calls == 0
        and report.task_satisfied is False
    )
    if result["safe_rejection"] and is_composed_task_preflight(report):
        result["rejection_kind"] = "task_preflight"
    elif planner_rejected:
        result.update(safe_rejection=True, mechanism_verified=True, rejection_kind="planner")
    elif result["safe_rejection"]:
        result["rejection_kind"] = "safety_gate"
    return result


def planning_summary(
    suite: PlanningSuite, config: PlanningConfig, rows: list[PlanningRow], ledger: PlanningLedger
) -> dict:
    if [r.case_id for r in rows] != [c.case_id for c in suite.cases]:
        raise ValueError("Every requested case must retain a result")
    supported = [r for c, r in zip(suite.cases, rows) if c.expected]
    rejected = [r for c, r in zip(suite.cases, rows) if c.rejection_code]
    usage = [r for r in rows if r.model_calls]
    total = (
        sum(r.model_stats.total_tokens for r in usage)
        if usage and all(r.model_stats and r.model_stats.total_tokens is not None for r in usage)
        else None
    )
    result = {
        "schema_version": suite.schema_version,
        "dataset_version": suite.dataset_version,
        "source": config.source,
        "split": suite.split,
        "formal_eval_v2_score": None,
        "classification": "DEVELOPMENT_ONLY"
        if suite.split == "development"
        else "CONTROLLED_PLANNING_EXPERIMENT",
        "requested": len(rows),
        "attempted": sum(r.attempted for r in rows),
        "not_attempted": sum(not r.attempted for r in rows),
        "human_reviewed": sum(c.review_status == "HUMAN_REVIEWED" for c in suite.cases),
        "supported": len(supported),
        "supported_success": sum(
            bool(r.grade and r.grade["end_to_end_success"]) for r in supported
        ),
        "rejection_cases": len(rejected),
        "planner_safe_rejections": sum(
            bool(r.grade and r.grade["rejection_kind"] == "planner") for r in rejected
        ),
        "gate_safe_rejections": sum(
            bool(r.grade and r.grade["rejection_kind"] == "safety_gate") for r in rejected
        ),
        "model_calls": sum(r.model_calls for r in rows),
        "real_model_calls": sum(r.real_model_calls for r in rows),
        "reported_tokens": total,
        "usage_kind": "SIMULATED"
        if config.source == "mock_transport"
        else "OBSERVED"
        if config.source == "real"
        else "NOT_APPLICABLE",
        "ledger": asdict(ledger),
        "java_requests": 0,
        "real_database_writes": 0,
        "results": [r.model_dump(mode="json") for r in rows],
    }
    preflights = sum(bool(r.grade and r.grade["rejection_kind"] == "task_preflight") for r in rows)
    if preflights:
        result.update(task_preflight_rejections=preflights,
                      unprocessed=sum(r.run_id is None for r in rows))
    return result


def write_planning_fixtures(suite: PlanningSuite, source: str, output: Path) -> None:
    """Explicit label-reading baseline generator; never called from the evaluation runner."""
    if source not in {"fake", "mock_transport"} or suite.split != "development":
        raise ValueError("Only explicit development fixtures can be generated")
    fixtures = {}
    for case in suite.cases:
        draft = (
            reference_draft(case.expected).model_dump()
            if case.expected
            else {"name": "explicit fixture abstention", "steps": []}
        )
        fixtures[case.case_id] = (
            draft
            if source == "fake"
            else {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(draft, ensure_ascii=False)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
        )
    save_evaluation(fixtures, output)


async def run_planning(
    suite: PlanningSuite,
    config: PlanningConfig,
    output: Path,
    *,
    root: Path,
    responses_path: Path | None = None,
    suite_path: Path | None = None,
    settings: ModelSettings | None = None,
    review_lock: Path | None = None,
    proposal_path: Path | None = None,
) -> dict:
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    suite = PlanningSuite.model_validate(suite.model_dump())
    config = PlanningConfig.model_validate(config.model_dump())
    if config.source == "real":
        if not config.real_opt_in or settings is None or responses_path is not None:
            raise ValueError("Live calls require explicit opt-in, credentials and no fixtures")
        private_output(root, output)
    elif (
        config.real_opt_in
        or settings is not None
        or review_lock
        or proposal_path
        or suite.split != "development"
    ):
        raise ValueError("Offline runs do not accept live credentials or approvals")
    settings = planning_settings(config, settings)
    if settings.api_key.get_secret_value() in json.dumps(suite.model_dump(), ensure_ascii=False):
        raise ValueError("Credential literal in planning enrollment")
    responses = load_responses(responses_path) if responses_path else {}
    if config.source != "real" and set(responses) != {c.case_id for c in suite.cases}:
        raise ValueError("Explicit fixtures required for every selected offline case")
    frozen = PlanningFreeze(
        root,
        suite,
        config,
        output,
        suite_path=suite_path,
        responses_path=responses_path,
        review_lock=review_lock,
    )
    if config.source == "real":
        verify_live_proposal(proposal_path, frozen)
    contexts, _, _, registry = planning_inputs(suite, config)
    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(frozen.manifest, output / "freeze.json")
    save_evaluation(contexts, output / "contexts.json")
    ledger, rows, store = PlanningLedger(), [], ReportStore(output / "reports")
    for case in suite.cases:
        row = PlanningRow(
            case_id=case.case_id, source=config.source, review_status=case.review_status
        )
        try:
            frozen.verify()
        except (OSError, ValueError):
            ledger.stop = "PLANNING_FREEZE_CHANGED"
        preflight = requires_composed_task_preflight(case.user_task)
        may_preflight = preflight and ledger.stop in {None, "NOT_ATTEMPTED_BUDGET"}
        if ((ledger.stop and not may_preflight)
                or (not preflight and not ledger.reserve(config))):
            row.reason = ledger.stop
            row.classification = (
                "NOT_ATTEMPTED_BUDGET"
                if ledger.stop == "NOT_ATTEMPTED_BUDGET"
                else "NOT_ATTEMPTED_AFTER_STOP"
            )
        else:
            row.reserved = not preflight
            if row.reserved:
                save_evaluation(asdict(ledger), output / (case.case_id + ".reservation.json"))
            observed = None
            if preflight:
                delegate = FakeLLM("TASK_PREFLIGHT_MUST_NOT_PLAN")
            elif config.source == "fake":
                delegate = ComposedDraftPlanner(FakeLLM(json.dumps(responses[case.case_id])))
            else:
                fixture = responses.get(case.case_id)
                transport = (
                    httpx.AsyncHTTPTransport(retries=0)
                    if config.source == "real"
                    else httpx.MockTransport(
                        lambda request, f=fixture: httpx.Response(
                            f.get("http_status", 200), json=f.get("body", f)
                        )
                    )
                )
                observed = ObservedTransport(transport)
                if observed.source != config.source:
                    raise ValueError("Actual transport provenance mismatch")
                delegate = OpenAICompatiblePlanner(settings, observed, profile="mini_composed")
            planner = FrozenContextPlanner(delegate, contexts[case.case_id], frozen.verify)
            target, target_config, target_registry, environ = composed_inputs()
            target_config.max_requests = min(target_config.max_requests, config.max_http_requests)
            started = time.monotonic()
            try:
                report = await Workflow(
                    json.dumps(target.openapi()).encode(),
                    target_config,
                    target_registry,
                    planner,
                    store,
                    httpx.ASGITransport(target, raise_app_exceptions=False),
                    environ,
                    WorkflowBudget(
                        model_timeout=config.model_timeout + 5,
                        workflow_timeout=config.workflow_timeout,
                    ),
                    user_task=case.user_task,
                    intent_validator=validate_composed_intent,
                    execution_guard=frozen.verify,
                ).run()
                row.workflow_elapsed_ms = round((time.monotonic() - started) * 1000, 3)
                row.attempted, row.run_id = planner.attempted, report.run_id
                row.report_sha256 = report_digest(report)
                row.model_stats = report.model_call
                row.model_calls = observed.calls if observed else 0
                row.real_model_calls = row.model_calls if config.source == "real" else 0
                ledger.calls += row.model_calls
                ledger.real_calls += row.real_model_calls
                if report.plan_valid and planner.plan is not None:
                    row.plan = capture_composition(planner.plan, registry)
                row.grade = planning_grade(case, report, row.plan, registry, config.source)
                row.classification = result_classification(report, row.grade)
                row.reason = row.grade["diagnostic"]
                if row.model_calls:
                    row.usage_status = (
                        "SIMULATED" if config.source == "mock_transport" else "OBSERVED"
                    )
                    if row.model_stats is None or row.model_stats.total_tokens is None:
                        row.usage_status, ledger.stop = "UNKNOWN", "MODEL_USAGE_UNAVAILABLE"
                if row.model_stats and row.model_stats.reason not in {
                    "MODEL_OUTPUT_RECEIVED",
                    "MODEL_NO_APPROVED_PLAN",
                    "MODEL_REFUSAL",
                }:
                    ledger.stop = ledger.stop or row.model_stats.reason or "MODEL_FAILURE"
                if not row.attempted and row.grade["rejection_kind"] != "task_preflight":
                    ledger.stop = (
                        "PLANNING_FREEZE_CHANGED"
                        if report.reason == "PLANNING_FREEZE_CHANGED"
                        else ledger.stop or "PLANNING_PREFLIGHT_FAILED"
                    )
            except asyncio.CancelledError:
                try:
                    save_evaluation(
                        {
                            "completed": False,
                            "reason": "RUN_CANCELLED",
                            "case_id": case.case_id,
                            "reservation": asdict(ledger),
                            "current_model_calls": observed.calls if observed else 0,
                        },
                        output / "abort.json",
                    )
                except OSError:
                    pass
                raise
            finally:
                planner.output, planner.plan = None, None
        save_evaluation(row.model_dump(mode="json"), output / (case.case_id + ".json"))
        rows.append(row)
    result = planning_summary(suite, config, rows, ledger)
    save_evaluation(result, output / "results.json")
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write(
            f"# 组合任务规划评测\n\n来源：{config.source}；独立正式 Eval v2 成绩：未生成。\n\n"
            f"请求 {len(rows)} 项，规划尝试 {result['attempted']} 项，真实模型调用 "
            f"{result['real_model_calls']} 次。\n"
        )
    seal(output)
    verify_planning(output)
    return result


def verify_planning(directory: Path) -> dict:
    manifest = read_json(directory / "archive.json")
    if manifest.get("schema_version") != "agenttest.eval.archive.v1" or manifest.get(
        "files"
    ) != inventory(directory):
        raise ValueError("Planning archive integrity mismatch")
    frozen = read_json(directory / "freeze.json")
    suite, config = (
        PlanningSuite.model_validate(frozen["suite"]),
        PlanningConfig(**frozen["config"]),
    )
    registry = PolicyRegistry([RegisteredPolicy.model_validate(p) for p in frozen["registry"]])
    if (
        frozen.get("schema_version") != "agenttest.composition.planning.freeze.v1"
        or fingerprint(suite.model_dump()) != frozen["suite_sha256"]
        or registry.digest() != frozen["inputs"]["policy_sha256"]
        or fingerprint(read_json(directory / "contexts.json")) != frozen["contexts_sha256"]
    ):
        raise ValueError("Planning freeze identity mismatch")
    if config.source == "real":
        lock = frozen.get("review_lock")
        records = [c.review_record.model_dump() if c.review_record else None for c in suite.cases]
        if (
            not lock
            or fingerprint(lock) != frozen.get("review_lock_sha256")
            or suite.split != "independent_holdout"
            or any(c.review_status != "HUMAN_REVIEWED" for c in suite.cases)
            or lock.get("suite_sha256") != frozen["suite_sha256"]
            or lock.get("review_records_sha256") != fingerprint(records)
            or any(frozen["inputs"].get(k) != v for k, v in lock.get("inputs", {}).items())
        ):
            raise ValueError("Archived independent review identity mismatch")
    elif suite.split != "development" or frozen.get("review_lock"):
        raise ValueError("Offline diagnostics cannot claim independently reviewed results")
    result, rows, run_ids = read_json(directory / "results.json"), [], set()
    reserved = PlanningLedger()
    for case in suite.cases:
        row = PlanningRow.model_validate(read_json(directory / (case.case_id + ".json")))
        if (
            row.case_id != case.case_id
            or row.source != config.source
            or row.review_status != case.review_status
        ):
            raise ValueError("Planning case provenance mismatch")
        if row.reserved:
            if not reserved.reserve(config) or read_json(
                directory / (case.case_id + ".reservation.json")
            ) != asdict(reserved):
                raise ValueError("Planning reservation journal mismatch")
        elif (directory / (case.case_id + ".reservation.json")).exists():
            raise ValueError("Unexpected planning reservation")
        if row.run_id:
            ReportStore.validate_id(row.run_id)
            report = RunReport.model_validate(
                read_json(directory / "reports" / row.run_id / "report.json")
            )
            if not row.reserved and (
                not is_composed_task_preflight(report) or row.attempted or row.model_calls
                or row.real_model_calls or row.model_stats is not None or row.plan is not None
                or not row.grade or row.grade["rejection_kind"] != "task_preflight"
            ):
                raise ValueError("Only a verified task preflight may omit a reservation")
            if (
                row.run_id in run_ids
                or report.run_id != row.run_id
                or report_digest(report) != row.report_sha256
                or not (
                    report.contract_sha256 == frozen["inputs"]["contract_sha256"]
                    or report.contract_sha256 == frozen["inputs"]["raw_contract_sha256"]
                    and not report.plan_valid
                    and not report.steps
                    and not row.plan
                    and not row.model_calls
                )
                or report.model_call != row.model_stats
                or planning_grade(case, report, row.plan, registry, config.source) != row.grade
                or row.classification != result_classification(report, row.grade)
                or row.reason != row.grade["diagnostic"]
            ):
                raise ValueError("Planning report/rescore mismatch")
            run_ids.add(row.run_id)
        elif (
            row.grade
            or row.plan
            or row.attempted
            or row.reserved
            or row.model_calls
            or row.model_stats
            or row.report_sha256
            or row.workflow_elapsed_ms is not None
            or row.classification not in {"NOT_ATTEMPTED_BUDGET", "NOT_ATTEMPTED_AFTER_STOP"}
            or row.reason is None
        ):
            raise ValueError("Unattempted case cannot claim execution or model evidence")
        if (
            row.real_model_calls != (row.model_calls if config.source == "real" else 0)
            or config.source == "fake"
            and row.model_calls
            or row.model_stats
            and row.model_stats.calls != row.model_calls
        ):
            raise ValueError("Planning model-call provenance mismatch")
        expected_usage = (
            "NOT_APPLICABLE"
            if not row.model_calls
            else "UNKNOWN"
            if row.model_stats is None or row.model_stats.total_tokens is None
            else "SIMULATED"
            if config.source == "mock_transport"
            else "OBSERVED"
        )
        if row.usage_status != expected_usage:
            raise ValueError("Planning usage provenance mismatch")
        reserved.calls += row.model_calls
        reserved.real_calls += row.real_model_calls
        rows.append(row)
    ledger = PlanningLedger(**result["ledger"])
    if (
        ledger.slots != sum(r.reserved for r in rows)
        or ledger.reserved_tokens != ledger.slots * config.token_reservation
        or ledger.slots > config.max_calls
        or ledger.reserved_tokens > config.max_total_tokens
        or ledger.calls != sum(r.model_calls for r in rows)
        or ledger.calls > ledger.slots
        or ledger.real_calls != sum(r.real_model_calls for r in rows)
        or planning_summary(suite, config, rows, ledger) != result
    ):
        raise ValueError("Planning ledger/summary mismatch")
    return {
        "archive_verified": True,
        "rescored_cases": len(rows),
        "real_model_calls": ledger.real_calls,
        "formal_eval_v2_score": None,
    }
