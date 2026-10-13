"""Bounded DEVELOPMENT_ONLY diagnostics; never enter or relax the formal real runner."""

import json
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, model_validator

from agenttest.eval_v2.archive import read_json, seal, verify_archive
from agenttest.eval_v2.comparison import compare
from agenttest.eval_v2.freeze import Freeze
from agenttest.eval_v2.models import CaseResult, Suite
from agenttest.eval_v2.runner import (
    ContextPlanner,
    EvalConfig,
    ObservedTransport,
    guarded_run,
    load_responses,
    prepare_inputs,
    render_markdown,
)
from agenttest.eval_v2.scoring import capture_plan, score_report, summarize
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import _git
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import ReportStore, fingerprint
from agenttest.safety import StrictModel


class DevelopmentConfig(StrictModel):
    """Fixed mini-target smoke limits shared across both groups, not per group."""

    development: Literal[True] = True
    source: Literal["fake", "mock_transport", "real"] = "mock_transport"
    variant: Literal["A", "B"] = "A"
    real_opt_in: bool = False
    allowed_model: str | None = None
    allowed_endpoint: str | None = None
    max_calls: int = Field(default=4, ge=0, le=4)
    max_total_tokens: int = Field(default=88_192, ge=0, le=88_192)
    candidate_max_bytes: int = Field(default=6000, ge=256, le=16_000)
    max_http_requests: Literal[40] = 40
    max_workflow_seconds: Literal[120] = 120

    @model_validator(mode="after")
    def explicit_live_identity(self):
        if self.source == "real" and (
            not self.real_opt_in or not self.allowed_model or not self.allowed_endpoint
        ):
            raise ValueError("Development real calls require explicit opt-in and identity")
        return self


@dataclass
class Ledger:
    """Reserve before invocation; never refund a slot or unknown token usage."""

    slots: int = 0
    reserved_tokens: int = 0
    calls: int = 0
    real_calls: int = 0
    observed_tokens: int = 0
    usage_complete: bool = True
    stop: str | None = None

    def reserve(self, config):
        if (self.slots >= config.max_calls
                or self.reserved_tokens + 22_048 > config.max_total_tokens):
            self.stop = "NOT_ATTEMPTED_BUDGET"
            return False
        self.slots += 1
        self.reserved_tokens += 22_048
        return True

    def public(self):
        return {**vars(self), "classification": "DEVELOPMENT_ONLY",
                "observed_tokens": self.observed_tokens if self.usage_complete else None}


def private_output(root: Path, output: Path):
    """Live evidence must be new, Git ignored, and inside this repository's runs/."""
    for path in (output, *output.parents):
        if path.exists() and (
            stat.S_ISLNK(path.lstat().st_mode)
            or getattr(path.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Linked output directory")
    if not output.resolve().is_relative_to((root / "runs").resolve()):
        raise ValueError("Private output must be under repository runs/")
    if not _git(root, "check-ignore", str(output.absolute())):
        raise ValueError("Private output must be Git ignored")


def development_suite(suite):
    suite = Suite.model_validate(suite.model_dump())
    if suite.split != "development" or len(suite.cases) > 2 or any(
        c.labels.review_status != "UNREVIEWED"
        or c.labels.label_provenance != "ai_generated_draft"
        or c.review_record is not None
        or c.labels.expected_behavior != "execute"
        or c.labels.faults
        for c in suite.cases
    ):
        raise ValueError("Only 1-2 UNREVIEWED AI development execute cases, no fault pairing")
    return suite


def pair_inputs(suite, config):
    # EvalConfig remains offline here. Never construct real+development EvalConfig.
    inputs = {}
    for variant in ("A", "B"):
        inputs[variant] = prepare_inputs(suite, EvalConfig(
            development=True, variant=variant, candidate_max_bytes=config.candidate_max_bytes,
            max_http_requests=config.max_http_requests,
        ))
    if inputs["A"][0] != inputs["B"][0]:
        raise ValueError("A/B base context mismatch")
    for key, context in inputs["B"][1].items():
        base = {k: v for k, v in context.items() if k != "candidate_discovery"}
        if base != inputs["A"][1][key]:
            raise ValueError("A/B differs outside candidate metadata")
    return inputs


def model_settings(config, settings):
    if config.source == "real":
        if settings is None or (
            settings.model != config.allowed_model or settings.base_url != config.allowed_endpoint
        ):
            raise ValueError("Model identity/endpoint mismatch")
        settings = settings.model_copy(deep=True)
    else:
        if settings is not None:
            raise ValueError("Offline diagnostics do not accept credentials")
        settings = ModelSettings(model="ecnu-plus", base_url="https://chat.ecnu.edu.cn/open/api/v1",
                                 protocol="chatecnu", api_key="offline-fixture-only")
    return ModelSettings.model_validate({**settings.model_dump(), "timeout": 30,
        "max_input_tokens": 20_000, "max_output_tokens": 2048, "max_total_tokens": 22_048})


def _mock_response(fixture):
    return httpx.Response(fixture.get("http_status", 200), json=fixture.get("body", fixture))


async def run_development_ab(suite, config, output, *, root, suite_path=None,
                             responses_path=None, settings=None):
    """One serial A+B invocation with one global budget, sealed evidence and no retries."""
    from agenttest.workflow import FakeLLM, WorkflowBudget

    suite, config = development_suite(suite), DevelopmentConfig.model_validate(config.model_dump())
    settings = model_settings(config, settings)
    if config.source == "real":
        private_output(root, output)
        if responses_path is not None:
            raise ValueError("Live diagnostics cannot accept fixed responses")
    responses = load_responses(responses_path) if responses_path else {}
    if config.source != "real" and any(c.case_id not in responses for c in suite.cases):
        raise ValueError("Explicit fixed responses required for every offline case")
    inputs = pair_inputs(suite, config)
    freezes = {v: Freeze(root, suite, inputs[v][2], config.model_copy(update={"variant": v}),
        output / v, settings=settings if config.source != "fake" else None,
        suite_path=suite_path, response_path=responses_path, real=config.source == "real")
        for v in ("A", "B")}
    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(suite.model_dump(mode="json"), output / "suite.json")
    for variant, freeze in freezes.items():
        freeze.save()
        save_evaluation(inputs[variant][1], output / variant / "contexts.json")
        if inputs[variant][3] is not None:
            save_evaluation(inputs[variant][3].model_dump(mode="json"),
                            output / variant / "candidates.json")

    def guard():
        for v, frozen in freezes.items():
            frozen.verify()
            if read_json(output / v / "freeze.json") != frozen.manifest:
                raise ValueError("On-disk freeze drift")
        current = pair_inputs(suite, config)
        if any(current[v][2] != inputs[v][2] for v in freezes):
            raise ValueError("Trusted context drift")

    ledger, groups = Ledger(), {}
    budget = WorkflowBudget(model_timeout=31, workflow_timeout=120)
    for variant in ("A", "B"):
        rows = []
        store = ReportStore(output / variant / "reports")
        for case in suite.cases:
            row = CaseResult(case_id=case.case_id, category=case.category, source=config.source,
                             variant=variant, classification="UNSUPPORTED",
                             usage_status="NOT_APPLICABLE")
            try:
                guard()
            except (ValueError, OSError):
                ledger.stop = "EVALUATION_FREEZE_CHANGED"
            if ledger.stop or not ledger.reserve(config):
                row.classification = ("NOT_ATTEMPTED_BUDGET"
                    if ledger.stop == "NOT_ATTEMPTED_BUDGET" else (
                        "EVALUATION_CONFIGURATION_ERROR"
                        if ledger.stop == "EVALUATION_FREEZE_CHANGED" else "NEEDS_REVIEW"))
                row.diagnostics = ["NOT_ATTEMPTED_AFTER_STOP", ledger.stop]
            else:
                observed = None
                if config.source == "fake":
                    delegate = FakeLLM(json.dumps(responses[case.case_id]))
                else:
                    transport = (httpx.AsyncHTTPTransport(retries=0) if config.source == "real"
                        else httpx.MockTransport(lambda request, f=responses[case.case_id]:
                                                 _mock_response(f)))
                    observed = ObservedTransport(transport)
                    if observed.source != config.source:
                        raise ValueError("Transport provenance mismatch")
                    delegate = OpenAICompatiblePlanner(settings, observed)
                planner = ContextPlanner(delegate, inputs[variant][0][case.case_id],
                                          inputs[variant][1][case.case_id])
                started = time.monotonic()
                report = await guarded_run(case.task.user_task, planner, store, budget, guard,
                                           max_requests=config.max_http_requests)
                row.workflow_elapsed_ms = (time.monotonic() - started) * 1000
                row.attempted, row.planner_invocations = planner.attempted, report.planner_calls
                for key, value in score_report(case, report, planner.plan).items():
                    setattr(row, key, value)
                row.run_id, row.report_sha256 = report.run_id, fingerprint(report.model_dump())
                if row.plan_valid and planner.plan is not None:
                    row.plan_evidence = capture_plan(planner.plan)
                    row.plan_sha256 = fingerprint(planner.plan.model_dump())
                row.context_sha256 = fingerprint(inputs[variant][1][case.case_id])
                row.context_bytes = len(json.dumps(inputs[variant][1][case.case_id],
                                                  ensure_ascii=False).encode())
                row.http_requests = sum(len(s.attempts) for s in report.steps)
                row.executed = row.http_requests > 0
                row.http_attempt_elapsed_ms = sum(a.elapsed_ms for s in report.steps
                                                  for a in s.attempts) if row.executed else None
                if observed is not None:
                    row.model_calls = observed.calls
                    row.real_api_calls = observed.calls if observed.source == "real" else 0
                    stats = report.model_call
                    if stats is not None:
                        row.model_elapsed_ms = stats.elapsed_ms
                        row.input_tokens = stats.input_tokens
                        row.output_tokens = stats.output_tokens
                        row.total_tokens = stats.total_tokens
                    if observed.calls:
                        row.usage_status = ("OBSERVED" if row.total_tokens is not None
                                            else "MODEL_USAGE_UNAVAILABLE")
                        if row.total_tokens is None:
                            ledger.usage_complete, ledger.stop = False, "MODEL_USAGE_UNAVAILABLE"
                        else:
                            ledger.observed_tokens += row.total_tokens
                    ledger.calls += row.model_calls
                    ledger.real_calls += row.real_api_calls
                    if stats and stats.reason != "MODEL_OUTPUT_RECEIVED":
                        ledger.stop = ledger.stop or stats.reason or "MODEL_FAILURE"
                if not row.end_to_end_success:
                    ledger.stop = ledger.stop or "DEVELOPMENT_CASE_FAILED"
                planner.output, planner.plan = None, None
            rows.append(row)
            save_evaluation(row.model_dump(mode="json"),
                            output / variant / (case.case_id + ".json"))
        freeze_valid = True
        try:
            guard()
        except (ValueError, OSError):
            freeze_valid, ledger.stop = False, "EVALUATION_FREEZE_CHANGED"
        result = {"schema_version": "agenttest.development.ab.v1", "source": config.source,
            "variant": variant, "dataset_version": suite.dataset_version,
            "formal_independent_evaluation": False, "review_status": "DEVELOPMENT_ONLY",
            "freeze_valid": freeze_valid, "stop_reason": ledger.stop,
            "summary": summarize(suite, rows), "results": [r.model_dump(mode="json") for r in rows],
            "fault_pairing": "DISABLED", "batch_ledger": ledger.public()}
        save_evaluation(result, output / variant / "results.json")
        (output / variant / "summary.md").write_text(
            render_markdown(result).replace("# Agent Eval v2", "# DEVELOPMENT_ONLY A/B diagnostic"),
            encoding="utf-8")
        seal(output / variant)
        groups[variant] = result
    for variant in groups:
        verify_archive(output / variant)
    comparison = (compare(output / "A", output / "B") if not ledger.stop else {
        "formal_independent_evaluation": False, "comparison_status": "STOPPED_NOT_COMPARABLE",
        "paired_results": [{"case_id": c.case_id, **{v: next(r for r in groups[v]["results"]
            if r["case_id"] == c.case_id) for v in groups}} for c in suite.cases]})
    comparison.update(classification="DEVELOPMENT_ONLY", batch_ledger=ledger.public(),
                      execution_order="A cases, then B cases; no replay or retry",
                      conclusion_limit="Two reused AI development cases; no generalization claim")
    save_evaluation(comparison, output / "comparison.json")
    return comparison
