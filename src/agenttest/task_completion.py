"""Deterministic completion criteria for a natural-language testing goal.

P1 measured the central quality gap: a harness could produce a fluent final answer, reach a
terminal state and call the task done while the *user's* goal had only been sampled. This
module removes "the model said it finished" from the definition. It classifies the goal from
its own words (never from a model), derives the approved obligations the round was scoped to
exercise, and decides whether the requested testing actually happened.

Six different questions are kept apart, because collapsing them is how a completion rate gets
inflated:

============================  ==========================================================
question                      field
============================  ==========================================================
did the agent stop normally?  ``flags.agent_terminated``
was any evidence produced?    ``flags.evidence_produced``
is the scope measured?        ``flags.obligations_covered``
is the *task* complete?       ``complete`` (every required criterion ``MET``)
was a real anomaly found?     ``flags.anomalies_found``
is anything left pending?     ``flags.pending_items``
============================  ==========================================================

The kind of goal picks the criteria; the operator's own approvals decide what may be tested:

``FUNCTIONAL``   exercise every in-scope approved obligation, including a full write
                 lifecycle when the scope contains one (not "one read = done");
``REGRESSION``   exercise the policies around the changed subject (a deletion and its
                 lifecycle), and nothing about unrelated endpoint counts;
``PRE_RELEASE``  exercise every approved obligation of the project scope, with every group
                 designed, no open questions and no pending resource;
``INVESTIGATION`` reproduce the subject, keep expected-vs-actual evidence and record the
                 investigation boundary; it does **not** require every check to pass.

A measured ``ASSERTION_FAILED`` is complete *testing* and a reported anomaly: the user asked
for a test, not for the service to pass. ``checks_passed`` stays separate and strict.
"""

from __future__ import annotations

import re

from agenttest.product_obligations import (
    approved_scope,
    executed_evidence,
    open_obligations,
)

FUNCTIONAL = "FUNCTIONAL"
REGRESSION = "REGRESSION"
PRE_RELEASE = "PRE_RELEASE"
INVESTIGATION = "INVESTIGATION"
KINDS = (FUNCTIONAL, REGRESSION, PRE_RELEASE, INVESTIGATION)

MET = "MET"
UNMET = "UNMET"
EXEMPT = "EXEMPT"

# Ordered: the most specific intent wins. Every pattern is a plain lexical rule, so the same
# goal always classifies the same way and the matched phrase is reported verbatim.
_INVESTIGATION = re.compile(
    r"偶发|偶尔|时好时坏|报错|出错|异常|排查|调查|定位|看看|看一下|为什么|原因|不稳定|"
    r"intermittent|occasionally|investigat|diagnos|debug|trace|why does", re.IGNORECASE)
_REGRESSION = re.compile(
    r"回归|刚改|改过|改动|修改过|修复后|变更后|重新验证|验证修复|"
    r"regression|just changed|modified|after the fix|verify the fix", re.IGNORECASE)
_PRE_RELEASE = re.compile(
    r"上线|发布|发版|准备上线|投产|验收一轮|测一轮|全量|整套|"
    r"release|pre-?release|before launch|go live|production readiness", re.IGNORECASE)
_FUNCTIONAL = re.compile(
    r"测一下|测试|测测|帮我测|功能|接口正常|能用|冒烟|"
    r"test|smoke|functional|check", re.IGNORECASE)

# Lexical subject cues mapped to HTTP methods. The subject is what the user pointed at; an
# unrelated endpoint is never counted as the subject of a regression goal.
_METHOD_CUES = (
    (re.compile(r"删除|移除|delete|remove|purge", re.IGNORECASE), {"DELETE"}),
    (re.compile(r"创建|新建|新增|添加|create|add", re.IGNORECASE), {"POST"}),
    (re.compile(r"更新|修改|编辑|替换|变更|update|patch|replace|modif", re.IGNORECASE),
     {"PATCH", "PUT"}),
    (re.compile(r"读取|查询|详情|获取|read|get|fetch|lookup", re.IGNORECASE), {"GET", "HEAD"}),
)


def classify_task(text: str) -> dict:
    """Classify the goal from its own words and report the matched cues (no model call)."""
    text = text or ""
    cues = []
    for kind, pattern in ((INVESTIGATION, _INVESTIGATION), (REGRESSION, _REGRESSION),
                          (PRE_RELEASE, _PRE_RELEASE), (FUNCTIONAL, _FUNCTIONAL)):
        matched = sorted({match.group(0) for match in pattern.finditer(text)})
        if matched:
            cues.append(dict(kind=kind, cues=matched))
    kind = cues[0]["kind"] if cues else FUNCTIONAL
    reason = (f"命中{kind}线索：" + "、".join(cues[0]["cues"])) if cues else \
        "未命中特定线索，按功能测试处理。"
    return dict(kind=kind, cues=cues, reason=reason)


def _operations(project):
    return {operation.operation_id: operation for operation in project.contract.operations}


def _policies(project):
    return {policy.id: policy for policy in project.registry.definitions()}


def subject_scope(project, text: str, kind: str) -> dict:
    """The operations/policies the goal is *about*, derived from its words and the contract.

    A regression or investigation goal needs a subject, otherwise "everything else passed"
    would silently stand in for it. The closure follows the approved bindings: a deletion
    drags in the creation that produced its identifier and the absence read that observes it;
    a creation drags in its approved cleanup deletion.
    """
    policies = _policies(project)
    allowed = [item for item in project.contract.operations
               if item.operation_id in project.target.allowed_operations]
    methods = set()
    cues = []
    for pattern, wanted in _METHOD_CUES:
        matched = sorted({match.group(0) for match in pattern.finditer(text or "")})
        if matched:
            methods |= wanted
            cues.extend(matched)
    lexical = {operation.operation_id for operation in allowed
               if any(operation.operation_id.lower() in token.lower()
                      for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text or ""))}
    if lexical:
        subject_operations = lexical
        basis = "operation_id 在原文中出现"
    else:
        # The interface is often named in the operator's own language ("包裹"), not by its
        # operation id. A CJK n-gram that appears in the approved summary/path is the same
        # kind of lexical evidence -- deterministic, quoted back, never model-inferred. The
        # longest matching phrase wins, so "查询包裹超时" selects the read, not the family.
        candidates = {run[index:index + size]
                      for run in re.findall(r"[\u4e00-\u9fff]+", text or "")
                      for size in range(2, min(4, len(run)) + 1)
                      for index in range(len(run) - size + 1)}
        by_summary, matched = set(), []
        for size in (4, 3, 2):
            grams = {gram for gram in candidates if len(gram) == size}
            by_summary = {operation.operation_id for operation in allowed
                          if any(gram in (operation.summary or "") or gram in operation.path
                                 for gram in grams)}
            if by_summary:
                matched = sorted(gram for gram in grams
                                 if any(gram in (operation.summary or "")
                                        or gram in operation.path for operation in allowed))
                break
        if by_summary:
            subject_operations = by_summary
            basis = "原文词元匹配操作摘要：" + "、".join(matched[:6])
        elif methods:
            subject_operations = {operation.operation_id for operation in allowed
                                  if operation.method in methods}
            basis = "原文动作词 → " + "、".join(sorted(methods))
        elif kind in {REGRESSION, INVESTIGATION}:
            subject_operations, basis = set(), "原文没有指出可确定的主体接口"
        else:
            subject_operations = {operation.operation_id for operation in allowed}
            basis = "检查整个已授权范围"
    policy_ids = {policy.id for policy in policies.values()
                  if policy.operation_id in subject_operations}
    # Closure along the approved bindings: a deletion drags in the creation that produced its
    # identifier, a creation drags in its approved cleanup deletion, and any consumer of a
    # selected producer belongs to the same lifecycle.
    changed = True
    while changed:
        changed = False
        for policy in policies.values():
            if policy.id in policy_ids:
                continue
            sources = {rule.source_policy_id for rule in policy.binding_rules
                       if rule.source_policy_id}
            produces_for_selected = any(
                policy.id == rule.source_policy_id
                for identifier in policy_ids
                for rule in policies[identifier].binding_rules)
            if sources & policy_ids or produces_for_selected:
                policy_ids.add(policy.id)
                changed = True
    # An approved absence expectation (404/410) is part of the lifecycle even when the read
    # operation itself was not named: it is the only policy that verifies the deletion.
    for policy in policies.values():
        if policy.id in policy_ids or policy.negative is not None:
            continue
        status = policy.assertions.status
        codes = set(status.codes) if status is not None else set()
        if not codes or not codes <= {404, 410}:
            continue
        if {rule.source_policy_id for rule in policy.binding_rules} & policy_ids:
            policy_ids.add(policy.id)
    subject_operations |= {policies[identifier].operation_id for identifier in policy_ids
                           if identifier in policies}
    resolved = bool(subject_operations and policy_ids)
    return dict(operations=sorted(subject_operations), policy_ids=sorted(policy_ids),
                basis=basis, cues=sorted(set(cues)), resolved=resolved)


def _ready_operations(summary):
    return {item["operation_id"] for item in (summary.get("inventory") or {}).get(
        "operations", []) if item.get("state") == "READY"}


def _scope_rows(project, summary, subject, kind):
    """Approved obligations of the round scope, split into in-scope, measured and blocked."""
    scope = approved_scope(project, summary)
    if kind in {REGRESSION, INVESTIGATION} and subject.get("resolved"):
        scope = [item for item in scope if item["policy_id"] in set(subject["policy_ids"])]
    policies = _policies(project)
    excluded = []
    if kind in {FUNCTIONAL, PRE_RELEASE} and (summary.get("focus") or {}).get(
            "mode") == "selected":
        # A narrowed focus deliberately leaves the approved negative-value checks out of the
        # round. Requiring them here would make ``complete`` unreachable for every narrowed
        # request; ignoring them silently would hide the gap. They are excluded *and listed*.
        excluded = [item for item in scope
                    if (policies.get(item["policy_id"]) is not None
                        and policies[item["policy_id"]].negative is not None)]
        scope = [item for item in scope if item not in excluded]
    evidence = executed_evidence(summary)
    ready = _ready_operations(summary)
    measured, missing, blocked = [], [], []
    for item in scope:
        entry = evidence.get(item["policy_id"])
        row = dict(item, measured=entry is not None,
                   passed=bool(entry and entry["passed"]),
                   observed=(entry or {}).get("observed"),
                   cases=list((entry or {}).get("cases") or ()),
                   checks=len((entry or {}).get("checks") or ()))
        if entry is not None:
            measured.append(row)
        elif item["operation_id"] not in ready:
            blocked.append(dict(row, reason="接口尚未就绪（缺少输入、身份或授权）。"))
        else:
            missing.append(row)
    return dict(scope=scope, measured=measured, missing=missing, blocked=blocked,
                evidence=evidence, open=open_obligations(scope, evidence),
                excluded_negatives=excluded)


def _case_operations(summary):
    return {operation for case in summary.get("cases") or ()
            for operation in case.get("executed_operations") or ()}


def _executed_cases(summary):
    return [case for case in summary.get("cases") or ()
            if case.get("status") == "FINISHED" and (case.get("http_requests") or 0) > 0]


def _created_resources(summary):
    """Counts every resource the run really created, not only the ones with a lifecycle rule.

    A case that sends an approved POST without a lifecycle receipt still created a resource on
    the isolated target; reporting "0 created" hid ten leaked parcels in the pre-release round.
    Identity-level cleanup is only provable through a lifecycle receipt, so an executed POST
    without one is *pending* (never silently clean). The detail names the operations so the
    report can show which creations have no auditable cleanup.
    """
    rows = [item for case in summary.get("cases") or ()
            for item in case.get("resource_lifecycle") or ()]
    pending = [row for row in rows
               if row.get("status") in {"OWNED", "CLEANUP_PENDING", "CLEANUP_OUTCOME_UNKNOWN"}]
    posts = [step for case in summary.get("cases") or ()
             for step in case.get("evidence") or ()
             if (step.get("method") or "").upper() == "POST" and step.get("request")]
    unproven = max(0, len(posts) - len(rows))
    pending.extend(dict(operation_id=step.get("operation_id"), status="CLEANUP_UNPROVEN",
                        basis="EXECUTED_CREATE_WITHOUT_CLEANUP")
                   for step in posts[len(rows):])
    return dict(total=len(rows) + unproven, pending=pending,
                lifecycle=len(rows), posts=len(posts), unproven=unproven)


def _write_lifecycle(summary):
    """A case that both created a resource and ran its approved cleanup successfully."""
    for case in summary.get("cases") or ():
        lifecycle = case.get("resource_lifecycle") or ()
        if not lifecycle:
            continue
        created = [row for row in lifecycle if row.get("status") != "NOT_CREATED"]
        cleaned = bool(created) and all(row.get("cleanup_result") == "PASS"
                                        for row in created)
        if cleaned and case.get("outcome") in {"PASS", "ASSERTION_FAILED"}:
            return case
    return None


def _questions_in_scope(summary, kind, subject):
    questions = []
    for question in summary.get("questions") or ():
        operation = question.get("operation_id")
        if operation is None:
            questions.append(question)
        elif kind in {REGRESSION, INVESTIGATION} and subject.get("resolved"):
            if operation in set(subject["operations"]):
                questions.append(question)
        else:
            questions.append(question)
    return questions


def _anomalies(summary):
    confirmed, unproven = [], []
    for case in summary.get("cases") or ():
        failed = [dict(operation_id=step.get("operation_id"), kind=check.get("kind"),
                       selector=check.get("selector"), expected=check.get("expected"),
                       actual=check.get("actual"))
                  for step in case.get("evidence") or () if step.get("executed")
                  for check in step.get("checks") or () if check.get("passed") is False]
        if case.get("outcome") == "ASSERTION_FAILED" and failed:
            confirmed.append(dict(case_id=case.get("case_id"),
                                  operations=list(case.get("executed_operations") or ()),
                                  failed=failed[:8]))
        elif case.get("outcome") not in {None, "PASS"}:
            unproven.append(dict(case_id=case.get("case_id"), outcome=case.get("outcome"),
                                 reason=(case.get("reason") or "")[:160] or None,
                                 cause_confirmed=False))
    return confirmed, unproven


def _criterion(identifier, title, status, detail, evidence=None):
    return dict(id=identifier, title=title, status=status, detail=detail,
                evidence=evidence or {})


def _common_criteria(summary):
    executed = _executed_cases(summary)
    checks = [check for case in executed for step in case.get("evidence") or ()
              for check in step.get("checks") or ()]
    resources = _created_resources(summary)
    confirmed, _ = _anomalies(summary)
    rows = [
        _criterion("EVIDENCE_PRODUCED", "产生了真实执行证据",
                   MET if executed and checks else UNMET,
                   f"已执行 {len(executed)} 个用例、{len(checks)} 项检查。"
                   if executed else "没有任何完成的执行用例，无法据此判定任务。",
                   dict(executed_cases=len(executed), checks=len(checks))),
        _criterion("RESOURCES_RESOLVED", "已创建资源具备可审计清理结果",
                   MET if not resources["pending"] else UNMET,
                   f"创建 {resources['total']} 个资源，待清理 {len(resources['pending'])} 个。"
                   + (f"其中 {resources['unproven']} 个只有执行记录、没有清理凭据。"
                      if resources.get("unproven") else ""),
                   dict(created=resources["total"], pending=len(resources["pending"]),
                        lifecycle=resources.get("lifecycle"),
                        unproven=resources.get("unproven"))),
        _criterion("ANOMALIES_EVIDENCED", "异常结论都有期望值与实测值",
                   MET if all(item.get("failed") for item in confirmed) else UNMET,
                   f"已确认异常 {len(confirmed)} 条；每条都带有期望/实测检查证据。"
                   if confirmed else "本次没有产生断言级异常。",
                   dict(confirmed=len(confirmed))),
    ]
    return rows


def _negatives_row(scope_rows):
    excluded = scope_rows.get("excluded_negatives") or []
    return _criterion(
        "NEGATIVES_OUT_OF_SCOPE", "本轮未纳入的已批准异常取值检查被明确排除", EXEMPT,
        ("以下已批准异常取值检查不在本轮重点范围内，不计入完成判据："
         + "、".join(item["policy_id"] for item in excluded[:12])) if excluded
        else "本轮没有排除已批准的异常取值检查。",
        dict(excluded=[item["policy_id"] for item in excluded[:12]]))


def _functional_criteria(project, summary, scope_rows, subject, kind):
    rows = []
    missing = scope_rows["missing"]
    rows.append(_criterion(
        "SCOPE_OBLIGATIONS_MEASURED", "范围内的每项已批准义务都有执行证据",
        MET if not missing else UNMET,
        ("范围内 " + str(len(scope_rows["measured"])) + " 项义务全部有执行证据。")
        if not missing else ("仍有 " + str(len(missing)) + " 项义务没有执行证据："
                             + "、".join(item["policy_id"] for item in missing[:8])),
        dict(measured=len(scope_rows["measured"]), missing=len(missing),
             policies=[item["policy_id"] for item in missing])))
    ready_scope = {item["operation_id"] for item in scope_rows["scope"]
                   if item["operation_id"] in _ready_operations(summary)}
    exercised = _case_operations(summary)
    unexercised = sorted(ready_scope - exercised)
    rows.append(_criterion(
        "SCOPE_OPERATIONS_EXERCISED", "范围内每个就绪接口都被真实调用",
        MET if not unexercised else UNMET,
        "范围内所有就绪接口都有真实请求。" if not unexercised else
        "以下接口范围内但从未被请求：" + "、".join(unexercised[:10]),
        dict(exercised=len(exercised & ready_scope), unexercised=unexercised[:10])))
    writable = [policy for policy in project.registry.definitions()
                if policy.negative is None
                and policy.operation_id in ready_scope
                and any(operation.operation_id == policy.operation_id
                        and operation.method == "POST"
                        for operation in project.contract.operations)]
    lifecycle = _write_lifecycle(summary)
    if writable:
        rows.append(_criterion(
            "WRITE_LIFECYCLE_EXERCISED", "写操作按完整生命周期测试并清理",
            MET if lifecycle else UNMET,
            f"用例 {lifecycle.get('case_id')} 完成了创建并在批准范围内清理。"
            if lifecycle else "范围内有写接口，但没有一条执行用例完成创建+批准清理。",
            dict(case_id=(lifecycle or {}).get("case_id"))))
    else:
        rows.append(_criterion("WRITE_LIFECYCLE_EXERCISED", "写操作按完整生命周期测试并清理",
                               EXEMPT, "范围内的已批准操作没有写接口。"))
    rows.append(_negatives_row(scope_rows))
    return rows


def _regression_criteria(project, summary, scope_rows, subject, kind):
    rows = []
    if not subject.get("resolved"):
        rows.append(_criterion(
            "SUBJECT_RESOLVED", "回归主体可以从原文确定",
            UNMET, "原文没有指出可确定的接口或动作；不能把无关接口数量当作回归完成。",
            dict(basis=subject.get("basis"))))
        return rows
    rows.append(_criterion(
        "SUBJECT_RESOLVED", "回归主体可以从原文确定", MET,
        f"主体接口：{'、'.join(subject['operations'])}（{subject['basis']}）。",
        dict(operations=subject["operations"])))
    missing = scope_rows["missing"]
    rows.append(_criterion(
        "SUBJECT_OBLIGATIONS_MEASURED", "变更主体及其生命周期的已批准义务都有执行证据",
        MET if not missing else UNMET,
        f"主体范围内 {len(scope_rows['measured'])} 项义务已测、{len(missing)} 项未测。",
        dict(measured=len(scope_rows["measured"]),
             missing=[item["policy_id"] for item in missing])))
    methods = {operation.operation_id: operation.method
               for operation in project.contract.operations}
    policies = {policy.id: policy for policy in project.registry.definitions()}
    deletes = {policy_id for policy_id in subject["policy_ids"]
               if policy_id in policies
               and methods.get(policies[policy_id].operation_id) == "DELETE"}
    executed_cases = _executed_cases(summary)
    delete_seen = [case for case in executed_cases
                   if any(step.get("operation_id") in subject["operations"]
                          and methods.get(step.get("operation_id")) == "DELETE"
                          and step.get("executed")
                          for step in case.get("evidence") or ())]
    if deletes:
        rows.append(_criterion(
            "SUBJECT_DELETE_OBSERVED", "变更的删除操作被真实执行",
            MET if delete_seen else UNMET,
            "删除操作已按批准策略执行。" if delete_seen
            else "删除操作没有实际执行证据；仅有读取不能说明删除回归通过。",
            dict(cases=[case.get("case_id") for case in delete_seen])))
    else:
        rows.append(_criterion("SUBJECT_DELETE_OBSERVED", "变更的删除操作被真实执行",
                               EXEMPT, "主体范围内没有删除策略。"))
    absence = [policy_id for policy_id in subject["policy_ids"]
               if any(policy.id == policy_id and policy.negative is None
                      and (policy.assertions.status or None) is not None
                      and set(policy.assertions.status.codes) <= {404, 410}
                      for policy in project.registry.definitions())]
    if absence:
        verified = []
        for case in executed_cases:
            steps = {step.get("operation_id"): step for step in case.get("evidence") or ()}
            rows_absence = case.get("absence_expectations") or ()
            if not any(row.get("policy_id") in absence and row.get("satisfied")
                       for row in rows_absence):
                continue
            if any(step.get("executed") and methods.get(step.get("operation_id")) == "DELETE"
                   and step.get("outcome") == "PASS" for step in steps.values()):
                verified.append(case)
        rows.append(_criterion(
            "POST_DELETE_ABSENCE_VERIFIED", "删除后的缺失断言在删除成立后得到验证",
            MET if verified else UNMET,
            f"用例 {verified[0].get('case_id')} 在删除成功后验证了缺失断言。"
            if verified else "没有用例在删除成功之后再验证相同的缺失断言。",
            dict(cases=[case.get("case_id") for case in verified], policies=absence)))
    else:
        rows.append(_criterion("POST_DELETE_ABSENCE_VERIFIED",
                               "删除后的缺失断言在删除成立后得到验证", EXEMPT,
                               "操作员没有为该主体批准缺失断言（404/410）策略。"))
    other = sorted(set(_ready_operations(summary)) - set(subject["operations"]))
    rows.append(_criterion(
        "UNRELATED_SCOPE_DECLARED", "与本轮无关的接口被明确排除", EXEMPT,
        "以下就绪接口不在回归主体内，不计入完成率：" + ("、".join(other[:12]) or "无"),
        dict(excluded=other[:12])))
    return rows


def _pre_release_criteria(project, summary, scope_rows, subject, kind):
    rows = []
    missing = scope_rows["missing"]
    rows.append(_criterion(
        "ALL_OBLIGATIONS_MEASURED", "项目范围内全部已批准义务都有执行证据",
        MET if not missing else UNMET,
        f"已测 {len(scope_rows['measured'])} 项，未测 {len(missing)} 项。",
        dict(measured=len(scope_rows["measured"]),
             missing=[item["policy_id"] for item in missing])))
    groups = summary.get("groups") or ()
    undecided = [group.get("id") for group in groups
                 if group.get("status") not in {"DESIGNED", "OUTSIDE_FOCUS"}]
    rows.append(_criterion(
        "ALL_GROUPS_DECIDED", "全部测试分组都已设计并给出结论",
        MET if groups and not undecided else UNMET,
        "所有分组都已设计。" if groups and not undecided else
        ("没有测试分组。" if not groups else
         "以下分组没有形成可执行结论：" + "、".join(str(item) for item in undecided[:8])),
        dict(groups=len(groups), undecided=undecided[:8])))
    questions = _questions_in_scope(summary, kind, subject)
    rows.append(_criterion(
        "NO_OPEN_QUESTIONS", "没有阻塞上线的待确认事项",
        MET if not questions else UNMET,
        "没有待确认事项。" if not questions else
        f"仍有 {len(questions)} 项待确认，不能视为完成上线前测试。",
        dict(questions=len(questions))))
    rows.append(_negatives_row(scope_rows))
    return rows


def _investigation_criteria(project, summary, scope_rows, subject, kind):
    rows = []
    if not subject.get("resolved"):
        rows.append(_criterion("SUBJECT_RESOLVED", "调查主体可以从原文确定", UNMET,
                               "原文没有指出可调查的接口，无法界定调查范围。"))
        return rows
    rows.append(_criterion("SUBJECT_RESOLVED", "调查主体可以从原文确定", MET,
                           f"主体接口：{'、'.join(subject['operations'])}。",
                           dict(operations=subject["operations"])))
    executed = _executed_cases(summary)
    reproduced = [case for case in executed
                  if set(case.get("executed_operations") or ()) & set(subject["operations"])]
    rows.append(_criterion(
        "SUBJECT_REPRODUCED", "对调查主体完成了真实复现尝试",
        MET if reproduced else UNMET,
        f"{len(reproduced)} 个用例真实请求了主体接口。" if reproduced
        else "没有任何用例真实请求调查主体，调查目标没有完成。",
        dict(cases=[case.get("case_id") for case in reproduced][:8])))
    executed = _executed_cases(summary)
    observations = [check for case in executed
                    if case.get("case_id") in {item.get("case_id") for item in reproduced}
                    for step in case.get("evidence") or () if step.get("executed")
                    for check in step.get("checks") or ()]
    rows.append(_criterion(
        "OBSERVATIONS_RECORDED", "保留了可核对的实际观测",
        MET if observations else UNMET,
        f"记录了 {len(observations)} 项实际检查结果。",
        dict(checks=len(observations))))
    confirmed, unproven = _anomalies(summary)
    rows.append(_criterion(
        "CONSEQUENCE_STATED", "异常是否可归因被明确说明",
        MET if not unproven else UNMET,
        (f"已确认异常 {len(confirmed)} 条、未确认原因 {len(unproven)} 条，均未冒充已确认缺陷。"
         if (confirmed or unproven) else "本轮没有观察到异常。"),
        dict(confirmed=len(confirmed), unconfirmed=len(unproven))))
    probed = sorted(set(_case_operations(summary)) & set(subject["operations"]))
    boundary = dict(probed=probed,
                    unprobed=sorted(set(subject["operations"]) - set(probed)),
                    out_of_scope=sorted(set(_ready_operations(summary))
                                        - set(subject["operations"])))
    rows.append(_criterion(
        "INVESTIGATION_BOUNDARY", "调查边界明确",
        MET if probed else UNMET,
        "已探测：" + ("、".join(boundary["probed"]) or "无")
        + "；未探测：" + ("、".join(boundary["unprobed"]) or "无")
        + "；不在调查范围：" + ("、".join(boundary["out_of_scope"][:8]) or "无"),
        boundary))
    return rows


def evaluate(project, summary, text, *, stop=None) -> dict:
    """The deterministic completion report for one round; no model output is trusted here."""
    stop = dict(stop or {})
    classification = classify_task(text)
    kind = classification["kind"]
    subject = subject_scope(project, text, kind)
    scope_rows = _scope_rows(project, summary, subject, kind)
    common = _common_criteria(summary)
    if kind == REGRESSION:
        specific = _regression_criteria(project, summary, scope_rows, subject, kind)
    elif kind == PRE_RELEASE:
        specific = _pre_release_criteria(project, summary, scope_rows, subject, kind)
    elif kind == INVESTIGATION:
        specific = _investigation_criteria(project, summary, scope_rows, subject, kind)
    else:
        specific = _functional_criteria(project, summary, scope_rows, subject, kind)
    questions = _questions_in_scope(summary, kind, subject)
    if kind in {FUNCTIONAL, REGRESSION}:
        specific = specific + [_criterion(
            "NO_OPEN_QUESTIONS", "没有影响本轮的待确认事项",
            MET if not questions else UNMET,
            "没有待确认事项。" if not questions else
            f"仍有 {len(questions)} 项待确认事项，相关接口没有完成测试。",
            dict(questions=len(questions)))]
    criteria = common + specific
    required = [row for row in criteria if row["status"] != EXEMPT]
    unmet = [row for row in required if row["status"] != MET]
    complete = not unmet
    confirmed, unproven = _anomalies(summary)
    executed = _executed_cases(summary)
    # ``checks_passed`` answers one question only: did every executed check pass? Coverage
    # (missing obligations, open questions) belongs to ``complete``; mixing them would report
    # a partially covered round as "checks failed".
    passed = (bool(executed)
              and all(case.get("outcome") == "PASS" for case in executed)
              and not confirmed)
    pending = [dict(item="question", detail=question.get("question"),
                    operation_id=question.get("operation_id")) for question in questions]
    pending.extend(dict(item="obligation", detail=item["policy_id"],
                        operation_id=item["operation_id"]) for item in scope_rows["missing"])
    pending.extend(dict(item="resource", detail=row.get("create_step"),
                        operation_id=None) for row in
                   _created_resources(summary)["pending"])
    ended = stop.get("reason") or "UNKNOWN"
    flags = dict(
        agent_terminated=ended in {"ANSWER", "MODEL_CALL_LIMIT", "TURN_LIMIT",
                                   "TOOL_CALL_LIMIT", "NO_FURTHER_ACTION"},
        agent_stop_reason=ended,
        evidence_produced=bool(_executed_cases(summary)),
        obligations_covered=not scope_rows["missing"],
        task_complete=complete,
        anomalies_found=bool(confirmed),
        pending_items=bool(pending))
    return dict(
        schema="agenttest.task-completion.v1",
        kind=kind, kind_reason=classification["reason"], kind_cues=classification["cues"],
        subject=subject,
        complete=complete, checks_passed=passed,
        criteria=criteria, unmet=[row["id"] for row in unmet],
        flags=flags,
        anomalies=dict(confirmed=confirmed, unconfirmed=unproven),
        pending=pending[:20],
        coverage=dict(obligations=len(scope_rows["scope"]),
                      measured=len(scope_rows["measured"]),
                      missing=len(scope_rows["missing"]),
                      blocked=len(scope_rows["blocked"]),
                      operations=sorted(_ready_operations(summary))),
        notes=[row["detail"] for row in criteria if row["status"] != MET][:8],
    )


def public_view(report: dict) -> dict:
    """A compact view for a task summary or a web response (never includes secrets)."""
    return dict(schema=report.get("schema"), kind=report.get("kind"),
                kind_reason=report.get("kind_reason"),
                complete=report.get("complete"),
                checks_passed=report.get("checks_passed"),
                flags=report.get("flags"), unmet=report.get("unmet"),
                criteria=[dict(id=row["id"], status=row["status"], detail=row["detail"])
                          for row in report.get("criteria") or ()],
                coverage=report.get("coverage"), pending=report.get("pending"),
                anomalies=report.get("anomalies"), notes=report.get("notes"))
