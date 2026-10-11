"""Bounded Chinese intent -> pinned Java fixture -> existing trusted workflow."""

import json
import re
import unicodedata
from dataclasses import dataclass
from uuid import uuid4

import httpx

from agenttest.courseinsight import FIXTURES, inputs
from agenttest.dsl import ArgumentBinding, Scenario
from agenttest.planner_input import validate_user_task
from agenttest.policies import NeedsReview, PolicyRegistry, RegisteredPolicy
from agenttest.report import ReportStore, RunReport

DEFAULT_TASK = ('使用 admin 登录，创建名称为“AgentTest AI Course”、教师为“Demo Teacher”的课程，'
                '再查询并核对 ID、code、name、teacherName 与创建输入一致。')
PROFILE = "courseinsight_demo_nl_v1"
WORDS = frozenset("""
使用 admin demo_admin 管理员 登录 创建 新建 名称 课程名 教师 教师名 教师姓名 为 是 的 课程
再 随后 然后 查询 读取 并 核对 检查 验证 测试 确认 ID id code name teacherName
与 和 及 创建输入 输入 数据 一致 一致性 返回 详情 CourseInsight 请 先 后 成功
课程 名称为 教师为 代码 编号 各项 字段 相同
""".split())
POLICIES = ["ci_nl_login_v1", "ci_nl_create_v1", "ci_nl_read_v1"]


@dataclass(frozen=True)
class JavaIntent:
    name: str
    teacher: str


def interpret(task: str) -> JavaIntent:
    task = validate_user_task(task)
    patterns = [r'(?:课程名|名称)(?:为|是)?[：: ]*[“"]([^“”"\n]+)[”"]',
                r'(?:教师姓名|教师名|教师)(?:为|是)?[：: ]*[“"]([^“”"\n]+)[”"]']
    values, residue = [], task
    for pattern, limit in zip(patterns, (100, 50), strict=True):
        matches = list(re.finditer(pattern, residue))
        if len(matches) != 1:
            raise NeedsReview("INTENT_JAVA_EXPLICIT_FIELDS_REQUIRED")
        match = matches[0]
        value = match.group(1)
        if (not value.strip() or any(unicodedata.category(c).startswith("C") for c in value)
                or len(value.encode("utf-16-le")) // 2 > limit):
            raise NeedsReview("INTENT_JAVA_FIELD_UNSUPPORTED")
        values.append(value)
        residue = residue[:match.start()] + residue[match.end():]
    if (not re.search(r"登录", residue) or not re.search(r"创建|新建", residue)
            or not re.search(r"查询|读取", residue)
            or not re.search(r"核对|检查|验证|确认", residue)
            or not re.search(r"一致|相同", residue)
            or not re.search(r"admin|管理员", residue)):
        raise NeedsReview("INTENT_JAVA_CHAIN_REQUIRED")
    for word in sorted(WORDS, key=lambda w: (-len(w), w)):
        residue = residue.replace(word, "")
    if re.sub(r"[\s，。；、：:;（）(),!?！？]+", "", residue):
        raise NeedsReview("INTENT_JAVA_UNSUPPORTED_OBJECTIVE")
    return JavaIntent(*values)


def approved_inputs(task: str, *, course_code: str | None = None):
    """Operator-side data binding; all original approved assertion semantics are preserved."""
    intent = interpret(task)
    code = course_code or "AT4A" + uuid4().hex[:24]
    if not re.fullmatch(r"AT4A[0-9a-f]{24}", code):
        raise ValueError("OPERATOR_COURSE_CODE_INVALID")
    scenario, target, contract, _ = inputs()
    scenario.name = "CourseInsight operator-pinned natural language test"
    body = {"code": code, "name": intent.name, "teacherName": intent.teacher,
            "description": "Isolated local demo acceptance fixture"}
    scenario.steps[1].body = {k: ArgumentBinding(value=v) for k, v in body.items()}
    entries = json.loads((FIXTURES / "policies.json").read_bytes())
    for step, policy, policy_id in zip(scenario.steps, entries, POLICIES, strict=True):
        step.policy_id = policy["id"] = policy_id
        policy["business_scenario"] = PROFILE
    entries[1]["required_bindings"] = {"body": scenario.steps[1].model_dump()["body"]}
    for rule in entries[1]["binding_rules"]:
        rule["literal_schema"] = {"type": "string", "enum": [body[rule["name"]]]}
    entries[2]["binding_rules"][0]["source_policy_id"] = POLICIES[1]
    registry = PolicyRegistry([RegisteredPolicy.model_validate(p) for p in entries])
    target.business_scenario = PROFILE
    return scenario, target, contract, registry


def intent_check(expected: Scenario):
    expected = expected.model_copy(deep=True)

    def validate(task: str, scenario: Scenario) -> None:
        intent = interpret(task)
        if len(scenario.steps) != 3:
            raise NeedsReview("INTENT_JAVA_PATH_MISMATCH")
        for step, approved in zip(scenario.steps, expected.steps, strict=True):
            if (step.operation_id != approved.operation_id or step.role != approved.role
                    or step.policy_id != approved.policy_id or step.poll or step.read_retries):
                raise NeedsReview("INTENT_JAVA_PATH_MISMATCH")
        create, read = scenario.steps[1:]
        if create.body != expected.steps[1].body:
            raise NeedsReview("INTENT_JAVA_INPUT_MISMATCH")
        if (create.body["name"].value != intent.name
                or create.body["teacherName"].value != intent.teacher):
            raise NeedsReview("INTENT_JAVA_INPUT_MISMATCH")
        binding = read.path_parameters.get("id")
        if binding is None or binding.from_step != create.id or binding.select != "$.data":
            raise NeedsReview("INTENT_JAVA_RESOURCE_MISMATCH")

    return validate


async def run(task: str, planner, store: ReportStore, *, bundle=None,
              environ: dict[str, str], transport: httpx.AsyncBaseTransport | None = None,
              authorized: bool = False, execution_guard=None) -> RunReport:
    from agenttest.workflow import Workflow, WorkflowBudget

    expected, target, _, registry = bundle or approved_inputs(task)
    target = target.model_copy(deep=True)
    # Real HTTP must go through the separate isolation/approval harness. Caller owns this flag.
    target.allow_mutations = authorized
    budget = WorkflowBudget(model_timeout=65.0, workflow_timeout=120.0)
    return await Workflow((FIXTURES / "openapi.json").read_bytes(), target, registry,
                          planner, store, transport, environ, budget, user_task=task,
                          intent_validator=intent_check(expected),
                          execution_guard=execution_guard).run()
