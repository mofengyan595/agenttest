"""Deterministic requirement-to-rubric consistency lint, with no model and no network.

A rubric is an independent answer key: it states which approved operations must run, in
which order, under which identity, and which clauses must stay pending. This lint only
compares that key with the operator's own words, so a key that asks for an action the
requirement never mentions, drops one it does mention, swaps an identity, reorders the
clauses or invents a pending expectation is reported before any paid position starts.

Finding kinds are stable so callers and tests can assert them:

``extra_action``       a rubric step whose operation is never named in the requirement
``missing_action``     a named operation with an action verb that no step or gap covers
``wrong_identity``     a step role that contradicts the identity named in its clause
``role_not_approved``  a step role the approved project does not allow for that operation
``wrong_order``        ``order`` is not a permutation, or contradicts clause order
``wrong_hold``         a pending expectation the requirement does not justify, or an
                       approved assertion kept pending anyway
``missing_hold``       an unapproved assertion in the text that no gap covers
``unjustified_repeat`` a repeated step without repetition wording in the requirement
"""

import re

ACTIONS = ("读取", "查询", "取回", "打开", "核对", "查看", "获取", "调用", "访问", "确认")
REPEAT = re.compile(
    r"重复|再次|再(?:查|读|调用|请求)|重新(?:查|读)|分别|间隔|先后|前后|"
    r"([2-9]|[1-9]\d+|[二两三四五六七八九十]+)次|连续|两遍|各一?次"
)
PARALLEL = re.compile(r"同时|并行|一边|以及")
INQUIRY = re.compile(
    r"权限|403|404|拒绝(?:规则|状态|行为|结果|方式)|是否拒绝|"
    r"是否.*(?:允许|访问|读取)"
)
SELECTOR = re.compile(r"\$?(?:\.[A-Za-z_][\w-]*(?:\[-?\d+\])?)+")
ASSERTION = re.compile(r"核对|校验|断言|等于")


def _clauses(text):
    return [part.strip() for part in re.split(r"[；;。\n]", text) if part.strip()]


def _clause_at(text, position):
    start = 0
    for part in _clauses(text):
        index = text.find(part, start)
        if index <= position <= index + len(part):
            return part
        start = index + len(part)
    return text


def _mention(text, label):
    return re.search(re.escape(label), text).start() if label and label in text else None


def _named(clause, aliases):
    return [
        alias
        for alias in aliases
        if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(alias) + r"(?![A-Za-z0-9_-])", clause)
    ]


def _expected_role(text, position, clause, aliases):
    """Identity named inside the clause, else the nearest identity named before it."""
    named = _named(clause, aliases)
    if named:
        return named[-1]
    best, role = -1, None
    for alias in aliases:
        for match in re.finditer(
            r"(?<![A-Za-z0-9_-])" + re.escape(alias) + r"(?![A-Za-z0-9_-])", text[:position]
        ):
            if match.start() > best:
                best, role = match.start(), alias
    return role


def _selectors(text):
    return {match.group(0) for match in SELECTOR.finditer(text)}


def _assertions(text, project):
    """(operation, selector) pairs the requirement asks to verify, by clause label."""
    pairs = []
    for clause in _clauses(text):
        if not ASSERTION.search(clause):
            continue
        owners = [
            operation
            for operation, descriptor in project.items()
            if descriptor.get("labels") and descriptor["labels"] in clause
        ]
        if not owners:
            continue
        for selector in _selectors(clause):
            pairs.append((owners[0], selector))
    return pairs


def lint(text, rubric, project, aliases):
    """Compare one requirement text with its rubric and the approved project scope.

    ``project`` maps an operation id to ``{"role": str|None, "labels": str,
    "checks": [(selector, operator, expected)]}`` as approved for this task.
    """
    findings = []

    def add(kind, detail, **extra):
        findings.append(dict(kind=kind, detail=detail, **extra))

    steps = list(rubric.get("steps") or [])
    gaps = list(rubric.get("gaps") or [])
    order = list(rubric.get("order") or [])
    if order and sorted(order) != list(range(len(steps))):
        add("wrong_order", f"order {order} is not a permutation of {len(steps)} steps")

    for index, step in enumerate(steps):
        label = project.get(step["operation"], {}).get("labels", "")
        if _mention(text, label) is None:
            add(
                "extra_action",
                f"step {index} {step['operation']} ({label or 'no label'}) is absent from "
                f"the requirement",
                step=index,
                operation=step["operation"],
            )

    covered = {step["operation"] for step in steps} | {gap["operation"] for gap in gaps}
    for operation, descriptor in project.items():
        label = descriptor.get("labels") or ""
        position = _mention(text, label)
        if position is None or operation in covered:
            continue
        if any(verb in _clause_at(text, position) for verb in ACTIONS):
            add(
                "missing_action",
                f"requirement names {operation} ({label}) with an action verb but neither a "
                f"step nor a gap covers it",
                operation=operation,
            )

    for index, step in enumerate(steps):
        operation = step["operation"]
        descriptor = project.get(operation) or {}
        approved_role = descriptor.get("role")
        position = _mention(text, descriptor.get("labels") or "")
        expected = (
            None
            if approved_role is None
            else (
                _expected_role(text, position, _clause_at(text, position), aliases)
                if position is not None
                else approved_role
            )
        )
        if expected != step.get("role"):
            add(
                "wrong_identity",
                f"step {index} {operation} uses {step.get('role')!r} but the clause names "
                f"{expected!r}",
                step=index,
                operation=operation,
            )
        if step.get("role") != approved_role:
            add(
                "role_not_approved",
                f"step {index} {operation} uses {step.get('role')!r} but the project approves "
                f"{approved_role!r}",
                step=index,
                operation=operation,
            )

    if order and not PARALLEL.search(text):
        positions = [
            _mention(text, project.get(step["operation"], {}).get("labels") or "") for step in steps
        ]
        for left, right in zip(order, order[1:]):
            if left >= len(positions) or right >= len(positions):
                continue
            first, second = positions[left], positions[right]
            if first is None or second is None:
                continue
            if first > second:
                add(
                    "wrong_order",
                    f"order runs step {left} after step {right} but the requirement states "
                    f"them the other way round",
                )
            if (
                steps[left]["operation"] == steps[right]["operation"]
                and first == second
                and not REPEAT.search(text)
            ):
                add(
                    "unjustified_repeat",
                    f"step {right} repeats {steps[left]['operation']} without repetition wording",
                )

    approved = {
        (operation, selector): expected
        for operation, descriptor in project.items()
        for selector, operator, expected in descriptor.get("checks") or []
    }
    for gap in gaps:
        operation = gap["operation"]
        descriptor = project.get(operation) or {}
        label = descriptor.get("labels") or ""
        if _mention(text, label) is None:
            add(
                "wrong_hold",
                f"gap on {operation} ({label or 'no label'}) is not mentioned in the requirement",
                operation=operation,
            )
            continue
        if gap.get("kind") == "permission":
            role = gap.get("role")
            if not INQUIRY.search(text) or role not in _named(text, aliases):
                add(
                    "wrong_hold",
                    f"permission gap on {operation}/{role} has no refusal question naming {role!r}",
                    operation=operation,
                )
            elif role == descriptor.get("role"):
                add(
                    "wrong_hold",
                    f"permission gap on {operation}/{role} asks about the operation's own "
                    f"approved role",
                    operation=operation,
                )
        else:
            selector = gap.get("selector") or ""
            if selector and selector not in _selectors(text):
                add(
                    "wrong_hold",
                    f"content gap on {operation} uses selector {selector} the requirement "
                    f"never names",
                    operation=operation,
                )
            if (operation, selector) in approved:
                add(
                    "wrong_hold",
                    f"content gap on {operation} {selector} is already approved as "
                    f"{approved[(operation, selector)]!r}",
                    operation=operation,
                )
    for operation, selector in _assertions(text, project):
        if (operation, selector) in approved:
            continue
        if not any(
            gap["operation"] == operation and gap.get("selector") == selector for gap in gaps
        ):
            add(
                "missing_hold",
                f"requirement asks to check {selector} on {operation} without approval and "
                f"no gap covers it",
                operation=operation,
            )
    return findings


def format_findings(findings):
    return "\n".join(f"{row['kind']}: {row['detail']}" for row in findings) or "consistent"
