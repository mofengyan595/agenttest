"""Third-round positions: two fresh natural-language requirements plus the unconsumed T3.

T1/T2 were executed once each in the second acceptance and are never replayed; the new
positions carry new wording, new keys and the same approved scope (six operations, approved
roles and documented 401/403/404/409 negatives). T3 keeps its original position text because
it was never executed.
"""

import plan_v2

RUBRIC = {
    "T1V3": plan_v2.RUBRIC["T1"],
    "T2V3": plan_v2.RUBRIC["T2"],
    "T3": plan_v2.RUBRIC["T3"],
}

FINAL_CASES = [
    dict(
        key="T1V3",
        title="第三轮完整读写生命周期",
        text="先用 author 身份创建名称是 first 的条目，并从响应里取得本次生成的 id；"
             "再按这个 id 读取该条目，确认名称仍是 first；"
             "然后把名称改为 renamed；"
             "接着用 replaced 这个名称整体替换该条目；"
             "之后删除该条目；"
             "最后按同一个 id 回读，确认该批准策略返回 404。",
        expect=dict(methods=["POST", "GET", "PATCH", "PUT", "DELETE", "GET"],
                    statuses=[201, 200, 200, 200, 204, 404], denied=0, conflict=None,
                    cleanup=True),
    ),
    dict(
        key="T2V3",
        title="第三轮幂等重放与冲突",
        text="用已批准的幂等键创建名称为 first 的条目，确认成功并记下返回的 id；"
             "在同一请求成功之后，用相同的幂等键与完全相同的报文体再次创建，"
             "确认返回 200 且 id 不变；"
             "保持同一幂等键，仅把名称改成批准值 second 再次创建，"
             "确认该批准策略返回 409；"
             "最后删除该条目，并按同一 id 回读确认 404。",
        expect=dict(methods=["POST", "POST", "POST", "DELETE", "GET"],
                    statuses=[201, 200, 409, 204, 404], denied=0, conflict=1, cleanup=True),
    ),
    dict(key="T3", title=plan_v2.FINAL_CASES[2]["title"],
         text=plan_v2.FINAL_CASES[2]["text"], expect=plan_v2.FINAL_CASES[2]["expect"]),
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
    """Same contract as plan_v2: wording must ask for exactly the rubric's requests."""
    result = plan_v2.consistency(cases)
    keys = [case["key"] for case in cases]
    if set(keys) != {"T1V3", "T2V3", "T3"}:
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
