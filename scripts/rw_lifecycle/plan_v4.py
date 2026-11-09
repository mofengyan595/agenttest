"""Fourth-round positions: a minimal and a full CRUD closure, never run against a model.

The two requirements use fresh wording, new position keys and the same approved scope as the
previous rounds. ``readiness`` and ``consistency`` are deterministic: no model call, no
target request and no write authority is involved in freezing them.

Round 4a consumed position ``M1`` with values that the fixture never approved (``alpha`` and
friends). That record is kept as evidence of an authoring defect, the position is marked
consumed, and the corrected positions ``M2``/``F1`` only use the four approved literals.
"""

import re

import plan_v2

# Mirrors the frozen isolated fixture: createItem admits first/second, updateItem writes
# renamed and replaceItem writes replaced. A wording change must not invent a fifth value.
APPROVED_VALUES = frozenset({"first", "second", "renamed", "replaced"})
VALUE_PATTERN = re.compile(r"名称(?:是|为|改为|改成)\s*([A-Za-z][A-Za-z0-9_-]*)"
                           r"|用\s*([A-Za-z][A-Za-z0-9_-]*)\s*整体替换"
                           r"|批准值\s*([A-Za-z][A-Za-z0-9_-]*)")

RUBRIC = {
    "M2": {
        "title": "最小读写闭环",
        "items": [
            ["create", "POST /items 返回 201，动态生成资源 ID"],
            ["read", "GET /items/{itemId} 返回 200 且名称与创建一致"],
            ["delete", "DELETE /items/{itemId} 返回 204"],
            ["readback", "删除后回读返回 404"],
            ["cleanup", "终态 items=0 且 state_sha256 回到初始值"],
        ],
        "audit": ["POST 201", "GET 200", "DELETE 204", "GET 404"],
    },
    "F1": plan_v2.RUBRIC["T1"],
}

FINAL_CASES = [
    dict(
        key="M2",
        title="第四轮最小读写闭环",
        text="先由 author 身份创建名称是 first 的条目，并从返回值里拿到 id；"
             "接着按这个 id 读取该条目，确认返回 200；"
             "随后删除该条目，确认返回 204；"
             "最后用同一个 id 回读，确认返回 404。",
        expect=dict(methods=["POST", "GET", "DELETE", "GET"],
                    statuses=[201, 200, 204, 404], denied=0, conflict=None, cleanup=True),
    ),
    dict(
        key="F1",
        title="第四轮完整读写生命周期",
        text="先用 author 身份创建名称是 first 的条目，并记下返回值里的 id；"
             "然后用这个 id 读取该条目，确认返回 200；"
             "再把名称改为 renamed，确认返回 200；"
             "之后用 replaced 整体替换该条目，确认返回 200；"
             "接着删除该条目，确认返回 204；"
             "最后按同一个 id 回读，确认返回 404。",
        expect=dict(methods=["POST", "GET", "PATCH", "PUT", "DELETE", "GET"],
                    statuses=[201, 200, 200, 200, 204, 404], denied=0, conflict=None,
                    cleanup=True),
    ),
]

DEV_CASES = []
REQUIRED_POLICIES = plan_v2.REQUIRED_POLICIES
VERBS, FORBIDDEN = plan_v2.VERBS, plan_v2.FORBIDDEN
# M1 was executed once with unapproved literal values; it is never reused.
CONSUMED_POSITIONS = ("M1",)


def readiness(rows):
    return plan_v2.readiness(rows)


def case_by_key(key):
    return next(case for case in FINAL_CASES if case["key"] == key)


def literals(text):
    return {match for group in VALUE_PATTERN.findall(text) for match in group if match}


def consistency(cases=FINAL_CASES):
    """plan_v2's contract plus the rubric order and the approved-literal check."""
    result = plan_v2.consistency(cases)
    keys = [case["key"] for case in cases]
    if set(keys) != {"M2", "F1"}:
        result["ok"] = False
        result["problems"].append(dict(reason="unexpected_positions", detail=keys))
    if set(RUBRIC) != set(keys):
        result["ok"] = False
        result["problems"].append(dict(reason="rubric_position_mismatch", detail=sorted(RUBRIC)))
    for case in cases:
        audit = RUBRIC[case["key"]]["audit"]
        observed = [" ".join(pair) for pair in zip(case["expect"]["methods"],
                                                   [str(status) for status in
                                                    case["expect"]["statuses"]])]
        if [entry.replace(" ", "") for entry in audit] != [
                entry.replace(" ", "") for entry in observed]:
            result["ok"] = False
            result["problems"].append(dict(case=case["key"], reason="rubric_audit_mismatch"))
        unapproved = sorted(literals(case["text"]) - APPROVED_VALUES)
        if unapproved:
            result["ok"] = False
            result["problems"].append(dict(case=case["key"], reason="unapproved_literal",
                                           detail=unapproved))
    return result
