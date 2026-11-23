"""Model-facing projection of case-level precheck refusals (P3-B2.5).

The precheck already stores a structured ``preflight_diagnostic`` per rejected case, but the
refusal the model read carried only ``reason``: it could not tell which field it invented, which
field names the approved schema allows, or which obligation the case belonged to, so it resubmitted
the same shape. This module projects that stored record through an explicit whitelist.

What may travel: schema-owned field names, masked field paths, small array indices, step and
obligation identifiers, and closed enum words. What never travels: request or response values,
credential slots, free text copied from the model, and any key that is not listed in ``FACTS``.
The projection only *reports* facts the precheck already decided; it never re-validates, never
accepts a rejected plan, and never changes a code.
"""

import re

from agenttest.model_input import PATH_PART, safe_code

MAX_CASES_FULL = 4     # per refusal: full facts; the rest stay locatable but compact
MAX_CASES_TOTAL = 16   # hard bound; every case up to here keeps its identity and code
MAX_NAMES = 6          # schema field names per fact
MAX_POSITIONS = 6      # body indices per fact
MAX_PATHS = 8          # masked field paths per case
MAX_CASES = MAX_CASES_FULL  # backward-compatible name for the full-fact cap

STEP_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,63}$")
FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
ENUM_WORD = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# A model can paste a credential or a value into a name slot, and such a string can still look
# like an identifier (``AKIA…``, ``sk-…``, a hex digest). These shapes are refused everywhere, so
# a name-shaped secret is dropped instead of echoed.
SECRET_SHAPED = re.compile(
    r"(?i:secret|token|password|passwd|api[-_]?key|authorization|bearer|credential|private)"
    r"|sk-[A-Za-z0-9_\-]{6,}|(?:AKIA|ASIA)[A-Za-z0-9]{8,}"
    r"|[a-f0-9]{16,}|[A-Z0-9]{12,}")

# Whitelist: stored detail key -> how its value must look. An unlisted key is dropped, so a new
# producer cannot leak a value by adding a field to ``Diagnostic.detail``.
FACTS = {
    "step_id": "STEP_ID",
    "obligation_id": "STEP_ID",
    "step_index": "INDEX",
    "allowed_body_fields": "NAMES",
    "allowed_fields": "NAMES",
    "unknown_fields": "NAMES",
    "missing_fields": "NAMES",
    "allowed_from_steps": "NAMES",
    "unknown_body_positions": "INDEXES",
    "parameter_location": "ENUM",
    "expected": "ENUM",
    "expected_type": "ENUM",
    "actual_type": "ENUM",
    "constraint": "ENUM",
    "violation": "ENUM",
    "reference": "ENUM",
    "limit": "INDEX",
    "allowed_count": "INDEX",
    # Cleanup read-back facts (P3-B2.5): which resource, deleted where, which approved proof is
    # missing and where it must run. Every value is an identifier or an enum word.
    "create_step": "STEP_ID",
    "delete_step": "STEP_ID",
    "read_policy": "STEP_ID",
    "lifecycle_create": "STEP_ID",
    "lifecycle_delete": "STEP_ID",
    "lifecycle_parameter": "STEP_ID",
    "proof_kind": "ENUM",
    "required_position": "ENUM",
}
UNKNOWN_FIELD_CODES = frozenset({"OPENAPI_UNKNOWN_BODY_FIELD", "OPENAPI_OBJECT_FIELDS"})

HINTS = {
    "UNKNOWN_FIELD": ("该对象只接受 allowed_body_fields/allowed_fields 中的字段名：删除 fields 指出"
                      "的多余成员（body 下标见 unknown_body_positions），其余步骤、策略与预期"
                      "不变。"),
    "UNKNOWN_FIELD_TRUNCATED": (
        "allowed_body_fields/allowed_fields 只列出了部分：total 是完整数量（truncated=true）。"
        "未列出的字段名不代表不合法，先用已批准策略的 binds[].name 核对，"
        "只删除 fields 指出的多余成员（body 下标见 unknown_body_positions），不要因为没列出来"
        "就删掉合法字段。"),
    "MISSING_FIELD": "缺少必填字段：补齐 missing_fields 列出的字段名，不要新增其他成员。",
    "TYPE_MISMATCH": "该位置的类型或取值不符合批准 Schema：按 fields 指出的路径改成要求的类型。",
    "ARGUMENT_LOCATION": ("该步骤在 body/path_parameters/query/headers 里的参数名必须与该策略已批准"
                          "的绑定一致：删掉未批准的多余参数、补回缺少的参数，不要改名或换"
                          " location。"),
    "REFERENCE_ERROR": ("引用只能指向本方案已声明的步骤或已绑定来源：按 fields 与"
                        " allowed_from_steps 修正引用。"),
    "OTHER": "按 code 与 fields 指出的位置修正该案例；预检未通过时不会发送任何业务请求。",
}
CODE_HINTS = {
    "OPENAPI_VALUE_LENGTH": "该值长度超出批准范围：按 fields 指出的位置改成范围内长度，不换字段。",
    "OPENAPI_VALUE_BOUND": "该值超出批准的上下界：按 fields 指出的位置改回范围内。",
    "OPENAPI_VALUE_ENUM": "该值不在批准的枚举内：按 fields 指出的位置改用已批准取值之一。",
    "DESIGN_ABSENCE_PRECONDITION_MISSING": ("404/410 断言必须在同一资源已批准删除之后："
                                             "补上删除步骤或去掉该断言。"),
    "DESIGN_CLEANUP_READ_BACK_MISSING": ("创建的资源已计划删除，但缺少位于删除之后的已批准"
                                          "缺失回读：在 delete_step 之后、用同一身份"
                                          "（lifecycle_parameter 绑定 create_step）按 read_policy "
                                          "补一个 404/410 回读；204 与最终库存为 0 都不算证明。"),
}


def repair_kind(code: str, diagnostic: dict | None = None) -> str:
    """One of six closed repair categories, derived only from facts the precheck recorded."""
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    if diagnostic.get("reference"):
        return "REFERENCE_ERROR"
    if code in UNKNOWN_FIELD_CODES or code.endswith("_SCHEMA_EXTRA_FIELD"):
        return "UNKNOWN_FIELD"
    if code.endswith("_SCHEMA_MISSING_FIELD"):
        return "MISSING_FIELD"
    if code.endswith("_SCHEMA_TYPE_OR_CONSTRAINT"):
        return "TYPE_MISMATCH"
    if code == "POLICY_ARGUMENT_MISMATCH":
        return "ARGUMENT_LOCATION"
    return "OTHER"


def _safe(value: str) -> bool:
    return not SECRET_SHAPED.search(value)


def _identifier(value):
    return (value if isinstance(value, str) and STEP_ID.match(value) and _safe(value)
            else None)


def _names(value):
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        return None
    kept = []
    for item in items:
        if (isinstance(item, str) and FIELD_NAME.match(item) and _safe(item)
                and item not in kept):
            kept.append(item)
    return kept or None


def _indexes(value):
    items = [value] if type(value) is int else value
    if not isinstance(items, (list, tuple)):
        return None
    kept = []
    for item in items:
        if type(item) is int and 0 <= item <= 99 and item not in kept:
            kept.append(item)
    return kept or None


def _index(value):
    return value if type(value) is int and 0 <= value <= 999 else None


def _enum(value):
    return value if isinstance(value, str) and ENUM_WORD.match(value) and _safe(value) else None


_SANITIZERS = {"STEP_ID": _identifier, "NAMES": _names, "INDEXES": _indexes,
               "INDEX": _index, "ENUM": _enum}


def _path(value):
    """A masked field path: schema names, ``*`` for a masked key, and small indices only."""
    if not isinstance(value, str) or len(value) > 96:
        return None
    parts = value.split(".")
    return value if parts and all(PATH_PART.match(part) and _safe(part)
                                  for part in parts) else None


def _limit(kind) -> int:
    if kind == "NAMES":
        return MAX_NAMES
    if kind == "INDEXES":
        return MAX_POSITIONS
    return 1


def facts(detail) -> tuple[dict, dict]:
    """The whitelisted subset of one stored ``detail``, plus how much was left out."""
    if not isinstance(detail, dict):
        return {}, {}
    kept, omitted = {}, {}
    for key, kind in FACTS.items():
        if key not in detail:
            continue
        raw = detail[key]
        value = _SANITIZERS[kind](raw)
        if value is None:
            # Nothing usable survived the shape check: count what the model is not shown.
            total = len(raw) if isinstance(raw, (list, tuple)) else 1
            if total:
                omitted[key] = total
            continue
        if isinstance(value, list):
            total = len(raw) if isinstance(raw, (list, tuple)) else 1
            if len(value) > _limit(kind):
                value = _ranked(key, value, detail)[:_limit(kind)]
            if total > len(value):
                omitted[key] = total - len(value)   # refused entries plus the truncated tail
        kept[key] = value
    return kept, omitted


def _ranked(key, names, detail):
    """Failing-path names first, then declaration order: truncation must not hide the culprit."""
    tokens = set()
    for item in detail.get("fields") or ():
        tokens.update(part for part in str(item).split("."))
    order = {name: index for index, name in enumerate(names)}
    return sorted(names, key=lambda name: (name not in tokens, order[name]))


def public_diagnostic(diagnostic) -> dict:
    """One stored diagnostic as the model may see it: closed codes plus whitelisted facts."""
    if not isinstance(diagnostic, dict):
        return {}
    code = safe_code(diagnostic.get("code"))
    out = dict(code=code, repair_kind=repair_kind(code, diagnostic))
    if diagnostic.get("layer"):
        out["layer"] = _enum(str(diagnostic["layer"]).upper()) or None
        if out["layer"] is None:
            out.pop("layer")
    paths = [path for path in (_path(item) for item in diagnostic.get("fields") or ()) if path]
    if paths:
        if len(paths) > MAX_PATHS:
            out["omitted"] = {"fields": len(paths) - MAX_PATHS}
            paths = paths[:MAX_PATHS]
        out["fields"] = paths
    reference = diagnostic.get("reference")
    if isinstance(reference, dict) and _enum(str(reference.get("branch") or "")):
        out["reference"] = dict(branch=reference["branch"],
                                step_index=_index(reference.get("step_index")))
    kept, omitted = facts(diagnostic.get("detail"))
    if kept:
        out["detail"] = kept
    if omitted:
        out.setdefault("omitted", {}).update(omitted)
    return out


def case_failure(case) -> dict | None:
    """One rejected case reduced to identity, closed code, category and whitelisted facts.

    A list fact that hits its bound is marked ``<key>_truncated`` with the untruncated
    ``<key>_total``, and the hint is swapped for one that says the list is partial: a bound must
    never turn "these are the allowed names" into a false claim that the rest are illegal.
    """
    if not isinstance(case, dict):
        return None
    diagnostic = case.get("preflight_diagnostic")
    code = safe_code(case.get("reason")) or safe_code(
        (diagnostic or {}).get("code") if isinstance(diagnostic, dict) else "") or "PLAN_REJECTED"
    kind = repair_kind(code, diagnostic)
    row = dict(case_id=_identifier(case.get("case_id")), code=code, repair_kind=kind)
    public = public_diagnostic(diagnostic)
    if public.get("fields"):
        row["fields"] = public["fields"]
    kept, omitted = facts((diagnostic or {}).get("detail")
                          if isinstance(diagnostic, dict) else None)
    row.update(kept)
    for key in sorted(omitted):
        if isinstance(kept.get(key), list):
            row[f"{key}_total"] = len(kept[key]) + omitted[key]
            row[f"{key}_truncated"] = True
    obligations = sorted({item for item in (_identifier(value) for value in
                                            case.get("planned_policy_ids") or ()) if item})
    if obligations:
        row["policy_ids"] = obligations[:MAX_NAMES]
        if len(obligations) > MAX_NAMES:
            omitted["policy_ids"] = len(obligations) - MAX_NAMES
    step = case.get("step_id") or kept.get("step_id")
    if step and not row.get("step_id") and _identifier(step):
        row["step_id"] = step
    truncated = any(key.endswith("_truncated") for key in row)
    row["hint"] = CODE_HINTS.get(code) or (
        HINTS["UNKNOWN_FIELD_TRUNCATED"]
        if truncated and kind == "UNKNOWN_FIELD" and code not in CODE_HINTS else HINTS[kind])
    if truncated and kind != "UNKNOWN_FIELD" and code not in CODE_HINTS:
        row["hint"] = row["hint"] + "列表被截断的字段见 <key>_total，未列出的条目不代表不存在。"
    if omitted or public.get("omitted"):
        merged = dict(public.get("omitted") or {})
        merged.update(omitted)
        row["omitted"] = merged
    return row


def project_cases(cases, *, limit: int = MAX_CASES_FULL,
                  total: int = MAX_CASES_TOTAL) -> tuple[list[dict], int, int]:
    """Every rejected case stays locatable: full facts, then compact rows, then a counted rest.

    Returns the listed rows, how many of them are compact, and how many passed ``total``.
    A compact row still carries ``case_id`` and ``code``, so no rejected case can be silently
    missing from the feedback even when the detail budget is spent.
    """
    rows = [row for row in (case_failure(case) for case in cases or ()) if row is not None]
    listed, compacted, omitted = [], 0, 0
    for index, row in enumerate(rows):
        if index < limit:
            listed.append(row)
        elif index < total:
            listed.append(dict(case_id=row.get("case_id"), code=row["code"],
                               repair_kind=row["repair_kind"], compact=True))
            compacted += 1
        else:
            omitted += 1
    return listed, compacted, omitted


def rejection_error(cases, *, retryable: bool, message: str) -> dict:
    """The refusal object for a group whose cases all failed precheck (0 business requests)."""
    rows, compacted, omitted = project_cases(cases)
    first = rows[0] if rows else {}
    error = dict(code=first.get("code") or "PLAN_REJECTED", message=message,
                 retryable=retryable, hint=first.get("hint") or HINTS["OTHER"])
    if first.get("fields"):
        error["fields"] = first["fields"]
    # The shared hint is stated once; a case that needs a different one keeps its own.
    for row in rows:
        if row.get("hint") == error["hint"]:
            row.pop("hint")
    if rows:
        error["cases"] = rows
        error["cases_total"] = len(rows) + omitted
    if compacted:
        error["compact_cases"] = compacted
    if omitted:
        error["omitted_cases"] = omitted
    return error
