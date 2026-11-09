"""Fourth-round positions: a minimal and a full CRUD closure, never run against a model.

The two requirements use fresh wording, new position keys and the same approved scope as the
previous rounds. ``readiness`` and ``consistency`` are deterministic: no model call, no
target request and no write authority is involved in freezing them.
"""

import plan_v2

RUBRIC = {
    "M1": {
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
        key="M1",
        title="第四轮最小读写闭环",
        text="先由 author 身份创建名称是 alpha 的条目，并从返回值里拿到 id；"
             "接着按这个 id 读取该条目，确认返回 200；"
             "随后删除该条目，确认返回 204；"
             "最后用同一个 id 回读，确认返回 404。",
        expect=dict(methods=["POST", "GET", "DELETE", "GET"],
                    statuses=[201, 200, 204, 404], denied=0, conflict=None, cleanup=True),
    ),
    dict(
        key="F1",
        title="第四轮完整读写生命周期",
        text="先用 author 身份创建名称是 beta 的条目，并记下返回值里的 id；"
             "然后用这个 id 读取该条目，确认返回 200；"
             "再把名称改为 gamma，确认返回 200；"
             "之后用 delta 整体替换该条目，确认返回 200；"
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
CONSUMED_POSITIONS = ()


def readiness(rows):
    return plan_v2.readiness(rows)


def case_by_key(key):
    return next(case for case in FINAL_CASES if case["key"] == key)


def consistency(cases=FINAL_CASES):
    """Same contract as plan_v2 plus one extra check: the rubric audit is the text's order."""
    result = plan_v2.consistency(cases)
    keys = [case["key"] for case in cases]
    if set(keys) != {"M1", "F1"}:
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
    return result
