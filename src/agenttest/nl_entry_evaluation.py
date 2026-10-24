"""Frozen new-request evaluation; runtime receives project configuration and task text only."""

import asyncio
import json
import time
from pathlib import Path

from agenttest.contracts import load_openapi
from agenttest.credentials import CredentialVault
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.scoring import capture_plan
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.goal_suite import run_suite
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import EXAMPLES, project_inputs
from agenttest.project_goals import load_goal_project
from agenttest.regression import RegressionCapture
from agenttest.report import ModelCallStats, ReportStore, fingerprint


def prepare_profiles(root):
    """Two reusable configurations, never a per-request TaskContract or assertion edit."""
    purposes = read_json(PROJECT_ROOT / "examples/nl-reliability/policy-purposes.json")
    for name in ("courses", "tickets"):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=False)
        original = EXAMPLES / name
        for filename in ("openapi.json", "target.json", "goals.json"):
            (directory / filename).write_bytes((original / filename).read_bytes())
        policies = PolicyRegistry.from_json((original / "policies.json").read_bytes()).definitions()
        for policy in policies:
            policy.planning_purpose = purposes[policy.id]
        save_evaluation([p.model_dump(mode="json") for p in policies], directory / "policies.json")
        caps = []
        operations = {
            o.operation_id: o
            for o in load_openapi((directory / "openapi.json").read_bytes()).operations
            if o.operation_id
        }
        for policy in policies:
            if operations[policy.operation_id].method != "GET" or len(policy.binding_rules) != 1:
                continue
            rule = policy.binding_rules[0]
            if rule.source_policy_id is None or rule.location != "path_parameters":
                continue
            caps.append(
                dict(
                    id="registered_" + policy.id,
                    operation_id=policy.operation_id,
                    roles=policy.roles,
                    policy_id=policy.id,
                    business_scenario=policy.business_scenario,
                    source_policy_id=rule.source_policy_id,
                    source_selector=rule.source_selector,
                    path_parameter=rule.name,
                    effect="read_only",
                    no_side_effect_basis="Reviewed disposable GET: lookup and ownership check; "
                    "no mutation, asynchronous advancement, repair or external service access.",
                )
            )
        save_evaluation(caps, directory / "capabilities.json")
        registered = json.loads((original / "registered.project.json").read_bytes())
        registered.update(
            capabilities="capabilities.json",
            budget=dict(
                max_model_calls=4,
                max_readonly_investigations=2,
                max_input_tokens=25_000,
                max_output_tokens=4096,
                max_total_tokens=100_000,
                suite_max_model_calls=8,
                suite_max_total_tokens=100_000,
            ),
        )
        save_evaluation(registered, directory / "registered.project.json")
        load_goal_project(directory / "registered.project.json")


class TraceModel:
    def __init__(self, delegate, output, verify):
        self.delegate, self.output, self.verify = delegate, output, verify
        self.index = 0
        self._stats = ModelCallStats()

    @property
    def stats(self):
        return self._stats

    @property
    def token_reservation(self):
        return getattr(self.delegate, "token_reservation", 0)

    @property
    def evidence_relations(self):
        return getattr(self.delegate, "evidence_relations", False)

    @property
    def last_proposal(self):
        return getattr(self.delegate, "last_proposal", None)

    async def generate(self, context):
        self._stats = ModelCallStats()
        self.verify()
        forbidden = {
            "labels",
            "reference_plan",
            "rubric",
            "grading",
            "expected_normal_plans",
            "case_id",
            "fault",
            "sequence",
            "requirements",
        }
        pending = [context]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if forbidden & value.keys():
                    raise ValueError("Evaluation answers entered model input")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        self.index += 1
        self.output.mkdir(parents=True, exist_ok=True)
        save_evaluation(context, self.output / f"input-{self.index:02}.json")
        try:
            return await self.delegate.generate(context)
        finally:
            stats = getattr(self.delegate, "stats", None)
            if stats is not None:
                self._stats = stats.model_copy(deep=True)


def _settings(path):
    settings = ModelSettings.from_env_file(path)
    return settings.model_copy(
        update=dict(
            timeout=60.0, max_input_tokens=25_000, max_output_tokens=4096, max_total_tokens=100_000
        )
    )


def _seal_result(output, frozen, rows, source, elapsed):
    labels = {r["id"]: r for r in frozen["labels"]["cases"]}
    scores = [
        dict(
            position=row["position"],
            round=row["round"],
            case_id=row["case_id"],
            **grade_position(output, row, labels[row["case_id"]]),
        )
        for row in rows
        if row["attempted"]
    ]
    save_evaluation(
        dict(grading_version=frozen["grading_version"], scores=scores), output / "scores.json"
    )
    result = summarize(rows, source)
    result["quality"] = quality_summary(scores, frozen)
    result.update(results=rows, elapsed_ms=elapsed)
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(
        json.dumps(
            {k: v for k, v in result.items() if k != "results"}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
        newline="\n",
    )
    seal(output)
    return {k: v for k, v in result.items() if k != "results"}


def run_requests(
    output: Path, requests_path: Path, labels_path: Path, *, model_env=None, mock_archive=None
):
    """Exactly 36 fixed positions. Model/transport unknown usage stops all later paid work."""
    from agenttest.nl_entry_scoring import GRADING_VERSION

    identity = checkout_identity(PROJECT_ROOT)
    if identity["worktree_status"]:
        raise ValueError("Committed clean checkout required")
    requests = read_json(requests_path)
    labels = read_json(labels_path)
    if (
        len(requests) != 12
        or len({r["id"] for r in requests}) != 12
        or {r["id"] for r in requests} != {r["id"] for r in labels["cases"]}
        or labels["version"] != GRADING_VERSION
    ):
        raise ValueError("New request set or labels do not match protocol")
    source = "real" if model_env else "mock"
    if model_env:
        verified = verify_requests(mock_archive)
        offline = read_json(mock_archive / "results.json")
        if (verified["scheduled"] != 36 or verified["attempted"] != 36
                or offline["unknown_usage_calls"]
                or any(r["stop_paid"] for r in offline["results"])):
            raise ValueError("Complete offline acceptance required")
        previous = read_json(mock_archive / "freeze.json")
        if (
            previous["source"] != "mock"
            or previous["code"] != identity
            or previous["requests"] != requests
            or previous["labels"] != labels
        ):
            raise ValueError("Offline acceptance must bind this exact source and request set")
    output = output.absolute()
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    prepare_profiles(output / "projects")
    if model_env and inventory(output / "projects") != previous["profile_inventory"]:
        raise ValueError("Offline and live project configurations differ")
    settings = _settings(model_env) if model_env else None
    schedule = [
        dict(position=index + 1, round=round_number, case_id=request["id"])
        for index, (round_number, request) in enumerate(
            (r, q) for r in range(1, 4) for q in requests
        )
    ]
    frozen = dict(
        schema_version="agenttest.nl-entry-eval.v1",
        grading_version=GRADING_VERSION,
        classification="DEVELOPMENT_ONLY",
        review_status="UNREVIEWED",
        source=source,
        independent_human_reviewed=0,
        code=identity,
        requests=requests,
        labels=labels,
        schedule=schedule,
        model=public_model_config(settings) if settings else "SIMULATED",
        mock_archive_sha256=fingerprint(read_json(mock_archive / "archive.json"))
        if mock_archive
        else None,
        profile_inventory=inventory(output / "projects"),
        budget=dict(
            max_calls=288,
            max_total_tokens=3_600_000,
            per_position_calls=8,
            per_run_calls=4,
            per_run_readonly=2,
            model_timeout_seconds=60,
            feedback_timeout_seconds=90,
            workflow_timeout_seconds=300,
            retries=0,
        ),
    )
    save_evaluation(frozen, output / "freeze.json")

    def verify():
        if (
            checkout_identity(PROJECT_ROOT) != identity
            or inventory(output / "projects") != frozen["profile_inventory"]
            or (settings and public_model_config(_settings(model_env)) != frozen["model"])
        ):
            raise ValueError("Frozen source/configuration changed")

    by_id = {r["id"]: r for r in requests}
    # Scoring labels select only synthetic faults, outside the runtime/model context.
    targets = {r["id"]: r["fault"] for r in labels["cases"]}
    rows, stopped = [], None
    started = time.perf_counter()
    for slot in schedule:
        if stopped:
            row = dict(**slot, attempted=False, reason=stopped)
        else:
            verify()
            case = by_id[slot["case_id"]]
            folder = output / f"position-{slot['position']:03}"
            folder.mkdir()
            row = asyncio.run(_position(folder, case, targets[case["id"]], settings, verify))
            row.update(slot, attempted=True)
            if row["stop_paid"]:
                stopped = "NOT_ATTEMPTED_AFTER_USAGE_OR_INFRA_STOP"
        save_evaluation(row, output / f"row-{slot['position']:03}.json")
        rows.append(row)
    result = _seal_result(output, frozen, rows, source, (time.perf_counter() - started) * 1000)
    verify_requests(output)
    return result


async def _position(folder, request, fault, settings, verify):
    from agenttest.workflow import WorkflowBudget

    root = folder.parent / "projects" / request["project"]
    project = load_goal_project(root / "registered.project.json")
    app, _, _, _, environ = project_inputs(request["project"], fault)
    import httpx

    transport = httpx.ASGITransport(app=app)
    if settings:
        from agenttest.evidence_context import EvidenceFeedbackModel

        raw = OpenAICompatiblePlanner(settings, profile="generic", planning_constraints=True)
        feedback = EvidenceFeedbackModel(settings)
    else:
        from agenttest.nl_entry_fixture import mock_adapters

        raw, feedback = mock_adapters()
    capture = RegressionCapture(TraceModel(raw, folder / "planning-inputs", verify))
    analyzer = TraceModel(feedback, folder / "feedback-inputs", verify)
    store = ReportStore(folder / "reports")
    plans, shapes, validated = {}, {}, {}

    def on_validated(goal, plan):
        validated[fingerprint(goal.task.model_dump())] = plan

    def observed(goal, report):
        plan = validated.get(fingerprint(goal.task.model_dump()), capture.scenario)
        vault = CredentialVault(project.target, environ)
        try:
            if plan and vault.contains_secret(plan.model_dump()):
                plan = None
        finally:
            vault.close()
        plans[report.run_id] = capture_plan(plan).model_dump(mode="json") if plan else None
        shapes[report.run_id] = (
            [
                dict(
                    policy_id=s.policy_id,
                    arguments={
                        f"{loc}.{key}": dict(
                            sha256=fingerprint(b.value),
                            length=len(b.value) if isinstance(b.value, (str, list, dict)) else None,
                        )
                        for loc in ("body", "query", "path_parameters")
                        for key, b in getattr(s, loc).items()
                        if b.from_step is None
                    },
                )
                for s in plan.steps
            ]
            if plan
            else []
        )

    started = time.perf_counter()
    result = await run_suite(
        project,
        request["user_task"],
        capture,
        store,
        environ=environ,
        transport=transport,
        analyzer=analyzer,
        execution_guard=verify,
        on_report=observed, on_validated_plan=on_validated,
        budget=WorkflowBudget(model_timeout=61, workflow_timeout=300),
    )
    save_evaluation(result, folder / "product.json")
    save_evaluation(plans, folder / "plans.json")
    save_evaluation(shapes, folder / "input-shapes.json")
    reports = {
        row["run_id"]: store.read(row["run_id"]) for row in result["cases"] if row.get("run_id")
    }
    calls = [s for r in reports.values() for s in _stats(r) if s.calls]
    stop = not result["usage"]["usage_known"] or any(
        r.outcome.value == "TEST_INFRA_ERROR" for r in reports.values()
    )
    return dict(
        scope=result["scope"],
        elapsed_ms=(time.perf_counter() - started) * 1000,
        usage=result["usage"],
        requests=result["http_requests"],
        source_report_ids=list(reports),
        stop_paid=stop,
        known_input_tokens=sum(s.input_tokens or 0 for s in calls),
        known_output_tokens=sum(s.output_tokens or 0 for s in calls),
        unknown_usage_calls=sum(s.total_tokens is None for s in calls),
    )


def _stats(report):
    return ([report.model_call] if report.model_call else []) + (
        [d.model_call for d in report.feedback.decisions if d.model_call] if report.feedback else []
    )


def grade_position(output, row, label):
    from agenttest.nl_entry_scoring import score_request

    folder = output / f"position-{row['position']:03}"
    product = read_json(folder / "product.json")
    plans = read_json(folder / "plans.json")
    reports = {key: ReportStore(folder / "reports").read(key) for key in row["source_report_ids"]}
    return score_request(label, product, reports, plans, read_json(folder / "input-shapes.json"))


def summarize(rows, source):
    used = [r for r in rows if r.get("attempted")]
    known = all(r["usage"]["usage_known"] for r in used)
    return dict(
        schema_version="agenttest.nl-entry-results.v1",
        source=source,
        classification="DEVELOPMENT_ONLY",
        review_status="UNREVIEWED",
        independent_human_reviewed=0,
        scheduled=len(rows),
        attempted=len(used),
        not_attempted=len(rows) - len(used),
        model_calls=sum(r["usage"]["model_calls"] for r in used),
        real_model_calls=sum(r["usage"]["model_calls"] for r in used) if source == "real" else 0,
        simulated_model_calls=sum(r["usage"]["model_calls"] for r in used)
        if source == "mock"
        else 0,
        known_tokens=sum(r["usage"]["known_tokens"] for r in used),
        total_tokens=sum(r["usage"]["known_tokens"] for r in used) if known else None,
        unknown_usage_calls=sum(r["unknown_usage_calls"] for r in used),
        known_input_tokens=sum(r["known_input_tokens"] for r in used),
        known_output_tokens=sum(r["known_output_tokens"] for r in used),
        business_requests=sum(r["requests"] for r in used),
        real_business_http=0,
        real_database_writes=0,
        human_time_saved=None,
    )


def quality_summary(scores, frozen):
    labels = {r["id"]: r for r in frozen["labels"]["cases"]}
    result = {}
    for field in (
        "goal_scope_correct",
        "goal_identification_correct",
        "plan_complete",
        "actual_execution_complete",
        "supported_scope_outcome_correct",
        "full_task_complete",
        "clarification_correct",
        "designated_fault_detected",
    ):
        result[field] = dict(
            numerator=sum(bool(s[field]) for s in scores),
            denominator=sum(
                (
                    labels[s["case_id"]]["behavior"] == "review"
                    if field == "clarification_correct"
                    else bool(labels[s["case_id"]].get("designated_failure"))
                    if field == "designated_fault_detected"
                    else True
                )
                for s in frozen["schedule"]
            ),
        )
    for field in (
        "generated_plans",
        "rule_supported",
        "model_supported",
        "model_explicit_abstentions",
        "model_inappropriate_claims",
        "model_readonly_selections",
        "investigation_requests",
    ):
        result[field] = sum(s[field] for s in scores)
    result["expected_plans"] = sum(
        labels[s["case_id"]]["expected_normal_plans"] for s in frozen["schedule"]
    )
    result["by_scope"] = {
        scope: dict(
            scheduled=sum(labels[s["case_id"]]["scope"] == scope for s in frozen["schedule"]),
            attempted=sum(labels[s["case_id"]]["scope"] == scope for s in scores),
            supported_outcome_correct=sum(
                labels[s["case_id"]]["scope"] == scope and s["supported_scope_outcome_correct"]
                for s in scores
            ),
            full_task_complete=sum(
                labels[s["case_id"]]["scope"] == scope and s["full_task_complete"] for s in scores
            ),
        )
        for scope in ("single", "flow", "project")
    }
    return result


def verify_requests(output):
    output = Path(output)
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("New-request archive changed")
    frozen, result = [read_json(output / name) for name in ("freeze.json", "results.json")]
    if len(result["results"]) != len(frozen["schedule"]) or len(frozen["schedule"]) != 36:
        raise ValueError("Evaluation denominator changed")
    expected_schedule = [
        dict(position=i + 1, round=r, case_id=q["id"])
        for i, (r, q) in enumerate((r, q) for r in range(1, 4) for q in frozen["requests"])
    ]
    if (frozen["schedule"] != expected_schedule or len(frozen["requests"]) != 12
            or len({r["id"] for r in frozen["requests"]}) != 12
            or inventory(output / "projects") != frozen["profile_inventory"]):
        raise ValueError("Evaluation freeze configuration changed")
    labels = {r["id"]: r for r in frozen["labels"]["cases"]}
    scores = []
    seen = set()
    stopped = False
    for slot, row in zip(frozen["schedule"], result["results"]):
        if any(row[k] != v for k, v in slot.items()) or row != read_json(
            output / f"row-{slot['position']:03}.json"
        ):
            raise ValueError("Evaluation schedule changed")
        if row["attempted"]:
            if stopped:
                raise ValueError("Paid work continued after unknown usage or infrastructure stop")
            if seen & set(row["source_report_ids"]):
                raise ValueError("Cross-position report reused")
            seen.update(row["source_report_ids"])
            verify_position(output, row)
            stopped = row["stop_paid"]
            scores.append(dict(**slot, **grade_position(output, row, labels[row["case_id"]])))
        elif not stopped or row["reason"] != "NOT_ATTEMPTED_AFTER_USAGE_OR_INFRA_STOP":
            raise ValueError("Unexpected missing position")
    expected = summarize(result["results"], result["source"])
    if any(result[k] != value for k, value in expected.items()):
        raise ValueError("Evaluation usage summary changed")
    if read_json(output / "scores.json")["scores"] != scores or result[
        "quality"
    ] != quality_summary(scores, frozen):
        raise ValueError("Isolated quality score changed")
    return dict(
        archive_verified=True, scheduled=36, attempted=len(scores), scores=scores, new_model_calls=0
    )


def verify_position(output, row):
    """Recount actual report evidence independently from scheduler and product totals."""
    folder = output / f"position-{row['position']:03}"
    product = read_json(folder / "product.json")
    references = [c["run_id"] for c in product["cases"] if c.get("run_id")]
    if references != row["source_report_ids"] or len(references) != len(set(references)):
        raise ValueError("Position report identity changed")
    reports = [ReportStore(folder / "reports").read(key) for key in references]
    stats = [s for report in reports for s in _stats(report) if s.calls]
    requests = sum(len(s.attempts) for r in reports for s in [*r.steps, *r.investigations])
    calls = sum(s.calls for s in stats)
    known_tokens = sum(s.total_tokens or 0 for s in stats)
    known = all(s.total_tokens is not None for s in stats)
    expected = dict(requests=requests, model_calls=calls, known_tokens=known_tokens,
                    total_tokens=known_tokens if known else None, usage_known=known)
    stop = not known or any(r.outcome.value == "TEST_INFRA_ERROR" for r in reports)
    if (product["usage"] != expected or row["usage"] != expected
            or row["requests"] != product["http_requests"] or row["requests"] != requests
            or row["stop_paid"] != stop
            or row["known_input_tokens"] != sum(s.input_tokens or 0 for s in stats)
            or row["known_output_tokens"] != sum(s.output_tokens or 0 for s in stats)
            or row["unknown_usage_calls"] != sum(s.total_tokens is None for s in stats)):
        raise ValueError("Position usage differs from actual report evidence")
    if requests > 10 or calls > 8 or known_tokens > 100_000:
        raise ValueError("Position budget exceeded")
    for report in reports:
        if (sum(s.calls for s in _stats(report)) > 4
                or sum(len(i.attempts) for i in report.investigations) > 2):
            raise ValueError("Run model or read-only budget exceeded")
