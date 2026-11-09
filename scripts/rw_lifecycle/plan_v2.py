"""Next-round read-write acceptance plan: development samples and the frozen final set.

Two separate sets live here on purpose:

* ``DEV_CASES`` are development samples. They are used by deterministic preflight and
  tests to prove that the approved declaration can express each rubric item. They are
  never counted as acceptance positions and their model output is never scored.
* ``FINAL_CASES`` are the frozen final requirements for the next first-time acceptance.
  They were written against the rubric below, not selected by running the real model, so
  a passing development sample cannot promote itself into the final set.

No code here sends a request, starts a model or grants write authority.
"""

RUBRIC = {
    "T1": dict(
        title="完整读写生命周期",
        items=[
            ("create", "POST /items 返回 201，动态生成资源 ID"),
            ("read", "GET /items/{itemId} 返回 200 且名称与创建一致"),
            ("patch", "PATCH /items/{itemId} 返回 200 且名称变为 renamed"),
            ("put", "PUT /items/{itemId} 返回 200 且名称变为 replaced"),
            ("delete", "DELETE /items/{itemId} 返回 204"),
            ("readback", "删除后回读返回 404"),
            ("cleanup", "终态 items=0 且 state_sha256 回到初始值"),
        ],
        audit=["POST 201", "GET 200", "PATCH 200", "PUT 200", "DELETE 204", "GET 404"],
    ),
    "T2": dict(
        title="幂等键与冲突",
        items=[
            ("create", "带必填 Idempotency-Key 的创建返回 201"),
            ("replay", "同键同体重放返回 200 且复用同一 ID，不二次创建"),
            ("conflict", "同键异体返回 409，且不改变已有资源"),
            ("delete", "删除返回 204"),
            ("readback", "回读返回 404"),
            ("cleanup", "终态 items=0"),
        ],
        audit=["POST 201", "POST 200", "POST 409", "DELETE 204", "GET 404"],
    ),
    "T3": dict(
        title="身份隔离与受控写拒绝",
        items=[
            ("create", "author 创建返回 201"),
            ("read", "author 读取返回 200"),
            ("readonly_denial", "auditor 读取返回 403"),
            ("anonymous_denial", "匿名读取返回 401"),
            ("write_denial", "auditor 写入返回 403，且资源状态不变"),
            ("anonymous_write", "匿名写入返回 401，且资源状态不变"),
            ("delete", "author 删除返回 204"),
            ("readback", "回读返回 404"),
            ("cleanup", "终态 items=0"),
        ],
        audit=["POST 201", "GET 200", "GET 403", "GET 401", "PATCH 403", "PATCH 401",
               "DELETE 204", "GET 404"],
    ),
}

DEV_CASES = [
    dict(
        key="DEV1",
        title="开发样本：创建、改名、删除后回读",
        text="创建名为 first 的条目并读取；把名称改为 renamed；删除该条目；"
             "再读取该条目确认返回 404。",
    ),
    dict(
        key="DEV2",
        title="开发样本：同键异体冲突",
        text="创建名为 first 的条目；再提交同一幂等键、不同名称 second 的创建，确认返回 409；"
             "最后删除该条目并回读确认 404。",
    ),
]

FINAL_CASES = [
    dict(
        key="T1",
        title="完整读写生命周期",
        # One request per approved policy, with the approved absent-resource read-back.
        text="先用 author 身份创建名为 first 的条目；读取该条目确认返回 200；"
             "把名称改为 renamed；再用 replaced 整体替换名称；删除该条目；"
             "最后读取该条目，确认该批准策略返回 404。",
        expect=dict(methods=["POST", "GET", "PATCH", "PUT", "DELETE", "GET"],
                    statuses=[201, 200, 200, 200, 204, 404],
                    denied=0, conflict=None, cleanup=True),
    ),
    dict(
        key="T2",
        title="幂等键与冲突",
        text="用已批准的幂等键创建名为 first 的条目；重复提交完全相同的创建请求；"
             "然后用同一幂等键、批准名称 second 再次创建，确认该批准策略返回 409；"
             "最后删除该条目并回读确认 404。",
        expect=dict(methods=["POST", "POST", "POST", "DELETE", "GET"],
                    statuses=[201, 200, 409, 204, 404],
                    denied=0, conflict=1, cleanup=True),
    ),
    dict(
        key="T3",
        title="身份隔离与受控写拒绝",
        text="用 author 身份创建名为 first 的条目并读取；再用只读身份 auditor 读取该条目，"
             "确认返回 403；然后用匿名身份读取该条目，确认返回 401；"
             "再用 auditor 身份写入该条目，确认返回 403；用匿名身份写入该条目，确认返回 401；"
             "最后由 author 删除该条目并回读确认 404。",
        expect=dict(methods=["POST", "GET", "GET", "GET", "PATCH", "PATCH", "DELETE", "GET"],
                    statuses=[201, 200, 403, 401, 403, 401, 204, 404],
                    denied=4, conflict=None, cleanup=True,
                    approved_denials=[("getItem", "auditor", 403),
                                      ("getItem", None, 401),
                                      ("updateItem", "auditor", 403),
                                      ("updateItem", None, 401)]),
    ),
]

# Declaration facts the next acceptance cannot start without.
REQUIRED_POLICIES = [
    ("createItem", ("author",), (201,)),
    ("createItem", ("author",), (409,)),
    ("getItem", ("author",), (200,)),
    ("getItem", ("auditor",), (403,)),
    ("getItem", (None,), (401,)),
    ("getItem", ("author",), (404,)),
    ("updateItem", ("author",), (200,)),
    ("updateItem", ("auditor",), (403,)),
    ("updateItem", (None,), (401,)),
    ("replaceItem", ("author",), (200,)),
    ("deleteItem", ("author",), (204,)),
]


def readiness(rows):
    """Compare the built policies with the frozen declaration the plan needs."""
    present = {(row["operation"], tuple(row["roles"]), tuple(row["statuses"]))
               for row in rows}
    missing = [dict(operation=operation, roles=list(roles), statuses=list(statuses))
               for operation, roles, statuses in REQUIRED_POLICIES if
               (operation, roles, statuses) not in present]
    return dict(ready=not missing, missing=missing,
                required=len(REQUIRED_POLICIES), present=len(present))


def case_by_key(keys, cases=FINAL_CASES):
    wanted = {key.upper() for key in keys}
    return [case for case in cases if case["key"].upper() in wanted]


VERBS = {
    "创建": "POST", "读取": "GET", "回读": "GET", "改为": "PATCH", "替换": "PUT",
    "删除": "DELETE", "写入": "PATCH",
}
FORBIDDEN = ("搜索", "列出全部", "上传", "导入", "支付", "下单", "复制", "发送邮件")


def consistency(cases=FINAL_CASES):
    """Prove the frozen requirement text asks for exactly the rubric's requests.

    The wording must not smuggle in actions the rubric does not approve, and each
    expected request method must be visible in the text the model reads.
    """
    problems = []
    for case in cases:
        text, expect = case["text"], case["expect"]
        counts = {}
        for verb, method in VERBS.items():
            counts[method] = counts.get(method, 0) + text.count(verb)
        wanted = {method: expect["methods"].count(method) for method in set(expect["methods"])}
        observed = {method: count for method, count in counts.items() if count}
        if observed != wanted:
            problems.append(dict(case=case["key"], reason="verb_method_mismatch",
                                 detail=dict(observed=observed, expected=wanted)))
        if len(expect["methods"]) != len(expect["statuses"]):
            problems.append(dict(case=case["key"], reason="status_count_mismatch"))
        banned = [word for word in FORBIDDEN if word in text]
        if banned:
            problems.append(dict(case=case["key"], reason="unapproved_action", detail=banned))
        unapproved_status = [status for status in expect["statuses"]
                             if status not in {200, 201, 204, 401, 403, 404, 409}]
        if unapproved_status:
            problems.append(dict(case=case["key"], reason="unapproved_status",
                                 detail=unapproved_status))
    return dict(ok=not problems, problems=problems,
                cases={case["key"]: dict(methods=case["expect"]["methods"],
                                         statuses=case["expect"]["statuses"])
                       for case in cases})
