"""Conservative, deterministic mini-target intent checks; no evaluation labels or LLM."""

import re
from dataclasses import dataclass

from agenttest.dsl import Scenario
from agenttest.policies import NeedsReview

# A controlled Chinese vocabulary, not a general natural-language entailment claim.
# Unknown additional goals fail closed, even when the available API path would pass.
WORDS = frozenset("""
用户 创建 新建 新课程 课程 再 随后 读取 查询 核对 检查 验证 测试 确认
名称 编号 ID 名为 返回 创建者 使用 身份 以 由 用 请 帮 要求
一致 一致性 数据 数据一致性 结果 读取结果 自己 自己的 正常 正确 相同
同一 同一个 相同课程 这个 该 刚 能够 能 可以 准确 读回 没有 读到
其他 错误 后 立即 添加 从 到 并 和 与 及 的 了 它 为
真实 响应 操作 比较 先 规划 一门 进行 完成 测试目标
让 受到 应 受到归属 应该 权限 归属 所有权 跨用户 隔离 被拒绝
被禁止 禁止 不能 即使 已登录 登录 访问 对 检验 检查生效 生效
成功 且 另一 另一个 资源 刚创建 按 读取验证
或 限制 时 也 读 此 尝试
""".split())


@dataclass(frozen=True)
class TaskIntent:
    course_name: str
    creator: str
    other: str | None


def required_policy_roles(intent: TaskIntent) -> dict[str, set[str]]:
    """The same bounded goal scope drives catalog narrowing and post-plan validation."""
    required = {policy: {intent.creator}
                for policy in ("nl_login_v1", "nl_create_v1", "nl_read_v1")}
    if intent.other:
        required["nl_login_v1"].add(intent.other)
        required["nl_forbidden_v1"] = {intent.other}
    return required


def interpret_task(task: str) -> TaskIntent:
    """Recognize explicit quoted-name/role tasks; ambiguous or extra objectives need review."""
    matches = list(re.finditer(r'“([^“”]+)”|"([^"\n]+)"', task))
    if len(matches) != 1:
        raise NeedsReview("INTENT_EXPLICIT_NAME_REQUIRED")
    quoted = matches[0]
    name = quoted.group(1) or quoted.group(2)
    if not 1 <= len(name) <= 80:
        raise NeedsReview("INTENT_NAME_UNSUPPORTED")
    text = task[:quoted.start()] + "课程" + task[quoted.end():]
    for letter, role in (("A", "alice"), ("B", "bob")):
        wrong = "bob" if role == "alice" else "alice"
        if re.search(rf"用户\s*{letter}\s*[（(]\s*{wrong}\s*[）)]", text):
            raise NeedsReview("INTENT_ROLE_AMBIGUOUS")
        text = re.sub(rf"用户\s*{letter}\s*[（(]\s*{role}\s*[）)]", role, text)
        text = re.sub(rf"用户\s*{letter}", role, text)
        text = re.sub(rf"(?<![A-Za-z]){letter}(?![A-Za-z])", role, text)
    if re.search(r"不(?:要|能|得)?(?:创建|新建|查询)|无需|忽略|绕过|降低|修改|删除|更新", text):
        raise NeedsReview("INTENT_UNSUPPORTED_OBJECTIVE")
    roles = set(re.findall(r"alice|bob", text))
    creators = set(re.findall(r"创建者\s*(?:使用|为|是)?\s*(alice|bob)", text))
    creators.update(re.findall(
        r"(alice|bob)(?:(?!alice|bob|[，。；;]).){0,40}?(?:创建|新建|添加)", text))
    if len(creators) != 1 or not re.search(r"创建|新建|添加", text):
        raise NeedsReview("INTENT_CREATOR_AMBIGUOUS")
    creator = creators.pop()
    ownership = bool(re.search(r"归属|所有权|跨用户|隔离|权限|禁止|被拒绝|不能", text))
    if not re.search(r"查询|读取|读回", text):
        raise NeedsReview("INTENT_READ_REQUIRED")
    if (ownership and roles != {"alice", "bob"}) or (not ownership and roles != {creator}):
        raise NeedsReview("INTENT_ROLE_AMBIGUOUS")
    if ownership:
        other = "bob" if creator == "alice" else "alice"
        denials = list(re.finditer(r"被拒绝|被禁止|不能|(?:权限|归属).*?限制|归属检查生效", text))
        if not denials:
            raise NeedsReview("INTENT_RELATION_AMBIGUOUS")
        for denial in denials:
            preceding = re.findall(r"alice|bob", text[:denial.start()])
            if not preceding or preceding[-1] != other:
                raise NeedsReview("INTENT_RELATION_AMBIGUOUS")
        for positive in re.finditer(r"查询成功|读回正确|正常读取", text):
            preceding = re.findall(r"alice|bob", text[:positive.start()])
            if not preceding or preceding[-1] != creator:
                raise NeedsReview("INTENT_RELATION_AMBIGUOUS")
    residue = text
    for word in sorted(WORDS | {"alice", "bob"}, key=lambda w: (-len(w), w)):
        residue = residue.replace(word, "")
    if re.sub(r"[\s，。；、：:;（）(),!?！？]+", "", residue):
        raise NeedsReview("INTENT_UNSUPPORTED_LANGUAGE")
    return TaskIntent(name, creator, ("bob" if creator == "alice" else "alice")
                      if ownership else None)


def validate_intent(task: str, scenario: Scenario) -> None:
    """Called only after DSL/OpenAPI/Registry validation; never changes a plan or assertion."""
    intent = interpret_task(task)
    grouped = {}
    for step in scenario.steps:
        grouped.setdefault(step.policy_id, []).append(step)
    expected = set(required_policy_roles(intent))
    if set(grouped) != expected or any(len(grouped[p]) != 1 for p in expected - {"nl_login_v1"}):
        raise NeedsReview("INTENT_PATH_MISMATCH")
    create, read = grouped["nl_create_v1"][0], grouped["nl_read_v1"][0]
    if create.body["name"].from_step is not None or create.body["name"].value != intent.course_name:
        raise NeedsReview("INTENT_NAME_MISMATCH")
    if create.role != intent.creator or read.role != intent.creator:
        raise NeedsReview("INTENT_CREATOR_MISMATCH")
    roles = {intent.creator} | ({intent.other} if intent.other else set())
    logins = grouped["nl_login_v1"]
    if {s.role for s in logins} != roles or len(logins) != len(roles):
        raise NeedsReview("INTENT_AUTH_PATH_MISMATCH")
    for login in logins:
        if any(s.role == login.role and s.policy_id != "nl_login_v1"
               for s in scenario.steps[:scenario.steps.index(login)]):
            raise NeedsReview("INTENT_AUTH_ORDER_MISMATCH")
    reads = [read]
    if intent.other:
        forbidden = grouped["nl_forbidden_v1"][0]
        if forbidden.role != intent.other:
            raise NeedsReview("INTENT_CROSS_USER_MISMATCH")
        if scenario.steps.index(forbidden) < scenario.steps.index(read):
            raise NeedsReview("INTENT_READ_ORDER_MISMATCH")
        reads.append(forbidden)
    if any(s.path_parameters["courseId"].from_step != create.id
           or s.path_parameters["courseId"].select != "$.courseId" for s in reads):
        raise NeedsReview("INTENT_RESOURCE_MISMATCH")
