"""Frozen A/B/C feedback comparison and cold scoring, on disposable ASGI targets only."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agenttest.composed_planning import FrozenContextPlanner, report_digest
from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.evidence_context import EVIDENCE_PROMPT, EvidenceFeedbackModel
from agenttest.evidence_control import FixedEvidenceControl, fixed_decision
from agenttest.evidence_tasks import evidence_cases, evidence_inputs
from agenttest.feedback import FEEDBACK_PROMPT, FeedbackAction, FeedbackModel
from agenttest.feedback_check import stats_for
from agenttest.investigation import FeedbackBudget, InvestigationConfig
from agenttest.llm_planner import GENERIC_PROMPT, ModelSettings, OpenAICompatiblePlanner
from agenttest.paired_evaluation import stop_paid_schedule
from agenttest.planner_input import planning_context
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.task_contract import task_validator

GRADING_VERSION = "evidence_strict_v1"


def mock_decision(context):
    """Mechanism fixture reads public observations only, not case labels or fault switches."""
    if "relations" in context:
        return fixed_decision(context)
    failed = context["failure"]
    access = any(c["kind"] == "http_status" and not c["passed"] and c.get("expected")
        and all(x in {403, 404} for x in c["expected"]) and isinstance(c.get("actual"), int)
        and 200 <= c["actual"] < 300 for c in failed["checks"])
    for read in context["evidence"]:
        if read["operation_id"] != failed["operation_id"]:
            continue
        original = {(c["kind"], c["selector"]) for c in failed["checks"]
                    if c["kind"] != "http_status" and not c["passed"]}
        repeated = {(c["kind"], c["selector"]) for c in read["checks"]
                    if c["kind"] != "http_status" and not c["passed"]}
        finding = ("ACCESS_BOUNDARY_VIOLATION" if access and read["outcome"] == "PASS"
            and read["role"] != failed["role"] else "PERSISTENT_READBACK_MISMATCH"
            if original & repeated and read["role"] == failed["role"] else None)
        if finding:
            return dict(action="finish", selection=None, finding=finding,
                        evidence_refs=["original", read["ref"]])
    if not context["evidence"] and not context["finish_required"]:
        for choice in context["choices"]:
            if (choice["role"] != failed["role"] if access else choice["role"] == failed["role"]):
                return dict(action="investigate", selection=choice, finding=None, evidence_refs=[])
    return dict(action="finish", selection=None, finding="EVIDENCE_INSUFFICIENT", evidence_refs=[])


def mock_transport(draft):
    def response(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        content = (draft.model_dump(mode="json") if payload["response_format"]["json_schema"][
            "name"] == "agenttest_plan" else mock_decision(context))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(content)}}], "usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
    return httpx.MockTransport(response)


def score_feedback(case, report, scenario):
    """Score model-authored proposals separately from engine rejection/default abstention."""
    from agenttest.feedback_runtime import supported_finding

    feedback = report.feedback
    expected = case["finding"]
    roles = {s.id: s.role for s in scenario.steps} if scenario else {}
    effective, relevant = [], []
    failed = report.steps[-1] if report.steps else None
    parent = next((s for s in scenario.steps if failed and s.id == failed.step_id), None) \
        if scenario else None
    sources = {b.from_step for b in parent.path_parameters.values()} if parent else set()
    producer = roles[next(iter(sources))] if len(sources) == 1 else None
    for read in report.investigations:
        if (failed and read.operation_id == failed.operation_id and
            read.source_step_id in sources and (read.role == producer if case["category"] ==
            "access" else read.role == parent.role)):
            relevant.append(read.step_id)
        if expected and expected != "EVIDENCE_INSUFFICIENT" and supported_finding(
            FeedbackAction(action="finish", selection=None, finding=expected,
                evidence_refs=["original", read.step_id]), failed, parent.role,
                report.investigations, roles):
            effective.append(read.step_id)
    active_abstention = bool(feedback and feedback.reason == "FEEDBACK_EVIDENCE_INSUFFICIENT"
        and feedback.decisions and feedback.decisions[-1].action == "finish")
    supported = bool(feedback and feedback.state == "SUPPORTED" and
        feedback.finding == expected and supported_finding(FeedbackAction(action="finish",
            selection=None, finding=feedback.finding, evidence_refs=feedback.evidence_refs),
            failed, parent.role, report.investigations, roles))
    proposals = [d.proposal for d in feedback.decisions if d.proposal] if feedback else []
    inappropriate = expected == "EVIDENCE_INSUFFICIENT" and any(p.get("finding") in {
        "ACCESS_BOUNDARY_VIOLATION", "PERSISTENT_READBACK_MISMATCH"} for p in proposals)
    # Baseline A has no retained proposal text, so only accepted assertions are observable.
    inappropriate = inappropriate or bool(expected == "EVIDENCE_INSUFFICIENT" and feedback
        and (feedback.state == "SUPPORTED" or feedback.reason == "FEEDBACK_UNSUPPORTED_FINDING"))
    healthy = case["category"] == "healthy"
    from agenttest.eval_v2.scoring import capture_plan

    expected_check = {
        "ownership": ("getCourse", "http_status", None),
        "owner_leak": ("readTicket", "http_status", None),
        "response_name": ("getCourse", "equals", "$.name"),
        "response_id": ("getCourse", "equals", "$.courseId"),
        "wrong_subject": ("readTicket", "equals", "$.payload.subject"),
        "transient_subject": ("readTicket", "equals", "$.payload.subject"),
        "create_status": ("createCourse", "http_status", None),
        "read_unavailable": ("getCourse", "http_status", None),
        "close_ignored": ("closeTicket", "equals", "$.payload.state")}.get(case["fault"])
    business = bool(scenario and report.dsl_valid and report.policy_authorized
        and report.intent_verified and report.workflow_completed
        and report.scenario_sha256 == capture_plan(scenario).scenario_sha256
        and report.outcome == (Outcome.PASS if healthy else Outcome.ASSERTION_FAILED)
        and (all(s.attempts and s.checks and all(c.passed for c in s.checks)
                 for s in report.steps) and len(report.steps) == len(scenario.steps)
             if healthy else any((s.operation_id, c.kind, c.selector) == expected_check
                 and not c.passed for s in report.steps for c in s.checks)))
    safe = (len(report.investigations) <= 2 and (not feedback or feedback.model_calls_used <= 3)
        and all(r.method == "GET" and r.capability_id and r.source_step_id in sources
                for r in report.investigations))
    return {"business_correct": business, "strict_correct": bool(business and (
                report.task_satisfied if healthy else active_abstention if expected ==
                "EVIDENCE_INSUFFICIENT" else supported)),
        "supported_conclusion": supported, "active_abstention": active_abstention,
        "wrong_abstention_with_evidence": bool(effective and active_abstention),
        "inappropriate_assertion": bool(inappropriate), "effective_evidence": bool(effective),
        "citations_complete": supported,
        "selections": len(report.investigations), "relevant_selections": len(relevant),
        "safety_preserved": bool(safe)}


def evaluation_row(case, arm, round_index, report, scenario, elapsed):
    stats = [s for s in stats_for(report) if s.calls]
    known = all(s.total_tokens is not None for s in stats)
    return dict(case_id=case["id"], arm=arm, round=round_index, category=case["category"],
        run_id=report.run_id, report_sha256=report_digest(report),
        scenario=scenario.model_dump(mode="json") if scenario else None,
        outcome=report.outcome.value,
        grade=score_feedback(case, report, scenario), model_calls=sum(s.calls for s in stats),
        input_tokens=sum(s.input_tokens for s in stats) if known else None,
        output_tokens=sum(s.output_tokens for s in stats) if known else None,
        total_tokens=sum(s.total_tokens for s in stats) if known else None, usage_known=known,
        tool_requests=sum(len(r.attempts) for r in report.investigations),
        business_requests=sum(len(r.attempts) for r in report.steps), elapsed_ms=elapsed)


def summary(rows, schedule, source):
    groups = {}
    for arm in sorted({s["arm"] for s in schedule}):
        for category in ["all", *sorted({s["category"] for s in schedule})]:
            scheduled = [s for s in schedule if s["arm"] == arm and
                         (category == "all" or s["category"] == category)]
            if not scheduled:
                continue
            called = [r for r in rows if r["arm"] == arm and r.get("run_id") and
                      (category == "all" or r["category"] == category)]
            keys = ("strict_correct", "supported_conclusion", "active_abstention",
                "wrong_abstention_with_evidence", "inappropriate_assertion", "effective_evidence",
                "citations_complete", "selections", "relevant_selections", "business_correct",
                "safety_preserved")
            groups[f"{arm}/{category}"] = {"denominator": len(scheduled), "attempted": len(called),
                **{key: sum(r["grade"][key] for r in called) for key in keys},
                "model_calls": sum(r["model_calls"] for r in called),
                "input_tokens": sum(r["input_tokens"] for r in called)
                    if all(r["usage_known"] for r in called) else None,
                "output_tokens": sum(r["output_tokens"] for r in called)
                    if all(r["usage_known"] for r in called) else None,
                "total_tokens": sum(r["total_tokens"] for r in called)
                    if all(r["usage_known"] for r in called) else None,
                "unknown_usage_runs": sum(not r["usage_known"] for r in called),
                "tool_requests": sum(r["tool_requests"] for r in called),
                "business_requests": sum(r["business_requests"] for r in called),
                "elapsed_ms": round(sum(r["elapsed_ms"] for r in called), 3)}
    return dict(classification="DEVELOPMENT_ONLY", review_status="UNREVIEWED", human_reviewed=0,
        formal_eval_v2_score=None, source=source, grading_version=GRADING_VERSION,
        scheduled=len(schedule), attempted=sum(bool(r.get("run_id")) for r in rows),
        groups=groups, credits=None, currency_cost=None, human_time_saved=None,
        real_business_http=0, database_writes=0)


async def run_evidence(output: Path, split: str, settings=None, *, mock_acceptance=None,
                       prior_phase=None):
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    private_output(PROJECT_ROOT, output)
    identity = checkout_identity(PROJECT_ROOT)
    if settings and identity["worktree_status"]:
        raise ValueError("Paid schedule requires clean committed code")
    prior = None
    if settings:
        if mock_acceptance is None:
            raise ValueError("Paid schedule requires matching frozen Mock acceptance")
        verify_evidence(mock_acceptance)
        mock = read_json(mock_acceptance / "results.json")
        mock_freeze = read_json(mock_acceptance / "freeze.json")
        if (mock["source"] != "mock_transport" or mock_freeze["split"] != split or
            mock["attempted"] != mock["scheduled"] or any(
                g["business_correct"] != g["denominator"] or
                g["strict_correct"] != g["denominator"] for g in mock["groups"].values())):
            raise ValueError("Mock acceptance did not verify every frozen position")
        if split != "development":
            if prior_phase is None:
                raise ValueError("Prior real phase must be completed before this schedule")
            verify_evidence(prior_phase)
            prior = read_json(prior_phase / "results.json")
            previous = "development" if split == "validation" else "validation"
            if (read_json(prior_phase / "freeze.json")["split"] != previous or
                prior["source"] != "real" or prior["attempted"] != prior["scheduled"] or any(
                    g["unknown_usage_runs"] for g in prior["groups"].values())):
                raise ValueError("Prior phase incomplete or usage unknown; no more paid calls")
    cases = evidence_cases(split)
    rounds = 3 if split == "validation" else 1
    arms = ["B"] if split == "end_to_end" else ["A", "B", "C"]
    # Rotate A/B/C order by round and case; each arm receives a fresh app/session.
    schedule = [{"case_id": case["id"], "category": case["category"], "round": r, "arm": arm}
        for r in range(1, rounds+1) for i, case in enumerate(cases)
        for arm in (arms[(i+r-1) % len(arms):] + arms[:(i+r-1) % len(arms)])]
    selected = (settings or ModelSettings(model="offline-evidence", base_url="https://offline.invalid",
                api_key="synthetic-evidence-key")).model_copy(update={"timeout": 60.0})
    tasks, contexts, plans, inputs_hashes = {}, {}, {}, {}
    for case in cases:
        _, doc, target, policies, _, task, draft, capabilities = evidence_inputs(case)
        tasks[case["id"]] = task.model_dump(mode="json")
        plans[case["id"]] = draft.scenario().model_dump(mode="json")
        context = planning_context(load_openapi(doc), target, policies, task.user_task,
                                   Scenario.model_json_schema())
        contexts[case["id"]] = context
        inputs_hashes[case["id"]] = fingerprint([load_openapi(doc).model_dump(),
            target.model_dump(), policies.digest(),
            [c.model_dump() for c in capabilities.entries()]])
    max_calls = sum(4 if split == "end_to_end" else 3 for s in schedule if s["arm"] != "C")
    frozen = dict(schema_version="agenttest.evidence.freeze.v1", git=identity, split=split,
        frozen_at=datetime.now(timezone.utc).isoformat(), cases=cases, schedule=schedule,
        tasks=tasks, plans=plans, planning_contexts=contexts, inputs_sha256=inputs_hashes,
        prompts={"A": fingerprint(FEEDBACK_PROMPT), "B": fingerprint(EVIDENCE_PROMPT),
                 "planning": fingerprint(GENERIC_PROMPT)},
        model=public_model_config(selected), max_model_calls=max_calls,
        max_tokens=max_calls * selected.max_total_tokens, max_investigations=2,
        max_run_model_calls=4, grading_version=GRADING_VERSION, retries=0,
        source="real" if settings else "mock_transport")
    frozen["scorer_sha256"] = fingerprint(Path(__file__).read_bytes().hex())
    frozen["feedback_action_schema_sha256"] = fingerprint(FeedbackAction.model_json_schema())
    frozen["candidate_schema_source_sha256"] = fingerprint(
        (PROJECT_ROOT / "src/agenttest/evidence_context.py").read_bytes().hex())
    if settings:
        for key in ("cases", "schedule", "tasks", "plans", "inputs_sha256", "prompts",
                    "scorer_sha256", "candidate_schema_source_sha256"):
            if mock_freeze[key] != frozen[key]:
                raise ValueError("Mock acceptance differs from paid candidate")
        frozen["mock_acceptance_seal_sha256"] = fingerprint(
            read_json(mock_acceptance / "archive.json"))
        if prior_phase:
            previous_freeze = read_json(prior_phase / "freeze.json")
            for key in ("prompts", "scorer_sha256", "candidate_schema_source_sha256", "model"):
                if previous_freeze[key] != frozen[key]:
                    raise ValueError("Candidate/model changed after prior real phase")
            frozen["prior_phase_seal_sha256"] = fingerprint(
                read_json(prior_phase / "archive.json"))
    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(frozen, output / "freeze.json")

    def guard():
        if checkout_identity(PROJECT_ROOT) != identity:
            raise ValueError("Frozen checkout changed")

    by_id = {c["id"]: c for c in cases}
    rows, stop = [], None
    for position, slot in enumerate(schedule, 1):
        guard()
        if stop and slot["arm"] != "C":
            row = dict(slot, run_id=None, not_attempted_reason=stop)
        else:
            case = by_id[slot["case_id"]]
            app, doc, target, policies, env, task, draft, caps = evidence_inputs(case)
            if task.model_dump(mode="json") != tasks[case["id"]]:
                raise ValueError("Task changed")
            transport = None if settings else mock_transport(draft)
            analyzer = (FixedEvidenceControl() if slot["arm"] == "C" else
                FeedbackModel(selected, transport) if slot["arm"] == "A" else
                EvidenceFeedbackModel(selected, transport))
            if split == "end_to_end":
                planner = FrozenContextPlanner(OpenAICompatiblePlanner(selected, transport,
                    profile="generic"), contexts[case["id"]], guard)
            else:
                planner = FakeLLM(draft.scenario().model_dump_json())
            started = time.perf_counter()
            report = await Workflow(doc, target, policies, planner,
                ReportStore(output / "reports"), httpx.ASGITransport(app), env,
                WorkflowBudget(model_timeout=65, workflow_timeout=300), user_task=task.user_task,
                intent_validator=task_validator(task, load_openapi(doc), target, policies),
                investigation=InvestigationConfig(enabled=True,
                    feedback=FeedbackBudget(timeout=180)),
                capabilities=caps, analyzer=analyzer, execution_guard=guard).run()
            scenario = (planner.plan if split == "end_to_end" else draft.scenario())
            # No accepted NL plan is not a fixed-plan success.
            row = evaluation_row(case, slot["arm"], slot["round"], report, scenario,
                                 round((time.perf_counter()-started)*1000, 3))
            if slot["arm"] != "C" and stop_paid_schedule(report):
                stop = "UNKNOWN_USAGE_OR_EXTERNAL_FAILURE_STOP"
        rows.append(row)
        save_evaluation(row, output / f"position-{position:03}.json")
        print(json.dumps({"position": position, **slot, "run_id": row.get("run_id"),
                          "tokens": row.get("total_tokens")}), flush=True)
    result = {**summary(rows, schedule, frozen["source"]), "results": rows}
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(json.dumps({k: v for k, v in result.items()
        if k != "results"}, ensure_ascii=False, indent=2), encoding="utf-8")
    seal(output)
    verify_evidence(output)
    return {k: v for k, v in result.items() if k != "results"}


def verify_evidence(output):
    """Cold verification launches neither target nor model; scoring uses frozen expected labels."""
    from agenttest.dsl import Scenario

    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("Evidence archive changed")
    frozen, result = (read_json(output / name) for name in ("freeze.json", "results.json"))
    if frozen["grading_version"] != GRADING_VERSION:
        raise ValueError("Unknown grading version")
    cases = {case["id"]: case for case in frozen["cases"]}
    schedule = frozen["schedule"]
    if len(result["results"]) != len(schedule):
        raise ValueError("Denominator changed")
    store, seen = ReportStore(output / "reports"), set()
    for index, (slot, row) in enumerate(zip(schedule, result["results"]), 1):
        if any(row[k] != slot[k] for k in slot):
            raise ValueError("Schedule changed")
        if row != read_json(output / f"position-{index:03}.json"):
            raise ValueError("Row changed")
        if not row.get("run_id"):
            continue
        if row["run_id"] in seen:
            raise ValueError("Reused target report")
        seen.add(row["run_id"])
        report = store.read(row["run_id"])
        scenario = Scenario.model_validate_json(json.dumps(row["scenario"])) \
            if row["scenario"] else None
        expected = evaluation_row(cases[row["case_id"]], row["arm"], row["round"], report,
                                  scenario, row["elapsed_ms"])
        if expected != row or row["model_calls"] > 4 or row["tool_requests"] > 2:
            raise ValueError("Cold score or safety budget mismatch")
        if row["arm"] == "C" and row["model_calls"]:
            raise ValueError("Control called a model")
    computed = summary(result["results"], schedule, frozen["source"])
    # Preserve the first offline preflight, before token-direction counters were added.
    if "scorer_sha256" not in frozen:
        for group in computed["groups"].values():
            group.pop("input_tokens")
            group.pop("output_tokens")
    if computed != {k: v for k, v in result.items() if k != "results"}:
        raise ValueError("Summary changed")
    return dict(archive_verified=True, scheduled=len(schedule), reports=len(seen),
                new_model_calls=0)
