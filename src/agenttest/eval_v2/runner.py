"""Serial, zero-network defaults around the existing Workflow and mini fixture."""

import json
import time
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, model_validator

from agenttest.contracts import load_openapi
from agenttest.discovery import discover
from agenttest.dsl import ArgumentBinding, Scenario, Step
from agenttest.eval_v2.context import build_context
from agenttest.eval_v2.faults import pair_result, summarize_faults
from agenttest.eval_v2.freeze import Freeze
from agenttest.eval_v2.models import CaseResult, Source, Suite
from agenttest.eval_v2.scoring import capture_plan, score_report, summarize
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerFailure
from agenttest.natural_demo import natural_inputs, run_natural_demo
from agenttest.plan_json import bounded_json
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.safety import StrictModel


class EvalConfig(StrictModel):
    source: Source = "manual"
    variant: Literal["A", "B"] = "A"
    development: bool = False
    candidate_max_bytes: int = Field(default=6000, ge=256, le=16_000)
    max_cases_attempted: int = Field(default=200, ge=0, le=200)
    real_opt_in: bool = False
    max_calls: int = Field(default=0, ge=0, le=200)
    max_total_tokens: int = Field(default=0, ge=0)
    allowed_model: str | None = None
    allowed_endpoint_sha256: str | None = None
    allowed_target: Literal["mini_nl_v1"] = "mini_nl_v1"

    @model_validator(mode="after")
    def explicit_real(self):
        if self.source == "real" and (
            not self.real_opt_in
            or self.development
            or not self.max_calls
            or not self.max_total_tokens
            or not self.allowed_model
            or not self.allowed_endpoint_sha256
        ):
            raise ValueError(
                "Real calls need formal holdout, opt-in, identity and explicit budgets"
            )
        return self


def oracle_plan(case) -> Scenario | None:
    """Label-reading oracle baseline, explicitly never a model planning claim."""
    rubric = case.labels.rubric
    if rubric is None:
        return None
    # Topologically order partial-order labels, including producer/consumer edges.
    edges = list(rubric.before) + [
        (a.source, s.key) for s in rubric.steps for a in s.arguments if a.source is not None
    ]
    pending, ordered = list(rubric.steps), []
    while pending:
        ready = next(
            s
            for s in pending
            if all(a not in {p.key for p in pending} for a, b in edges if b == s.key)
        )
        ordered.append(ready)
        pending.remove(ready)
    steps = []
    for label in ordered:
        step = Step(
            id=label.key,
            operation_id=label.operation_id,
            role=label.role,
            policy_id=label.policy_id,
        )
        for arg in label.arguments:
            getattr(step, arg.location)[arg.name] = (
                ArgumentBinding(value=arg.value)
                if arg.source is None
                else ArgumentBinding(from_step=arg.source, select=arg.selector)
            )
        steps.append(step)
    return Scenario(name="Eval v2 manual oracle", steps=steps)


class ObservedTransport(httpx.AsyncBaseTransport):
    """Provenance follows the actual transport, not a caller-supplied baseline label."""

    def __init__(self, inner):
        if type(inner) is httpx.MockTransport:
            self.source = "mock_transport"
        elif type(inner) is httpx.AsyncHTTPTransport:
            self.source = "real"
        else:
            raise ValueError("Unaudited model transport")
        self.inner, self.calls = inner, 0

    async def handle_async_request(self, request):
        self.calls += 1
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


class ContextPlanner:
    """Capture a volatile plan including selectors without modifying Workflow/report schema."""

    def __init__(self, delegate, expected_context, context):
        self.delegate, self.expected_context, self.context = delegate, expected_context, context
        self.output, self.plan, self.attempted = None, None, False

    @property
    def stats(self):
        return getattr(self.delegate, "stats", None)

    async def generate(self, original):
        if fingerprint(original) != fingerprint(self.expected_context):
            raise ValueError("Planning input drift")
        self.attempted = True
        self.output = await self.delegate.generate(json.loads(json.dumps(self.context)))
        try:
            self.plan = Scenario.model_validate(bounded_json(self.output, 100_000))
        except ValueError:
            self.plan = None
        return self.output


class Reject:
    async def generate(self, context):
        raise PlannerFailure(Outcome.NEEDS_REVIEW, "MODEL_NO_APPROVED_PLAN")


async def guarded_run(task, planner, store, budget, guard):
    from agenttest.workflow import Workflow

    target, target_config, registry, environ = natural_inputs()
    return await Workflow(
        json.dumps(target.openapi()).encode(),
        target_config,
        registry,
        planner,
        store,
        httpx.ASGITransport(target, raise_app_exceptions=False),
        environ,
        budget,
        user_task=task,
        execution_guard=guard,
    ).run()


def load_responses(path):
    with path.open("rb") as stream:
        raw = stream.read(1_000_001)
    responses = bounded_json(raw.decode(), 1_000_000)
    if not isinstance(responses, dict) or any(not isinstance(v, dict) for v in responses.values()):
        raise ValueError("Responses must map case IDs (or default) to fixed JSON")
    return responses


def prepare_inputs(suite, config):
    target, target_config, registry, _ = natural_inputs()
    contract = load_openapi(json.dumps(target.openapi()).encode())
    artifact = discover(contract, registry, target_config) if config.variant == "B" else None
    contexts, base_contexts = {}, {}
    for case in suite.cases:
        base_contexts[case.case_id] = build_context(
            case.task, contract, target_config, registry, Scenario.model_json_schema()
        )
        contexts[case.case_id] = build_context(
            case.task,
            contract,
            target_config,
            registry,
            Scenario.model_json_schema(),
            discovery=artifact,
            max_bytes=config.candidate_max_bytes,
        )
    inputs = {
        "contract_sha256": fingerprint(contract.model_dump()),
        "policy_sha256": registry.digest(),
        "target_config_sha256": fingerprint(target_config.model_dump()),
        "candidate_algorithm": artifact.algorithm_version if artifact else None,
        "candidate_artifact_sha256": fingerprint(artifact.model_dump()) if artifact else None,
        "contexts_sha256": fingerprint(contexts),
    }
    return base_contexts, contexts, inputs, artifact


async def evaluate(
    suite: Suite,
    config: EvalConfig,
    output: Path,
    *,
    root: Path,
    responses_path: Path | None = None,
    suite_path: Path | None = None,
    settings: ModelSettings | None = None,
) -> dict:
    """Only disposable ASGI business targets. Real opt-in applies solely to the model API."""
    from agenttest.workflow import FakeLLM, WorkflowBudget

    # Snapshot caller-owned objects: no callback or model can mutate the rubric mid-run.
    suite = Suite.model_validate(suite.model_dump())
    config = EvalConfig.model_validate(config.model_dump())
    if not config.development:
        suite.require_formal()
    if config.source == "real" and (
        settings is None
        or settings.model != config.allowed_model
        or fingerprint(settings.base_url) != config.allowed_endpoint_sha256
    ):
        raise ValueError("Model identity/endpoint not explicitly allowed")
    if config.source != "real" and settings is not None:
        raise ValueError("Offline modes do not accept credentials")
    responses = load_responses(responses_path) if responses_path else {}
    if config.source in {"fake", "mock_transport"} and any(
        c.case_id not in responses and "default" not in responses
        for c in suite.cases
        if c.labels.expected_behavior != "review"
    ):
        raise ValueError("Fixed offline responses required; no oracle fallback")
    base_contexts, contexts, inputs, artifact = prepare_inputs(suite, config)
    freeze = Freeze(
        root,
        suite,
        inputs,
        config,
        output,
        settings=settings,
        suite_path=suite_path,
        response_path=responses_path,
        real=config.source == "real",
    )
    # Claim an entirely new directory before any planner/Executor invocation.
    output.mkdir(parents=True, exist_ok=False)
    freeze.save()
    if artifact is not None:
        save_evaluation(artifact.model_dump(mode="json"), output / "candidates.json")
    store = ReportStore(output / "reports")
    rows, pairs, calls, tokens, stop = [], [], 0, 0, None
    budget = WorkflowBudget(
        model_timeout=(settings.timeout + 1 if settings else 5),
        workflow_timeout=min(300, settings.timeout + 90) if settings else 90,
    )
    for case in suite.cases:
        row = CaseResult(
            case_id=case.case_id,
            category=case.category,
            source=config.source,
            variant=config.variant,
            classification="UNSUPPORTED",
            usage_status="NOT_APPLICABLE",
        )
        scheduled = case.labels.faults
        if case.labels.expected_behavior == "review":
            row.diagnostics = ["FUTURE_CAPABILITY_OR_LABEL_REVIEW"]
        elif (
            stop
            or sum(r.attempted for r in rows) >= config.max_cases_attempted
            or (
                config.source == "real"
                and (
                    calls >= config.max_calls
                    or tokens + settings.max_input_tokens + settings.max_output_tokens
                    > config.max_total_tokens
                )
            )
        ):
            stop = stop or "NOT_ATTEMPTED_BUDGET"
            row.classification = stop
        else:
            try:
                freeze.verify()
                # Reconstruct trusted inputs before each call, including Registry and context.
                if prepare_inputs(suite, config)[2] != inputs:
                    raise ValueError("Trusted input drift")
            except (ValueError, OSError):
                stop = row.classification = "EVALUATION_CONFIGURATION_ERROR"
                row.diagnostics = ["EVALUATION_FREEZE_CHANGED"]
            else:
                observed = None
                if config.source == "manual":
                    plan = oracle_plan(case)
                    selected = FakeLLM(plan.model_dump_json()) if plan else Reject()
                elif config.source == "fake":
                    selected = FakeLLM(
                        json.dumps(responses.get(case.case_id, responses.get("default")))
                    )
                else:
                    if config.source == "mock_transport":
                        envelope = responses.get(case.case_id, responses.get("default"))
                        observed = ObservedTransport(
                            httpx.MockTransport(
                                lambda request, body=envelope: httpx.Response(200, json=body)
                            )
                        )
                        model_settings = ModelSettings(
                            model="offline-fixture",
                            api_key="offline-only",
                            base_url="https://eval.invalid",
                            max_output_tokens=4096,
                        )
                    else:
                        observed = ObservedTransport(httpx.AsyncHTTPTransport(retries=0))
                        model_settings = settings
                    if observed.source != config.source:
                        raise ValueError("Transport provenance mismatch")
                    selected = OpenAICompatiblePlanner(model_settings, observed)
                planner = ContextPlanner(
                    selected, base_contexts[case.case_id], contexts[case.case_id]
                )
                started = time.monotonic()
                report = await guarded_run(
                    case.task.user_task, planner, store, budget, freeze.verify
                )
                row.workflow_elapsed_ms = (time.monotonic() - started) * 1000
                row.attempted = planner.attempted
                row.planner_invocations = report.planner_calls
                for key, value in score_report(case, report, planner.plan).items():
                    setattr(row, key, value)
                row.run_id = report.run_id
                row.report_sha256 = fingerprint(report.model_dump())
                row.plan_sha256 = fingerprint(planner.plan.model_dump()) if planner.plan else None
                if row.plan_valid and planner.plan is not None:
                    row.plan_evidence = capture_plan(planner.plan)
                row.context_sha256 = fingerprint(contexts[case.case_id])
                row.context_bytes = len(
                    json.dumps(contexts[case.case_id], ensure_ascii=False).encode()
                )
                row.http_requests = sum(len(s.attempts) for s in report.steps)
                row.executed = row.http_requests > 0
                row.http_attempt_elapsed_ms = (
                    sum(a.elapsed_ms for s in report.steps for a in s.attempts)
                    if row.executed
                    else None
                )
                if observed is not None:
                    row.model_calls = observed.calls
                    row.real_api_calls = observed.calls if observed.source == "real" else 0
                    stats = report.model_call
                    if stats is not None and observed.calls:
                        row.model_elapsed_ms = stats.elapsed_ms
                        row.input_tokens, row.output_tokens = (
                            stats.input_tokens,
                            stats.output_tokens,
                        )
                        row.total_tokens = stats.total_tokens
                        row.usage_status = (
                            "OBSERVED"
                            if stats.total_tokens is not None
                            else "MODEL_USAGE_UNAVAILABLE"
                        )
                    elif observed.calls:
                        row.usage_status = "MODEL_USAGE_UNAVAILABLE"
                    calls += row.model_calls
                    tokens += row.total_tokens or 0  # Internal budget only; never a usage estimate.
                    if row.usage_status == "MODEL_USAGE_UNAVAILABLE":
                        stop = "MODEL_USAGE_UNAVAILABLE"
                        row.diagnostics.append(stop)
                    if stats and (
                        stats.http_status in {401, 403, 404} or stats.reason == "MODEL_TOKEN_BUDGET"
                    ):
                        stop = "EVALUATION_CONFIGURATION_ERROR"
                if row.end_to_end_success and planner.output is not None:
                    for mechanism in scheduled:
                        # Same plan, fresh paired targets; no labels reach the planner.
                        normal = await run_natural_demo(
                            case.task.user_task, FakeLLM(planner.output), store, budget=budget
                        )
                        fault = await run_natural_demo(
                            case.task.user_task,
                            FakeLLM(planner.output),
                            store,
                            fault=mechanism,
                            budget=budget,
                        )
                        pair = pair_result(case.case_id, mechanism, normal, fault, True)
                        pairs.append(pair)
                        save_evaluation(pair, output / f"{case.case_id}-{mechanism}.json")
                planner.output, planner.plan = None, None
        for mechanism in scheduled:
            if not any(p["case_id"] == case.case_id and p["mechanism"] == mechanism for p in pairs):
                pairs.append(
                    {
                        "case_id": case.case_id,
                        "mechanism": mechanism,
                        "planned": bool(row.plan_valid and row.intent and row.intent.passed),
                        "executed": False,
                        "control_executed": False,
                        "false_positive": False,
                        "status": "NOT_EXECUTED",
                    }
                )
        rows.append(row)
        save_evaluation(row.model_dump(mode="json"), output / (case.case_id + ".json"))
    freeze_valid = True
    try:
        freeze.verify()
    except (ValueError, OSError):
        stop = "EVALUATION_CONFIGURATION_ERROR"
        freeze_valid = False
    result = {
        "schema_version": "agenttest.eval.v2",
        "source": config.source,
        "variant": config.variant,
        "dataset_version": suite.dataset_version,
        "formal_independent_evaluation": not config.development and stop is None,
        "freeze_valid": freeze_valid,
        "review_status": "DEVELOPMENT_ONLY" if config.development else "HUMAN_REVIEWED",
        "stop_reason": stop,
        "summary": summarize(suite, rows),
        "by_category": {
            category: summarize(
                Suite.model_validate(
                    {
                        **suite.model_dump(),
                        "cases": [c.model_dump() for c in suite.cases if c.category == category],
                    }
                ),
                [r for r in rows if r.category == category],
            )
            for category in sorted({c.category for c in suite.cases})
        },
        "fault_detection": summarize_faults(pairs),
        "pairs": pairs,
        "results": [r.model_dump(mode="json") for r in rows],
    }
    result["paired_http_requests"] = sum(
        p["normal"]["requests"] + p["fault"]["requests"] for p in pairs if "normal" in p
    )
    result["total_business_http_requests"] = (
        result["summary"]["http_requests"] + result["paired_http_requests"]
    )
    save_evaluation(result, output / "results.json")
    markdown = render_markdown(result)
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return result


def render_markdown(result):
    summary = result["summary"]
    lines = [
        "# Agent Eval v2",
        "",
        f"来源：{result['source']}；上下文：{result['variant']}；审核：{result['review_status']}。",
        "",
        f"requested={summary['requested']} / eligible={summary['eligible']} / "
        f"attempted={summary['attempted']} / executed={summary['executed']}",
        "",
        "| 指标 | 分子 / 分母 | 比例 |",
        "| --- | --- | --- |",
    ]
    for key in (
        "plan_validity",
        "conditional_plan_validity",
        "intent_satisfaction",
        "end_to_end_success",
        "safe_rejection",
    ):
        metric = summary[key]
        value = "不可计算" if metric["rate"] is None else f"{metric['rate']:.3f}"
        lines.append(f"| {key} | {metric['numerator']} / {metric['denominator']} | {value} |")
    lines += [
        "",
        f"真实模型 API 请求尝试：{summary['real_api_calls']}；"
        f"Token Usage：{summary['total_tokens']}。",
        "",
        "| Case | 分类 | 诊断 |",
        "| --- | --- | --- |",
    ]
    for row in result["results"]:
        lines.append(
            f"| {row['case_id']} | {row['classification']} | "
            + ", ".join(row["diagnostics"])
            + " |"
        )
    lines += ["", "开发/Fake/Mock 结果不支持真实模型泛化或 Candidate Discovery 增益结论。", ""]
    return "\n".join(lines)
