"""Separate UNREVIEWED development protocol; never upgrades formal Eval v2 scores."""

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from agenttest.composed_plan import POLICIES, ComposedPlannerDraft
from agenttest.dsl import Scenario
from agenttest.eval_v2.models import IntentScore, PlanEvidence, Rubric, StepEvidence
from agenttest.eval_v2.scoring import capture_plan
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, RunReport, fingerprint
from agenttest.safety import StrictModel


class CompositionGoals(StrictModel):
    creator: Literal["alice", "bob"]
    course_name: str = Field(min_length=1, max_length=80)
    goals: list[Literal["consistency", "ownership", "batch"]] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def coherent(self):
        if (len(set(self.goals)) != len(self.goals)
                or "ownership" in self.goals and "consistency" not in self.goals
                or "batch" in self.goals and self.creator != "alice"):
            raise ValueError("Unsupported development labels")
        return self


class CompositionCase(StrictModel):
    case_id: str = Field(pattern=r"^P[0-9]{3}$")
    user_task: str
    review_status: Literal["UNREVIEWED"] = "UNREVIEWED"
    expected: CompositionGoals | None
    rejection_code: Literal["INTENT_COMPOSED_UNSUPPORTED_OBJECTIVE",
                            "INTENT_COMPOSED_INCOMPLETE_OBJECTIVE",
                            "INTENT_COMPOSED_BATCH_ROLE_UNAPPROVED"] | None

    @model_validator(mode="after")
    def one_expectation(self):
        if (self.expected is None) == (self.rejection_code is None):
            raise ValueError("Label either execution goals or a rejection basis")
        return self


class CompositionSuite(StrictModel):
    schema_version: Literal["agenttest.composition.development.v1"]
    label_provenance: Literal["ai_generated_draft"]
    cases: list[CompositionCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case identity")
        return self


def goal_rubric(labels: CompositionGoals) -> Rubric:
    """Expand explicit offline labels, never parse user_task or call the runtime intent gate."""
    owner = labels.creator
    steps, before = [], []

    def add(key, operation, role, goal, arguments=()):
        steps.append(dict(key=key, operation_id=operation, role=role, policy_id=POLICIES[key],
                          arguments=list(arguments), goals=[goal]))

    root_goal = "consistency" if "consistency" in labels.goals else "batch"
    add("login", "login", owner, root_goal)
    add("create", "createCourse", owner, root_goal, [
        dict(location="body", name="name", value=labels.course_name)])
    before.append(("login", "create"))
    course = [dict(location="path_parameters", name="courseId", source="create",
                   selector="$.courseId")]
    if "consistency" in labels.goals:
        add("read", "getCourse", owner, "consistency", course)
    if "ownership" in labels.goals:
        other = "bob" if owner == "alice" else "alice"
        steps.append(dict(key="other_login", operation_id="login", role=other,
                          policy_id=POLICIES["login"], arguments=[], goals=["ownership"]))
        add("denied", "getCourse", other, "ownership", course)
        before.extend([("other_login", "denied"), ("read", "denied")])
    if "batch" in labels.goals:
        add("upload", "createBatch", owner, "batch", course)
        batch = [dict(location="path_parameters", name="batchId", source="upload",
                      selector="$.batchId")]
        add("poll", "getBatch", owner, "batch", batch)
        add("results", "getBatchResults", owner, "batch", batch)
        before.append(("poll", "results"))
    return Rubric(steps=steps, before=before, required_goals=labels.goals)


def reference_draft(labels: CompositionGoals) -> ComposedPlannerDraft:
    """Fixed labeled fixture for mechanism checks, not a natural-language planner."""
    rubric = goal_rubric(labels)
    return ComposedPlannerDraft(name="composition fixture", steps=[dict(
        id=s.key, operation_id=s.operation_id, role=s.role, policy_id=s.policy_id,
        arguments=[dict(location=a.location, name=a.name, value=a.value,
                        from_step=a.source, select=a.selector) for a in s.arguments],
        csv_fixture_id="mini_two_rows_v1" if s.key == "upload" else None,
        poll_policy_id=POLICIES["poll"] if s.key == "poll" else None,
    ) for s in rubric.steps])


class ExecutionEvidence(StrictModel):
    upload_sha256: str | None
    poll_policy_sha256: str | None
    assertion_template_sha256: str
    binding_rules_sha256: str


class CompositionStepEvidence(StepEvidence):
    execution: ExecutionEvidence


class CompositionPlanEvidence(PlanEvidence):
    steps: list[CompositionStepEvidence]
    registry_sha256: str


def policy_evidence(policy, *, upload=None, poll=False) -> ExecutionEvidence:
    return ExecutionEvidence(
        upload_sha256=fingerprint(upload.model_dump()) if upload is not None else None,
        poll_policy_sha256=fingerprint(policy.poll.model_dump()) if poll and policy.poll else None,
        assertion_template_sha256=fingerprint(policy.assertions.model_dump()),
        binding_rules_sha256=fingerprint([r.model_dump() for r in policy.binding_rules]),
    )


def capture_composition(plan: Scenario, registry: PolicyRegistry) -> CompositionPlanEvidence:
    """Capture before execution; template hashes do not claim to include runtime IDs."""
    base = capture_plan(plan)
    policies = {p.id: p for p in registry.definitions()}
    return CompositionPlanEvidence(
        scenario_sha256=base.scenario_sha256, registry_sha256=registry.digest(),
        steps=[CompositionStepEvidence(**e.model_dump(), execution=policy_evidence(
            policies[s.policy_id], upload=s.upload, poll=s.poll))
            for s, e in zip(plan.steps, base.steps)],
    )


def score_composition(rubric: Rubric, evidence: CompositionPlanEvidence | None,
                      registry: PolicyRegistry) -> IntentScore:
    """Score arguments, dependencies and explicitly labeled execution options.

    Formal Eval v2 still rejects unlabeled upload/poll options. This separate protocol
    compares actual evidence with the frozen approved rules; it never clears option flags.
    """
    if evidence is None:
        return IntentScore(passed=False, diagnostics=["PLAN_UNAVAILABLE"], covered_goals=[])
    errors, matched, positions, valid_keys = [], {}, {}, set()
    policies = {p.id: p for p in registry.definitions()}
    if evidence.registry_sha256 != registry.digest():
        errors.append("REGISTRY_MISMATCH")
    if len(evidence.steps) != len(rubric.steps):
        errors.append("STEP_COUNT_MISMATCH")
    for label in rubric.steps:
        found = [(i, s) for i, s in enumerate(evidence.steps)
                 if (s.operation_id, s.role, s.policy_id) == (
                     label.operation_id, label.role, label.policy_id)]
        if len(found) != 1:
            errors.append("OPERATION_ROLE_POLICY_MISMATCH:" + label.key)
        else:
            positions[label.key], matched[label.key] = found[0]
    if len(matched) != len(evidence.steps):
        errors.append("EXTRA_OR_MISSING_STEPS")
    for label in rubric.steps:
        if label.key not in matched:
            continue
        step, start = matched[label.key], len(errors)
        expected = {f"{a.location}.{a.name}": a for a in label.arguments}
        if set(step.argument_sha256) | set(step.dependencies) != set(expected):
            errors.append("PARAMETER_SET_MISMATCH:" + label.key)
        for key, arg in expected.items():
            if arg.source is None:
                valid = key not in step.dependencies and (
                    step.argument_sha256.get(key) == fingerprint(arg.value))
            else:
                source = matched.get(arg.source)
                valid = (source is not None and key not in step.argument_sha256
                         and step.dependencies.get(key) == source.step_id
                         and step.selectors.get(key) == arg.selector
                         and positions[arg.source] < positions[label.key])
            if not valid:
                errors.append("VALUE_SOURCE_SELECTOR_MISMATCH:" + label.key + "." + key)
        policy = policies.get(label.policy_id)
        if policy is None:
            errors.append("POLICY_UNAVAILABLE:" + label.key)
        else:
            expected_execution = policy_evidence(
                policy, upload=policy.csv_fixture.upload if policy.csv_fixture else None,
                poll=policy.poll is not None)
            if (step.execution != expected_execution or step.read_retries != 0
                    or step.upload != (policy.csv_fixture is not None)
                    or step.poll != (policy.poll is not None)):
                errors.append("EXECUTION_OPTIONS_MISMATCH:" + label.key)
        if start == len(errors):
            valid_keys.add(label.key)
    for first, second in rubric.before:
        if (first not in positions or second not in positions
                or positions[first] >= positions[second]):
            errors.append("ORDER_MISMATCH:" + first + ":" + second)
            valid_keys.discard(second)
    edges = [*rubric.before, *((a.source, step.key) for step in rubric.steps
                              for a in step.arguments if a.source is not None)]
    # A goal includes all of its prerequisites, even when those steps label another goal.
    while blocked := {second for first, second in edges
                      if first not in valid_keys and second in valid_keys}:
        valid_keys -= blocked
    if evidence.registry_sha256 != registry.digest():
        valid_keys.clear()
    covered = {goal for goal in rubric.required_goals
               if all(s.key in valid_keys for s in rubric.steps if goal in s.goals)}
    if covered != set(rubric.required_goals):
        errors.append("SUBGOAL_MISSING")
    return IntentScore(passed=not errors, diagnostics=errors, covered_goals=sorted(covered))


def score_composed_report(case: CompositionCase, report: RunReport,
                          evidence: CompositionPlanEvidence | None,
                          registry: PolicyRegistry) -> dict:
    """Require both independent plan evidence and a complete, bound Workflow result."""
    requests = sum(len(s.attempts) for s in report.steps)
    intent = (score_composition(goal_rubric(case.expected), evidence, registry)
              if case.expected else None)
    bound = evidence is not None and report.scenario_sha256 == evidence.scenario_sha256 and (
        [s.model_dump() for s in report.plan_structure] == [
            {key: getattr(s, key) for key in (
                "step_id", "operation_id", "role", "policy_id", "argument_sha256", "dependencies")}
            for s in evidence.steps])
    executed = bool(evidence and len(report.steps) == len(evidence.steps) and all(
        (s.step_id, s.operation_id) == (e.step_id, e.operation_id) and s.attempts
        for s, e in zip(report.steps, evidence.steps)))
    task_bound = report.user_task_sha256 == fingerprint(case.user_task)
    success = bool(case.expected and intent.passed and bound and executed and task_bound
                   and report.plan_valid and report.dsl_valid and report.policy_authorized
                   and report.intent_verified and report.task_satisfied is True
                   and report.task_mode == "natural_language" and report.completed
                   and report.workflow_completed is True and not report.investigation_error
                   and report.outcome == Outcome.PASS
                   and all(s.outcome == Outcome.PASS and s.checks
                           and all(c.passed for c in s.checks)
                           for s in report.steps))
    code = report.diagnostic.code if report.diagnostic else report.reason
    safe = bool(case.rejection_code and code == case.rejection_code and task_bound
                and report.outcome == Outcome.NEEDS_REVIEW and report.workflow_completed is True
                and not report.steps and requests == 0 and report.tool_calls == 0
                and report.task_satisfied is False)
    return {"case_id": case.case_id, "run_id": report.run_id, "review_status": "UNREVIEWED",
            "source": "fixed_plan_fixture", "outcome": report.outcome.value, "requests": requests,
            "diagnostic": code, "plan_bound": bool(bound), "execution_complete": executed,
            "intent": intent.model_dump() if intent else None,
            "end_to_end_success": success, "safe_rejection": safe,
            "mechanism_verified": success if case.expected else safe}


def composition_summary(rows: list[dict]) -> dict:
    return {"schema_version": "agenttest.composition.development.v1",
            "source": "fixed_plan_fixture", "classification": "DEVELOPMENT_ONLY",
            "review_status": "UNREVIEWED", "human_reviewed": 0,
            "formal_eval_v2_score": None, "real_model_calls": 0,
            "java_requests": 0, "real_database_writes": 0,
            "cases": len(rows), "mechanism_verified": sum(r["mechanism_verified"] for r in rows),
            "outcomes": dict(Counter(r["outcome"] for r in rows)), "results": rows}
