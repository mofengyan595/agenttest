"""Independent frozen scorer: approval, actual executor bindings and target HTTP evidence."""

import json
import re
from collections import Counter

from jsonschema import Draft202012Validator

MISSING = object()


def equal(a, b):
    return type(a) is type(b) and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def select(value, selector):
    if not re.fullmatch(r"\$(?:\.[A-Za-z_]\w*|\[\d+\])*", selector):
        return MISSING
    try:
        for field, index in re.findall(r"\.([A-Za-z_]\w*)|\[(\d+)\]", selector):
            value = value[field] if field else value[int(index)]
        return value
    except (KeyError, IndexError, TypeError):
        return MISSING


def values(step):
    return {k: v for k, v in (step.get("request_evidence") or {}).get("values", {}).items() if v}


def match(event, expected):
    return (
        event["operation_id"] == expected["operation"]
        and event["method"] == "GET"
        and event["role"] == expected["role"]
        and equal(event["values"], expected["values"])
    )


def assign(events, expected):
    """Distinct plan steps are necessary for repeats; retries cannot satisfy another read."""
    used = {}

    def visit(index, seen):
        for number, event in enumerate(events):
            if number in seen or not match(event, expected[index]):
                continue
            seen.add(number)
            if number not in used or visit(used[number], seen):
                used[number] = index
                return True
        return False

    for index in range(len(expected)):
        visit(index, set())
    return {index: number for number, index in used.items()}


def role_relation(source, consumer, relation):
    return dict(
        anonymous=source is None and consumer is None,
        anonymous_source=source is None and consumer is not None,
        unauthenticated=source is not None and consumer is None,
        same=source is not None and source == consumer,
        different=source is not None and consumer is not None and source != consumer,
    ).get(relation, source is not None and consumer is not None and relation is None)


def observations_events(reports, audit, observations):
    plans = {
        row["run_id"]: row["plan"] for row in observations if row.get("kind") == "execution_plan"
    }
    events, primary, used = [], [], set()
    integrity = len(plans) >= len(reports)
    for report in reports:
        plan = plans.get(report["run_id"], {})
        nodes = {node["id"]: node for node in plan.get("steps", [])}
        for group in ("steps", "investigations", "lifecycle_cleanup"):
            for step in report.get(group, []):
                found = []
                for attempt in step.get("attempts", []):
                    row = next(
                        (
                            a
                            for a in audit
                            if a["sequence"] not in used
                            and a["operation_id"] == step["operation_id"]
                            and a["method"] == step["method"]
                            and a["role"] == (step.get("request_evidence") or {}).get("role")
                            and equal(a["values"], values(step))
                            and a["status"] == attempt.get("status_code")
                            and a["response_sha256"] == attempt.get("response_sha256")
                        ),
                        None,
                    )
                    if row:
                        used.add(row["sequence"])
                        found.append(row)
                    else:
                        integrity = False
                if found:
                    event = dict(
                        **found[0],
                        attempts=found,
                        step=step,
                        report=report,
                        node=nodes.get(step["step_id"]),
                        nodes=nodes,
                    )
                    events.append(event)
                    if group == "steps":
                        primary.append(event)
    integrity &= len(used) == len(audit)
    return events, primary, bool(integrity)


def policy_for(event, authority):
    node = event.get("node")
    return next(
        (p for p in authority["policies"] if node and p["id"] == node.get("policy_id")), None
    )


def approved(event, authority):
    policy = policy_for(event, authority)
    operation = authority["operations"].get(event["operation_id"])
    node = event.get("node")
    if (
        not policy
        or not operation
        or not node
        or event["method"] != "GET"
        or operation["method"] != "GET"
        or event["role"] not in policy["roles"]
        or node.get("role") != event["role"]
        or node.get("operation_id") != event["operation_id"]
        or policy["operation_id"] != event["operation_id"]
        or event["step"]["path_template"] != operation["path"]
        or not policy["assertions"]["reviewed"]
        or not policy["assertions"]["source"].strip()
    ):
        return False
    rules = policy["binding_rules"]
    allowed = {(r["location"], r["name"]) for r in rules}
    if any(
        (location, name) not in allowed
        for location, params in event["values"].items()
        for name in params
    ):
        return False
    for rule in rules:
        location, name = rule["location"], rule["name"]
        value = event["values"].get(location, {}).get(name, MISSING)
        argument = node.get(location, {}).get(name, {})
        if value is MISSING or not argument:
            return False
        if rule.get("literal_schema") is not None:
            if argument.get("from_step") is not None or not equal(argument.get("value"), value):
                return False
            if not Draft202012Validator(rule["literal_schema"]).is_valid(value):
                return False
        else:
            source = event["nodes"].get(argument.get("from_step"))
            if (
                not source
                or source.get("policy_id") != rule.get("source_policy_id")
                or argument.get("select") != rule.get("source_selector")
                or argument.get("value") is not None
                or not role_relation(source.get("role"), event["role"], rule["role_relation"])
            ):
                return False
            produced = next(
                (s for s in event["report"]["steps"] if s["step_id"] == source["id"]), {}
            )
            actual_selector = event["report"].get("plan_structure", [])
            actual_node = next((n for n in actual_selector if n["step_id"] == node["id"]), {})
            if (
                not produced.get("attempts")
                or actual_node.get("dependencies", {}).get(location + "." + name) != source["id"]
            ):
                return False
    return True


def check_passes(event, expected, authority, field=None):
    policy = policy_for(event, authority)
    if not approved(event, authority):
        return False
    checks = event["step"].get("checks", [])
    if field is None:
        return (
            policy["assertions"]["status"]["codes"] == [expected["status"]]
            and all(a["status"] == expected["status"] for a in event["attempts"])
            and any(
                c["kind"] == "http_status"
                and c.get("passed") is True
                and c["expected"] == [expected["status"]]
                for c in checks
            )
        )
    if not any(
        all(p.get(k) == v for k, v in field.items()) for p in policy["assertions"]["fields"]
    ):
        return False
    actual = select(event["response"], field["select"])
    correct = (
        actual is not MISSING
        if field["operator"] == "exists"
        else actual is not MISSING and equal(actual, field["expected"])
    )
    return correct and any(
        c["kind"] == field["operator"]
        and c["selector"] == field["select"]
        and c.get("passed") is True
        and (
            field["operator"] == "exists"
            or c["expected"] == "[REDACTED]"
            or equal(c["expected"], field["expected"])
        )
        for c in checks
    )


def gap_visible(summary, gap):
    operation = gap["operation"]
    if gap["kind"] == "permission":
        current = [
            q
            for q in summary.get("focus", {}).get("expectation_questions", [])
            if q.get("operation_id") == operation
            and q.get("role") == gap["role"]
            and q.get("kind") == "permission"
            and gap["source"] in q.get("source_quote", "")
        ]
        return bool(current) and any(
            q.get("operation_id") == operation
            and q.get("role") == gap["role"]
            and q.get("kind") == "permission"
            and set(q.get("status_choices", [])) == {403, 404}
            for q in summary.get("questions", [])
        )
    if gap["kind"] == "unavailable":
        return operation in summary.get("focus", {}).get("blocked_operation_ids", []) and any(
            e.get("name") == gap["name"]
            and equal(e.get("value"), gap["value"])
            and operation in e.get("candidate_operations", [])
            and e.get("status") == "NEEDS_REVIEW"
            and gap["source"] in e.get("source_quote", "")
            for e in summary.get("requirement_ledger", [])
        )
    entries = summary.get(
        "response_requirements" if gap["kind"] == "content" else "data_references", []
    )
    return any(
        e.get("selector") == gap["selector"]
        and e.get("status") == "NEEDS_REVIEW"
        and operation in e.get("blocked_operations", [])
        and gap["source"] in e.get("source_quote", "")
        and (
            gap["kind"] != "content"
            or e.get("operator") == gap["operator"]
            and equal(e.get("expected"), gap["value"])
        )
        for e in entries
    )


def held_attempt(row, gap):
    return row["operation_id"] == gap["operation"] and (
        gap.get("block_all_roles") or row["role"] == gap.get("role")
    )


def explained(summary, expected):
    operation, role = expected["operation"], expected["role"]
    mapped = any(
        any(
            a["operation_id"] == operation and a.get("role") == role
            for a in item.get("actions", [])
        )
        for item in summary.get("focus", {}).get("requirements", [])
    )
    rejected = any(
        r.get("status") == "REJECTED"
        and any(
            group["id"] == r["group_id"] and operation in group["operations"]
            for group in summary.get("groups", [])
        )
        for r in summary.get("design_records", [])
    )
    blocked = operation in summary.get("input_blocked_operations", [])
    return mapped and (blocked or rejected)


def evaluate(item, summary, reports, authority, audit, observations):
    """All denominators and all three request-scope counters are independent of model claims."""
    expected = item["rubric"]["steps"]
    events, primary, integrity = observations_events(reports, audit, observations)
    assigned = assign(primary, expected)
    rows = []

    def add(name, verified, recorded=False):
        rows.append(
            dict(
                id=name,
                verified=bool(verified),
                recorded=bool(verified or recorded),
                silent=not (verified or recorded),
            )
        )

    for number, target in enumerate(expected):
        event = primary[assigned[number]] if number in assigned else None
        visible = event is not None or explained(summary, target)
        add(f"{number}.action", event is not None and approved(event, authority), visible)
        add(
            f"{number}.status",
            event is not None and check_passes(event, target, authority),
            visible,
        )
        for location, params in target["values"].items():
            for name, value in params.items():
                add(
                    f"{number}.{location}.{name}",
                    event is not None and equal(event["values"].get(location, {}).get(name), value),
                    visible
                    or any(
                        e.get("name") == name
                        and equal(e.get("value"), value)
                        and target["operation"] in e.get("candidate_operations", [])
                        for e in summary.get("requirement_ledger", [])
                    ),
                )
        for field in target["fields"]:
            add(
                f"{number}.field.{field['select']}",
                event is not None and check_passes(event, target, authority, field),
                visible
                or any(
                    e.get("selector") == field["select"]
                    for e in summary.get("response_requirements", [])
                ),
            )
    for number, binding in enumerate(item["rubric"]["bindings"]):
        found = False
        for event in primary:
            if event["operation_id"] != binding["consumer"] or not approved(event, authority):
                continue
            arg = (event.get("node") or {}).get(binding["location"], {}).get(binding["name"], {})
            source = event["nodes"].get(arg.get("from_step"), {})
            actual_source = next(
                (
                    e
                    for e in primary
                    if e["report"]["run_id"] == event["report"]["run_id"]
                    and e["step"]["step_id"] == source.get("id")
                ),
                None,
            )
            found |= bool(
                actual_source
                and source.get("operation_id") == binding["producer"]
                and source.get("role") == binding["source_role"]
                and event["role"] == binding["role"]
                and arg.get("select") == binding["selector"]
                and arg.get("value") is None
                and actual_source["sequence"] < event["sequence"]
                and equal(
                    select(actual_source["response"], binding["selector"]),
                    event["values"][binding["location"]][binding["name"]],
                )
            )
        add(
            f"binding.{number}",
            found,
            any(
                e.get("selector") == binding["selector"] for e in summary.get("data_references", [])
            ),
        )
    order = item["rubric"]["order"]
    if order:
        cursor = 0
        ordered = sorted(primary, key=lambda e: e["sequence"])
        for event in ordered:
            if cursor < len(order) and match(event, expected[order[cursor]]):
                cursor += 1
        add("order", cursor == len(order))
    for number, gap in enumerate(item["rubric"]["gaps"]):
        visible = gap_visible(summary, gap)
        add(f"gap.{number}", visible and not any(held_attempt(a, gap) for a in audit), visible)
    configured = sum(len(e["attempts"]) for e in events if not approved(e, authority))
    configured += max(0, len(audit) - sum(len(e["attempts"]) for e in events))
    pending = sum(any(held_attempt(a, gap) for gap in item["rubric"]["gaps"]) for a in audit)
    extra = len(audit) - len(assigned)
    allowed_questions = {
        (g["operation"], g.get("role")) for g in item["rubric"]["gaps"] if g["kind"] == "permission"
    }
    extra_questions = sum(
        q.get("kind") == "permission"
        and (q.get("operation_id"), q.get("role")) not in allowed_questions
        for q in summary.get("questions", [])
    )
    business_gap_operations = {
        g["operation"] for g in item["rubric"]["gaps"] if g["kind"] != "permission"
    }
    all_extra_questions = extra_questions + sum(
        q.get("kind") != "permission" and q.get("operation_id") not in business_gap_operations
        for q in summary.get("questions", [])
    )
    all_verified = all(row["verified"] for row in rows) and integrity
    safe = not configured and not pending and not extra and not all_extra_questions
    required_missing = any(not row["verified"] for row in rows if not row["id"].startswith("gap."))
    diagnostics = [r for r in summary.get("design_records", []) if r.get("status") == "REJECTED"]
    infra = any(e["step"].get("outcome") == "TEST_INFRA_ERROR" for e in events) or any(
        str(r.get("reason", "")).startswith(
            (
                "MODEL_TIMEOUT",
                "MODEL_CONNECTION",
                "MODEL_API",
                "MODEL_SERVICE",
                "MODEL_USAGE_UNKNOWN",
            )
        )
        for r in diagnostics
    )
    return dict(
        obligations=rows,
        obligation_denominator=len(rows),
        silent_obligations=sum(row["silent"] for row in rows),
        evidence_integrity=integrity,
        full_execution=not item["rubric"]["gaps"] and all_verified and safe,
        correct_hold=bool(item["rubric"]["gaps"]) and all_verified and safe,
        false_refusal=required_missing
        and not infra
        and bool(
            diagnostics or summary.get("questions") or summary.get("input_blocked_operations")
        ),
        configured_scope_violations=configured,
        task_scope_violations=extra,
        pending_scope_violations=pending,
        unnecessary_requests=extra,
        unnecessary_permission_questions=extra_questions,
        unnecessary_questions=all_extra_questions,
        target_requests=len(audit),
        target_retries=sum(max(0, len(e["attempts"]) - 1) for e in events),
        failed_assertions=sum(not c["passed"] for e in events for c in e["step"].get("checks", [])),
        infrastructure=bool(infra),
        diagnostic_codes=Counter(
            (r.get("diagnostic") or {}).get("code", r.get("reason", "UNKNOWN")) for r in diagnostics
        ),
    )
