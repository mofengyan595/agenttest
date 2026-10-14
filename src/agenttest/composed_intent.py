"""Controlled goal clauses, independent of evaluation labels and planner output."""

import re
from collections import Counter
from dataclasses import dataclass

from agenttest.batch_plan import approved_fixture
from agenttest.composed_plan import BUSINESS_ORDER_PAIRS, POLICIES
from agenttest.dsl import Scenario
from agenttest.planner_input import validate_user_task
from agenttest.policies import NeedsReview
from agenttest.report import Outcome, RunReport, fingerprint

CONSISTENCY = "核对创建者读取一致"
BATCH = "验证批准两行 CSV 批处理成功及两条结果"
_PREFIX = re.compile(r'(alice|bob)\s*创建课程(?:“([\w\u4e00-\u9fff -]{1,80})”'
                     r'|"([\w\u4e00-\u9fff -]{1,80})")')
_OWNERSHIP = re.compile(r"验证 (alice|bob) 读取被拒绝")


@dataclass(frozen=True)
class ComposedIntent:
    course_name: str
    creator: str
    consistency: bool
    other: str | None
    batch: bool


def requires_composed_task_preflight(task: str) -> bool:
    """Recognize a task refusal without labels or model output; unsafe input still raises."""
    try:
        interpret_composed_task(task)
    except NeedsReview:
        return True
    return False


def is_composed_task_preflight(report: RunReport) -> bool:
    """Distinguish a complete trusted preflight refusal from planner or execution evidence."""
    return (report.validation_issue == report.reason == "TASK_PREFLIGHT_REJECTED"
            and report.outcome == Outcome.NEEDS_REVIEW and report.completed
            and report.workflow_completed is True and report.task_mode == "natural_language"
            and report.task_satisfied is False and report.user_task_sha256 is not None
            and report.workflow_nodes == ["load_contract", "analyze_or_finish", "report"]
            and report.planner_calls == report.tool_calls == 0 and report.model_call is None
            and not report.steps and not report.plan_valid and not report.dsl_valid
            and not report.policy_authorized and not report.intent_verified
            and report.scenario_sha256 == fingerprint(None)
            and report.diagnostic is not None and report.diagnostic.layer.value == "intent")


def interpret_composed_task(task: str) -> ComposedIntent:
    """Recognize complete clauses; unknown, repeated or contradictory goals fail closed."""
    validate_user_task(task)
    clauses = re.split(r"[；;]", task.strip().removesuffix("。").removesuffix("."))
    clauses = [part.strip() for part in clauses]
    match = _PREFIX.fullmatch(clauses[0])
    if match is None or len(clauses) < 2 or len(set(clauses[1:])) != len(clauses[1:]):
        raise NeedsReview("INTENT_COMPOSED_UNSUPPORTED_OBJECTIVE")
    creator, name = match.group(1), match.group(2) or match.group(3)
    consistency, batch, other = False, False, None
    for clause in clauses[1:]:
        if clause == CONSISTENCY:
            consistency = True
        elif clause == BATCH:
            batch = True
        elif relation := _OWNERSHIP.fullmatch(clause):
            if other is not None or relation.group(1) == creator:
                raise NeedsReview("INTENT_COMPOSED_RELATION_MISMATCH")
            other = relation.group(1)
        else:
            raise NeedsReview("INTENT_COMPOSED_UNSUPPORTED_OBJECTIVE")
    if (not consistency and not batch) or (other and not consistency):
        raise NeedsReview("INTENT_COMPOSED_INCOMPLETE_OBJECTIVE")
    if batch and creator != "alice":
        raise NeedsReview("INTENT_COMPOSED_BATCH_ROLE_UNAPPROVED")
    return ComposedIntent(name, creator, consistency, other, batch)


def validate_composed_intent(task: str, scenario: Scenario) -> None:
    """Validate the goal set and partial order without rewriting the proposed plan."""
    intent = interpret_composed_task(task)
    signatures = [("login", intent.creator, POLICIES["login"]),
                  ("createCourse", intent.creator, POLICIES["create"])]
    if intent.consistency:
        signatures.append(("getCourse", intent.creator, POLICIES["read"]))
    if intent.other:
        signatures.extend([("login", intent.other, POLICIES["login"]),
                           ("getCourse", intent.other, POLICIES["denied"])])
    if intent.batch:
        signatures.extend((operation, intent.creator, POLICIES[key]) for operation, key in (
            ("createBatch", "upload"), ("getBatch", "poll"), ("getBatchResults", "results")))
    actual = Counter((s.operation_id, s.role, s.policy_id) for s in scenario.steps)
    if actual != Counter(signatures):
        raise NeedsReview("INTENT_COMPOSED_PATH_MISMATCH")
    grouped = {s.policy_id: s for s in scenario.steps if s.operation_id != "login"}
    create = grouped[POLICIES["create"]]
    if (set(create.body) != {"name"} or create.body["name"].from_step is not None
            or create.body["name"].value != intent.course_name):
        raise NeedsReview("INTENT_COMPOSED_NAME_MISMATCH")
    positions = {s.id: i for i, s in enumerate(scenario.steps)}
    logins = {s.role: s for s in scenario.steps if s.operation_id == "login"}
    for step in scenario.steps:
        if step.operation_id != "login" and positions[logins[step.role].id] >= positions[step.id]:
            raise NeedsReview("INTENT_COMPOSED_AUTH_ORDER_MISMATCH")
        expected_source = None
        if step.operation_id in {"getCourse", "createBatch"}:
            expected_source = (create, "courseId", "$.courseId")
        elif step.operation_id in {"getBatch", "getBatchResults"}:
            expected_source = (grouped[POLICIES["upload"]], "batchId", "$.batchId")
        if expected_source:
            source, key, selector = expected_source
            binding = step.path_parameters.get(key)
            if (set(step.path_parameters) != {key} or binding is None
                    or binding.from_step != source.id or binding.select != selector
                    or positions[source.id] >= positions[step.id]):
                raise NeedsReview("INTENT_COMPOSED_RESOURCE_MISMATCH")
        elif step.path_parameters:
            raise NeedsReview("INTENT_COMPOSED_OPTION_MISMATCH")
        upload = step.policy_id == POLICIES["upload"]
        if (step.query or step.read_retries or step.poll != (step.policy_id == POLICIES["poll"])
                or (step is not create and step.body)
                or step.upload != (approved_fixture().upload if upload else None)):
            raise NeedsReview("INTENT_COMPOSED_OPTION_MISMATCH")
    for earlier, later in BUSINESS_ORDER_PAIRS:
        if later in grouped and positions[grouped[earlier].id] >= positions[grouped[later].id]:
            raise NeedsReview("INTENT_COMPOSED_ORDER_MISMATCH")
