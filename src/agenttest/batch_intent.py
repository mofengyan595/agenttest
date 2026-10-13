"""Closed, ordered Chinese batch grammar and deterministic complete-goal validation."""

import re
from dataclasses import dataclass

from agenttest.batch_plan import FIXTURE_ID, approved_fixture
from agenttest.dsl import Scenario
from agenttest.policies import NeedsReview

COURSE_NAME_PATTERN = r"[\w\u4e00-\u9fff -]{1,80}"
# Full sentence grammar binds each action to its object and order. No bag-of-words match.
_TASK = re.compile(
    r'(?:请)?alice\s*登录(?:后|并|，|,)\s*(?:创建|新建)(?:一门)?课程'
    r'(?:名为)?[“"](?P<name>' + COURSE_NAME_PATTERN + r')[”"]\s*[，,；;]\s*'
    r'使用(?:已批准的|批准的)两行\s*CSV\s*fixture\s+mini_two_rows_v1\s*'
    r'创建(?:一项)?批处理任务\s*[，,；;]\s*'
    r'(?:使用创建响应中的\s*courseId\s*和\s*batchId\s*[，,；;]\s*)?'
    r'有界轮询(?:直到|等待)\s*SUCCEEDED\s*[，,；;]\s*(?:再)?读取结果'
    r'并(?:验证|检查)(?:返回)?条数为\s*2\s*[。.]?'
)
_SMOKE_TASK = re.compile(
    r'使用 alice 创建课程“(?P<name>' + COURSE_NAME_PATTERN + r')”，'
    r'使用已批准的两行 CSV 测试数据创建批量分析任务，等待任务成功后读取结果，'
    r'并验证返回两条记录。'
)


@dataclass(frozen=True)
class BatchIntent:
    course_name: str
    fixture_id: str = FIXTURE_ID
    result_count: int = 2


def interpret_batch_task(task: str) -> BatchIntent:
    """Unsupported phrasing and additional instructions require review, never inference."""
    match = _TASK.fullmatch(task.strip()) or _SMOKE_TASK.fullmatch(task.strip())
    if match is None:
        raise NeedsReview("INTENT_BATCH_UNSUPPORTED_OBJECTIVE")
    # Paired quotes are required rather than accepting mixed delimiter pairs.
    if ('“' in task) != ('”' in task):
        raise NeedsReview("INTENT_BATCH_UNSUPPORTED_OBJECTIVE")
    return BatchIntent(match.group("name"))


def validate_batch_intent(task: str, scenario: Scenario) -> None:
    intent = interpret_batch_task(task)
    signatures = [
        ("login", "batch_login_v1"), ("createCourse", "batch_course_v1"),
        ("createBatch", "batch_upload_v1"), ("getBatch", "batch_poll_v1"),
        ("getBatchResults", "batch_results_v1"),
    ]
    if ([(s.operation_id, s.policy_id) for s in scenario.steps] != signatures
            or any(s.role != "alice" for s in scenario.steps)):
        raise NeedsReview("INTENT_BATCH_FLOW_MISMATCH")
    login, course, batch, poll, results = scenario.steps
    if (set(course.body) != {"name"} or course.body["name"].from_step is not None
            or course.body["name"].value != intent.course_name):
        raise NeedsReview("INTENT_BATCH_NAME_MISMATCH")
    for step, key, source, selector in (
        (batch, "courseId", course, "$.courseId"),
        (poll, "batchId", batch, "$.batchId"), (results, "batchId", batch, "$.batchId"),
    ):
        binding = step.path_parameters.get(key)
        if (set(step.path_parameters) != {key} or binding is None
                or binding.from_step != source.id or binding.select != selector):
            raise NeedsReview("INTENT_BATCH_RESOURCE_MISMATCH")
    if batch.upload != approved_fixture().upload:
        raise NeedsReview("INTENT_BATCH_FIXTURE_MISMATCH")
    for step in scenario.steps:
        if (step.query or step.read_retries or step.poll != (step is poll)
                or (step is not batch and step.upload is not None)
                or (step is not course and step.body)
                or (step is login or step is course) and step.path_parameters):
            raise NeedsReview("INTENT_BATCH_OPTION_MISMATCH")
