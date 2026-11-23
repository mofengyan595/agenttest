"""Source-backed requests for existing negative capabilities, never new execution authority."""

import re
from copy import deepcopy

from pydantic import Field, model_serializer

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.llm_planner import LiteralValue, literal_data
from agenttest.report import fingerprint
from agenttest.safety import StrictModel

MAX_REQUESTED_CHECKS = 40
MISSING_REQUESTED_CHECKS = "FOCUS_REQUESTED_CHECKS_MISSING"
# Supplement the server's scope classification with a closed current release statement.
# An anchor with additional words may contain an exclusion even if it mentions the whole project.
_UNQUALIFIED_RELEASE = re.compile(
    r"(?:请)?\s*(?:上线|发布|发版|投产)前\s*(?:请)?\s*"
    r"(?:完整|全量|整套)?\s*(?:验收|检查|测试)\s*(?:整个项目|全项目|全部接口|整个服务)"
    r"|(?:请)?\s*(?:完整|全量|整套)\s*(?:上线|发布|发版|投产)前\s*(?:验收|检查|测试)"
    r"|(?:run\s+(?:a\s+)?)?(?:full|complete|comprehensive)\s+pre-?release"
    r"(?:\s+(?:check|test|validation|acceptance))?", re.IGNORECASE)


class ExactNegativeValue(StrictModel):
    value: LiteralValue


class RequestedCheck(StrictModel):
    policy_id: str = Field(min_length=1, max_length=160)
    source_quote: str = Field(min_length=1, max_length=300)
    exact_value: ExactNegativeValue | None = None
    exact_length: int | None = Field(default=None, ge=0, le=1000)

    @model_serializer(mode="wrap")
    def preserve_generic_check(self, handler):
        data = handler(self)
        if self.exact_value is None:
            data.pop("exact_value", None)
        if self.exact_length is None:
            data.pop("exact_length", None)
        return data


def requested_invalid_value(check, policy):
    """Materialize approved string size requirements without changing their rejection policy."""
    if check.exact_length is not None:
        if (check.exact_value is not None or policy.negative is None
                or policy.negative.kind != "boundary_outside"
                or type(policy.negative.value) is not str):
            raise ValidationFailure(Layer.MODEL, "FOCUS_NEGATIVE_LENGTH_INVALID")
        return (policy.negative.value if len(policy.negative.value) == check.exact_length
                else "x" * check.exact_length)
    return literal_data(check.exact_value.value) if check.exact_value is not None else None


def check_index(project):
    from agenttest.negative import approval_schema

    result = []
    operations = {o.operation_id: o for o in project.contract.operations}
    for policy in project.registry.definitions():
        approval = policy.negative
        if not approval or policy.operation_id not in project.target.allowed_operations:
            continue
        details = dict(policy_id=policy.id, operation_id=policy.operation_id,
            location=approval.location, name=approval.name, kind=approval.kind,
            roles=policy.roles,
            allow_value_variants=approval.allow_value_variants,
            expected_statuses=policy.assertions.status.codes)
        if approval.kind != "required_missing":
            details["invalid_value_type"] = {str: "string", int: "integer", float: "number",
                bool: "boolean", dict: "object", list: "array", type(None): "null"}[
                    type(approval.value)]
        schema, _ = approval_schema(approval, operations[policy.operation_id])
        details["field_constraints"] = {k: schema[k] for k in (
            "type", "nullable", "enum", "minimum", "maximum", "exclusiveMinimum",
            "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems") if k in schema}
        if approval.kind == "boundary_outside" and type(approval.value) in {int, float, bool}:
            details["invalid_value"] = approval.value
        elif approval.kind == "boundary_outside" and isinstance(approval.value, (str, list)):
            details["invalid_length"] = len(approval.value)
        result.append(details)
    return result


def check_groups(index):
    groups = {}
    for entry in index:
        common = {k: v for k, v in entry.items() if k not in {"policy_id", "operation_id"}}
        group = groups.setdefault(fingerprint(common), dict(**common, choices=[]))
        group["choices"].append({k: entry[k] for k in ("operation_id", "policy_id")})
    return list(groups.values())


# P3-B2.4 A1: one proposal can break several independent rules, and the single allowed repair
# round can only help if the rejection names all of them. Each rule below states the constraint
# and the edit it requires, so a repair never has to guess what "fix the error" means.
MAX_VIOLATION_PATHS = 12
CHECK_RULES = {
    MISSING_REQUESTED_CHECKS: (
        "整项目发布范围内需为每项已批准且就绪的负向策略显式提交 requested_checks 映射",
        "使用 selected，在 missing_checks 指定的 operation_id 的 requested_checks 中补齐"
        "对应 check_ref 和 source_ref。exact_value 填 null，适用时 exact_length 也填 null。"
        "保留已有合法条目；不要为可信范围子句新增 requirement_map 条目"),
    "FOCUS_REQUESTED_CHECK_INVALID": (
        "requested_checks 只能引用 scope 内已批准策略，且 source_quote 必须出现在任务原文里，"
        "同一策略只能用一次",
        "删除或改写该条目，使其引用 scope 内的已批准策略与任务原文，且不要重复同一策略"),
    "FOCUS_INPUT_TEXT_CHANGED": (
        "引用范围说明子句时 exact_value 与 exact_length 必须为 null；其它情况下取值必须"
        "原样出现在 source_quote 中",
        "把该条目的 exact_value 与 exact_length 置为 null（保留 source_ref 与 check_ref）；"
        "不要换成别的字面值"),
    "FOCUS_NEGATIVE_FIELD_CHANGED": (
        "原文用 field=value 指定字段时，取值不能迁到另一个字段",
        "保留原文指定的字段名，不要改字段"),
    "FOCUS_NEGATIVE_LENGTH_INVALID": (
        "exact_length 只能用于字符串型 boundary_outside 负向策略，且不能与 exact_value 同时出现",
        "删除 exact_length，或改为符合该策略的 exact_value"),
}


def _check_violation(code, paths, *, must_delete=(), must_be_null=(), must_match=()):
    """One violation record: closed code, field paths, the rule and the edit it requires."""
    constraint, rule = CHECK_RULES[code]
    return dict(error_code=code, field_path=paths[0], field_paths=list(paths), count=1,
                violated_constraint=constraint, expected_rule=rule,
                must_delete=list(must_delete), must_be_null=list(must_be_null),
                must_match=[dict(field_path=path, rule=text) for path, text in must_match])


def group_violations(violations):
    """Merge equal codes in first-seen order; the field order inside a code is proposal order."""
    grouped, index = [], {}
    for item in violations:
        entry = index.get(item["error_code"])
        if entry is None:
            entry = dict(error_code=item["error_code"], field_path=item["field_path"],
                         field_paths=[], count=0,
                         violated_constraint=item["violated_constraint"],
                         expected_rule=item["expected_rule"],
                         must_delete=[], must_be_null=[], must_match=[])
            index[item["error_code"]] = entry
            grouped.append(entry)
        entry["field_paths"].extend(item["field_paths"])
        entry["count"] += item.get("count") or 1
        if item["error_code"] == MISSING_REQUESTED_CHECKS:
            entry["missing_checks"] = item["missing_checks"]
        for key in ("must_delete", "must_be_null", "must_match"):
            for value in item.get(key) or ():
                if value not in entry[key]:
                    entry[key].append(value)
    return grouped


def grouped_failure(violations, *, code=None):
    """One diagnostic for all known violations; the first violation keeps today's precedence."""
    grouped = group_violations(violations)
    for item in grouped:
        item["field_paths"] = item["field_paths"][:MAX_VIOLATION_PATHS]
    error = ValidationFailure(Layer.MODEL, code or grouped[0]["error_code"])
    error.diagnostic.fields = [path for item in grouped
                               for path in item["field_paths"]][:8]
    # With the completeness rule there can be six codes: A1's independent requirement rule,
    # the four existing check rules and the missing mappings. Keep all six on that path.
    limit = 6 if any(item["error_code"] == MISSING_REQUESTED_CHECKS for item in grouped) else 4
    error.diagnostic.detail = dict(violations=grouped[:limit])
    return error


def missing_requested_checks(context, submitted):
    """Return only proven gaps from the server's current project scope and ready catalog."""
    from agenttest.model_input import IDENTIFIER, SECRET
    from agenttest.product_focus_wire import operation_checks
    from agenttest.product_sources import input_sources
    from agenttest.task_completion import is_variant_scope_clause

    scoped = context.get("trusted_scope_clauses", [])
    atoms = context.get("request_atoms", [])
    # The server recognizes the release anchor and the qualifier. Extra clauses may narrow
    # scope or refer to a different round, so leave their interpretation to the existing path.
    if (len(scoped) != 1 or len(atoms) != 2 or any(atom["future"] for atom in atoms)
            or not {"managed_authentication", "unavailable_operations", "pending_checks"}
            <= context.keys()):
        return []
    if not any(atom["source_id"] != scoped[0] and _UNQUALIFIED_RELEASE.fullmatch(atom["text"])
               for atom in atoms):
        return []
    quotes = [atom["text"] for atom in atoms if atom["source_id"] == scoped[0]
              and is_variant_scope_clause(atom["text"])]
    if len(quotes) != 1:
        return []
    source_ref = next((source["source_id"] for source in input_sources(context)
                       if source["origin"] == "current_clause" and source["text"] == quotes[0]),
                      None)
    if source_ref is None:
        return []

    ready = set(context["approved_ids"])
    # Live Focus always records positive inventory evidence. Frozen contexts recorded the
    # complete inventory's non-ready operations instead.
    if "focus_ready_operations" in context:
        ready.intersection_update(context["focus_ready_operations"])
    ready.difference_update(row["operation_id"] for row in context["unavailable_operations"])
    identities = {row["identity"] for row in context["managed_authentication"]
                  if row["credential_status"] == "READY"}
    pending = {(row["operation_id"], row["role"]) for row in context["pending_checks"]}
    required = {}
    index = context.get("approved_checks", [])
    if len({entry["policy_id"] for entry in index}) != len(index):
        return []
    for operation, entries in operation_checks(index).items():
        if operation not in ready:
            continue
        for reference, entry in entries.items():
            roles = entry.get("roles")
            if not roles or any(role is not None and (role not in identities
                    or (operation, role) in pending) for role in roles):
                continue
            if not IDENTIFIER.fullmatch(operation) or SECRET.search(operation):
                return []
            required[entry["policy_id"]] = dict(operation_id=operation, check_ref=reference,
                                                source_ref=source_ref)
    if len(required) > MAX_REQUESTED_CHECKS:
        return []  # A complete proposal cannot fit the existing Focus contract.
    return [row for policy_id, row in required.items() if policy_id not in submitted]


def requested_check_violations(checks, context, selected, *, mode=None):
    """Every independently determinable violation, in proposal order; never raises."""
    from agenttest.task_completion import is_variant_scope_clause

    index = {entry["policy_id"]: entry for entry in context.get("approved_checks", [])}
    approved = list(context.get("approved_checks", []))
    seen, violations = set(), []
    for position, check in enumerate(checks):
        entry = index.get(check.policy_id)
        previous = any(item.get("policy_id") == check.policy_id
            and check.source_quote in item.get("source_quote", "")
            for item in context.get("previous_requested_checks", []))
        if (entry is None or entry["operation_id"] not in selected
                or check.source_quote not in context["user_task"] and not previous
                or check.policy_id in seen):
            violations.append(_check_violation("FOCUS_REQUESTED_CHECK_INVALID",
                [f"requested_checks.{position}"],
                must_delete=[f"requested_checks.{position}"]))
            continue
        seen.add(check.policy_id)
        if (check.exact_value is not None or check.exact_length is not None) and (
                is_variant_scope_clause(check.source_quote)):
            # P3-B2.4 A1: name the fields that are really set. A length-only violation must not
            # be reported as a value violation, and a violation of both names both.
            paths = [f"requested_checks.{position}.{name}"
                     for name in ("exact_value", "exact_length")
                     if getattr(check, name) is not None]
            violations.append(_check_violation("FOCUS_INPUT_TEXT_CHANGED", paths,
                                               must_be_null=paths))
            continue
        # An explicit field=value request cannot migrate to another field with the same value.
        value = (literal_data(check.exact_value.value) if check.exact_value is not None
                 else entry.get("invalid_value"))
        assignments = re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*[=＝]\s*"
                                 r"(-?\d+(?:\.\d+)?)", check.source_quote)
        named = {name for name, number in assignments if type(value) in {int, float}
                 and float(number) == value}
        if named and entry.get("name") not in named:
            violations.append(_check_violation("FOCUS_NEGATIVE_FIELD_CHANGED",
                [f"requested_checks.{position}.exact_value"]))
            continue
        if check.exact_length is not None:
            from agenttest.product_inputs import _check_quote

            try:
                if (check.exact_value is not None or entry.get("kind") != "boundary_outside"
                        or entry.get("field_constraints", {}).get("type") != "string"):
                    raise ValidationFailure(Layer.MODEL, "FOCUS_NEGATIVE_LENGTH_INVALID")
                _check_quote(check.exact_length, {}, check.source_quote)
            except ValidationFailure:
                violations.append(_check_violation("FOCUS_NEGATIVE_LENGTH_INVALID",
                    [f"requested_checks.{position}.exact_length"]))
                continue
        if check.exact_value is not None:
            try:
                validate_exact_source(literal_data(check.exact_value.value), check.source_quote)
            except ValidationFailure:
                field = f"requested_checks.{position}.exact_value"
                violations.append(_check_violation("FOCUS_INPUT_TEXT_CHANGED",
                    [field, f"approved_checks.{approved.index(entry)}"],
                    must_match=[(field, "取值必须原样出现在 source_quote 中；"
                                       "不得用另一个字面值替代")]))
    if mode in ("selected", "all_ready") and (missing := missing_requested_checks(context, seen)):
        violation = _check_violation(MISSING_REQUESTED_CHECKS,
                                     ["scope.operations.*.requested_checks"])
        violations.append(dict(violation, count=len(missing), missing_checks=missing))
    return violations


def validate_requested_checks(checks, context, selected, *, mode=None):
    violations = requested_check_violations(checks, context, selected, mode=mode)
    if violations:
        raise grouped_failure(violations)


def validate_exact_source(value, quote):
    """A literal bad value is still operator data; it cannot be copied from a policy default."""
    from agenttest.product_inputs import _check_quote

    if isinstance(value, dict):
        for item in value.values():
            validate_exact_source(item, quote)
    elif isinstance(value, list):
        for item in value:
            validate_exact_source(item, quote)
    else:
        _check_quote(value, {}, quote)


def duplicates_boundary_check(requirement, value, checks, context, clauses):
    """Separate an exact duplicate from ordinary inputs without creating a negative check."""
    from agenttest.json_values import json_equal
    from agenttest.requirement_ledger import explicit_assignments

    def source_matches(quote):
        return [a["source_quote"] for a in explicit_assignments(quote)
                if a["name"] == requirement.name and not a["deferred"]
                and json_equal(a["value"], value)]

    source = source_matches(requirement.source_quote)
    if requirement.path or len(source) != 1:
        return False
    roles = {action.role for clause in clauses if clause.source_quote == requirement.source_quote
             and clause.disposition == "required" for action in clause.actions
             if action.operation_id == requirement.operation_id}
    index = {entry["policy_id"]: entry for entry in context.get("approved_checks", [])}
    matches = []
    for check in checks:
        entry = index[check.policy_id]
        exact = (literal_data(check.exact_value.value) if check.exact_value is not None
                 else entry.get("invalid_value"))
        if (entry["kind"] == "boundary_outside" and check.exact_length is None
                and entry["operation_id"] == requirement.operation_id
                and entry["location"] == requirement.location and entry["name"] == requirement.name
                and requirement.source_quote in check.source_quote
                and source_matches(check.source_quote) == source and json_equal(exact, value)
                and (not clauses or len(roles) == 1 and roles == set(entry["roles"]))):
            matches.append(entry)
    return len(matches) == 1


def partition_requested_checks(project, checks, *, input_requirements=(), input_questions=()):
    """Keep exact-value authorization local; unrelated approved work remains executable."""
    from agenttest.negative import select_negative_approval

    policies = {p.id: p for p in project.registry.definitions()}
    operations = {o.operation_id: o for o in project.contract.operations}
    executable, pending = [], []
    literal_gaps = unassigned_negative_literals(
        project, checks, input_requirements, input_questions)
    for check in checks:
        check = RequestedCheck.model_validate(check)
        policy = policies.get(check.policy_id)
        if policy is None or policy.negative is None:
            raise ValidationFailure(Layer.MODEL, "FOCUS_REQUESTED_CHECK_INVALID")
        if check.policy_id in literal_gaps:
            pending.append(dict(policy_id=policy.id, operation_id=policy.operation_id,
                field=policy.negative.name, requested_value=literal_gaps[check.policy_id],
                reason="NEGATIVE_LITERAL_NOT_PRESERVED"))
            continue
        try:
            if check.exact_value is not None or check.exact_length is not None:
                select_negative_approval(policy.negative, operations[policy.operation_id],
                    requested_invalid_value(check, policy), override=True)
        except ValueError as exc:
            pending.append(dict(policy_id=policy.id, operation_id=policy.operation_id,
                field=policy.negative.name, requested_value=requested_invalid_value(check, policy),
                reason=getattr(getattr(exc, "diagnostic", None), "code",
                               "NEGATIVE_VALUE_NOT_AUTHORIZED")))
        else:
            executable.append(check.model_dump())
    return executable, pending


def unassigned_negative_literals(project, checks, requirements, questions):
    """A default cannot replace an unaccounted quoted value of the approved invalid class."""
    import re

    from agenttest.negative import select_negative_approval

    def strings(value):
        if type(value) is str:
            return {value}
        if type(value) is list:
            return set().union(*(strings(v) for v in value))
        if type(value) is dict:
            return set().union(*(strings(v) for v in value.values()))
        return set()

    used = set()
    for requirement in requirements:
        used.update(strings(literal_data(requirement["value"])))
    for question in questions:
        used.update(question.get("choices", []))
    for check in checks:
        if check.get("exact_value") is not None:
            used.update(strings(literal_data(check["exact_value"]["value"])))
    policies = {p.id: p for p in project.registry.definitions()}
    operations = {o.operation_id: o for o in project.contract.operations}
    gaps = {}
    for raw in checks:
        check = RequestedCheck.model_validate(raw)
        policy = policies.get(check.policy_id)
        if (check.exact_value is not None or check.exact_length is not None
                or policy is None or not policy.negative):
            continue
        candidates = set(re.findall(r'“([^“”\n]{1,300})”|"([^"\n]{1,300})"', check.source_quote))
        for pair in candidates:
            value = next(part for part in pair if part)
            if value in used or value == policy.negative.value:
                continue
            try:
                # This tests the existing deviation class, independently of variation permission.
                select_negative_approval(policy.negative.model_copy(update={
                    "allow_value_variants": True}), operations[policy.operation_id],
                    value, override=True)
            except ValueError:
                continue
            gaps.setdefault(check.policy_id, []).append(value)
    return {key: sorted(values) for key, values in gaps.items()}


def exact_check_shape(entry, original):
    """Keep boundary/enum literals typed on the wire; runtime still verifies every deviation."""
    if entry.get("kind") == "required_missing":
        return dict(type="null")
    constraints = entry.get("field_constraints", {})
    if entry.get("location") == "query" and entry.get("kind") == "type_mismatch":
        return {"anyOf": [dict(type="object", additionalProperties=False,
            properties=dict(value={"anyOf": [dict(type=kind) for kind in
                ("string", "integer", "number", "boolean")]}), required=["value"]),
            dict(type="null")]}
    kind = (constraints.get("type") if entry.get("kind") == "boundary_outside"
            else entry.get("invalid_value_type") if entry.get("kind") == "enum_outside" else None)
    if type(kind) is not str or kind not in {"string", "integer", "number", "boolean", "null"}:
        return original
    wrapper = dict(type="object", additionalProperties=False,
        properties=dict(value=dict(type=kind)), required=["value"])
    return {"anyOf": [wrapper, dict(type="null")]}


def constrain_requested_checks(schema, context):
    array = schema["properties"]["requested_checks"]
    identifiers = [entry["policy_id"] for entry in context.get("approved_checks", [])]
    if identifiers:
        template = schema["$defs"]["RequestedCheck"]
        groups = {}
        for entry in context["approved_checks"]:
            shape = exact_check_shape(entry, template["properties"]["exact_value"])
            length = (entry.get("kind") == "boundary_outside"
                      and entry.get("field_constraints", {}).get("type") == "string")
            group = groups.setdefault(fingerprint([shape, length]),
                dict(shape=shape, length=length, ids=[]))
            group["ids"].append(entry["policy_id"])
        branches = []
        for group in groups.values():
            branch = deepcopy(template)
            branch["properties"]["policy_id"] = dict(type="string", enum=group["ids"])
            branch["properties"]["exact_value"] = deepcopy(group["shape"])
            if not group["length"]:
                branch["properties"].pop("exact_length", None)
            branch["required"] = list(branch["properties"])
            branches.append(branch)
        array["items"] = {"anyOf": branches}
    else:
        array.update(maxItems=0)
        array.pop("items", None)


def negative_prerequisite_policies(context, identifier):
    """Derive preparation and cleanup choices from approved data and login dependencies."""
    policies = {p["id"]: p for p in context["policies"]}
    def sources(identifier):
        policy = policies[identifier]
        baseline = policies.get(policy.get("binding_rules_from_policy"), policy)
        return ({r["source_policy_id"] for r in baseline.get("binding_rules", [])
                 if r.get("source_policy_id") in policies}
                | set(policy.get("prerequisite_policy_ids", [])))
    before, pending = set(), list(sources(identifier))
    while pending:
        source = pending.pop()
        if source in before:
            continue
        before.add(source)
        pending.extend(sources(source))
    current = policies[identifier]
    base = current.get("negative_test", {}).get("base_policy_id")
    methods = {o["id"]: o["method"] for o in context["operations"]}
    if (base in policies
            and current["operation_id"] in context.get("required_normal_operations", [])
            and methods.get(current["operation_id"]) in {"GET", "HEAD", "OPTIONS"}):
        before.add(base)
    roles = {role for p in [identifier, *before] for role in policies[p]["roles"] if role}
    login_operations = {a["login_operation_id"] for a in context.get("authentication", [])
                        if a["role"] in roles and a["login_operation_id"]}
    before.update(p["id"] for p in policies.values()
                  if p["operation_id"] in login_operations and not p.get("negative_test"))
    after = {p["id"] for p in policies.values() if not p.get("negative_test")
             and methods.get(p["operation_id"]) == "DELETE"
             and sources(p["id"]) and sources(p["id"]) <= before}
    return before, after


def reference_repair_view(context, diagnostic, allowed_operations, ready_roles):
    """Project a rejected reference and resolve possible producers in the current batch."""
    from agenttest.credentials import sensitive_field
    from agenttest.model_input import IDENTIFIER, SECRET, safe_detail, safe_fields, safe_selector
    from agenttest.plan_construction import argument_matches
    from agenttest.policies import dependency_roles_match

    def identifier(value):
        return isinstance(value, str) and IDENTIFIER.fullmatch(value) and not SECRET.search(value)

    detail = diagnostic["detail"]
    facts = ("reference", "case_index", "step_id", "obligation_id",
             "allowed_from_steps", "direct_from_steps", "reordered_from_steps")
    rejected, _ = safe_detail({key: detail[key] for key in facts if key in detail},
                              items=12, chars=3000)
    view = dict(code=diagnostic["code"], fields=safe_fields(diagnostic.get("fields")),
                detail=rejected)
    binding = detail.get("rejected_binding") or {}
    if identifier(binding.get("from_step")):
        rejected["from_step"] = binding["from_step"]
    if select := safe_selector(binding.get("select")):
        rejected["select"] = select
    location, name = binding.get("location"), binding.get("name")
    producers = []
    policies = {p["id"]: p for p in context["policies"]}
    consumer = policies.get(rejected.get("obligation_id"))
    role = binding.get("consumer_role")
    if (consumer and consumer["id"] in {row["policy_id"] for row in context["requested_checks"]}
            and "consumer_role" in binding and role in consumer["roles"] and role in ready_roles
            and location in ("body", "path_parameters", "query", "headers")
            and identifier(name) and not sensitive_field(name)):
        rejected["binding"] = dict(location=location, name=name)
        before, _ = negative_prerequisite_policies(context, consumer["id"])
        baseline = policies.get(consumer.get("binding_rules_from_policy"), consumer)
        for rule in baseline.get("binding_rules", []):
            policy_id = rule.get("source_policy_id")
            select = safe_selector(rule.get("source_selector", "$"))
            if policy_id not in before or not select or not argument_matches(
                    rule, dict(location=location, name=name, select=select)):
                continue
            producer = policies.get(policy_id)
            if (not producer or producer.get("negative_test") or not identifier(policy_id)
                    or not identifier(producer["operation_id"])
                    or producer["operation_id"] not in allowed_operations):
                continue
            prerequisites, _ = negative_prerequisite_policies(context, policy_id)
            if {consumer["id"], policy_id} & prerequisites:
                continue
            for source_role in producer["roles"]:
                if (source_role in ready_roles and (source_role is None or identifier(source_role))
                        and dependency_roles_match(source_role, role, rule.get("role_relation"))):
                    candidate = dict(operation_id=producer["operation_id"], policy_id=policy_id,
                                     role=source_role, selector=select)
                    if candidate not in producers:
                        producers.append(candidate)
    view["approved_producers"] = producers[:12]
    view["hint"] = ("allowed_from_steps 仅列出上一份方案在本用例已声明的可用节点；"
                    "引用 reordered_from_steps 中的节点时，构造器会将其排到使用步骤之前。"
                    "若没有可用节点且 approved_producers 有合法生产者，先在同一用例的 before 中"
                    "声明该操作、策略和角色的步骤，再用该步骤 id 作 from_step，"
                    "使用其 selector 作 select。"
                    "批准策略 ID 本身不是步骤 id。不得引用其他用例或已清理资源。"
                    "没有批准生产者时不得虚构节点、引用或资源标识。保留已批准的清理与回读。")
    return view


def prerequisite_steps(schema, identifiers):
    branches = []
    for branch in schema["$defs"]["GenericDraftStep"]["anyOf"]:
        allowed = [p for p in branch["properties"]["policy_id"]["enum"] if p in identifiers]
        if allowed:
            branch = deepcopy(branch)
            branch["properties"]["policy_id"]["enum"] = allowed
            branches.append(branch)
    result = dict(type="array", maxItems=8 if branches else 0)
    if branches:
        result["items"] = {"anyOf": branches}
    return result


def constrain_optional_negative_cases(schema, context):
    """All negative proposals use the existing preparation/check/cleanup wire shape."""
    schema["$defs"]["DesignCase"]["properties"]["kind"]["enum"] = [
        k for k in schema["$defs"]["DesignCase"]["properties"]["kind"]["enum"] if k != "negative"]
    negative = {p["id"] for p in context["policies"] if p.get("negative_test")}
    branches = schema["$defs"]["GenericDraftStep"]["anyOf"]
    groups = {}
    if not context.get("requested_checks"):
        for branch in branches:
            for identifier in branch["properties"]["policy_id"]["enum"]:
                policy = next(p for p in context["policies"] if p["id"] == identifier)
                if identifier not in negative or policy["operation_id"] not in context[
                        "primary_operations"]:
                    continue
                before, after = negative_prerequisite_policies(context, identifier)
                key = fingerprint([branch, sorted(before), sorted(after)])
                group = groups.setdefault(key, dict(branch=branch, before=before,
                                                     after=after, ids=[]))
                group["ids"].append(identifier)
    choices = []
    for group in groups.values():
        check = deepcopy(group["branch"])
        check["properties"]["policy_id"]["enum"] = group["ids"]
        props = dict(title=deepcopy(schema["$defs"]["DesignCase"]["properties"]["title"]),
            before=prerequisite_steps(schema, group["before"]), check=check,
            after=prerequisite_steps(schema, group["after"]))
        choices.append(dict(type="object", additionalProperties=False,
                            properties=props, required=list(props)))
    if choices:
        ordinary = deepcopy(schema["properties"]["cases"]["items"])
        schema["properties"]["cases"]["items"] = {"anyOf": [ordinary, *choices]}
    for branch in branches:
        branch["properties"]["policy_id"]["enum"] = [p for p in branch["properties"][
            "policy_id"]["enum"] if p not in negative]
    branches[:] = [b for b in branches if b["properties"]["policy_id"]["enum"]]


def decode_optional_negative_cases(wire, context, *, construction_chains=None):
    """A category label cannot turn an ordinary request into an executed negative check."""
    cases = wire.get("cases")
    if type(cases) is not list:
        raise ValidationFailure(Layer.MODEL, "DESIGN_NEGATIVE_CASE_OUTSIDE_REQUIRED")
    if context.get("requested_checks"):
        if any(type(case) is dict and "check" in case for case in cases):
            raise ValidationFailure(Layer.MODEL, "DESIGN_NEGATIVE_CASE_OUTSIDE_REQUIRED")
        return  # Explicit checks retain their existing closed-map validation and diagnostics.
    negative = {p["id"] for p in context.get("policies", []) if p.get("negative_test")}
    primary = {p["id"] for p in context.get("policies", []) if p["operation_id"] in context.get(
        "primary_operations", [])}
    for index, case in enumerate(cases):
        if type(case) is dict and "check" in case:
            if (context.get("requested_checks")
                    or set(case) != {"title", "before", "check", "after"}
                    or type(case["before"]) is not list or type(case["after"]) is not list
                    or type(case["check"]) is not dict
                    or case["check"].get("policy_id") not in negative & primary
                    or max(len(case["before"]), len(case["after"])) > 8):
                raise ValidationFailure(Layer.MODEL, "DESIGN_NEGATIVE_CHECK_MISSING")
            cases[index] = dict(title=case["title"], kind="negative", plan=dict(
                name="approved_negative", steps=[*case["before"], case["check"], *case["after"]]))
            if construction_chains is not None:
                construction_chains.append((index,
                    [step.get('id') for step in cases[index]['plan']['steps']]))
        elif type(case) is not dict or case.get("kind") == "negative" or any(
                step.get("policy_id") in negative for step in case.get("plan", {}).get("steps", [])
                if type(step) is dict):
            raise ValidationFailure(Layer.MODEL, "DESIGN_NEGATIVE_CASE_OUTSIDE_REQUIRED")


def constrain_required_cases(schema, context):
    """Every requested capability gets a mandatory case and an exactly typed check step."""
    requested = context.get("requested_checks", [])
    if not requested:
        return
    if len(requested) > 12:
        raise ValidationFailure(Layer.MODEL, "DESIGN_CHECK_BATCH_LIMIT")
    schema["properties"]["cases"]["maxItems"] = (
        0 if context.get("only_required_checks") else 12 - len(requested))
    cases = {}
    for requirement in requested:
        identifier = requirement["policy_id"]
        branch = next((step for step in schema["$defs"]["GenericDraftStep"]["anyOf"]
                       if identifier in step["properties"]["policy_id"]["enum"]), None)
        if branch is None:
            raise ValidationFailure(Layer.MODEL, 'DESIGN_REQUIRED_IDENTITY_NOT_APPROVED')
        check = deepcopy(branch)
        check["properties"]["policy_id"] = dict(type="string", enum=[identifier])
        before, after = negative_prerequisite_policies(context, identifier)
        props = dict(title=deepcopy(schema["$defs"]["DesignCase"]["properties"]["title"]),
                     before=prerequisite_steps(schema, before), check=check,
                     after=prerequisite_steps(schema, after))
        cases[identifier] = dict(type="object", additionalProperties=False,
                                properties=props, required=list(props))
    schema["properties"]["required_cases"] = dict(type="object", additionalProperties=False,
        properties=cases, required=list(cases))
    schema["required"] = [*schema["required"], "required_cases"]
    # Explicit negatives live only in required_cases; ordinary/setup steps use valid inputs.
    negatives = {p["id"] for p in context["policies"] if p.get("negative_test")}
    branches = schema["$defs"]["GenericDraftStep"]["anyOf"]
    for branch in branches:
        ids = branch["properties"]["policy_id"]["enum"]
        branch["properties"]["policy_id"]["enum"] = [key for key in ids if key not in negatives]
    branches[:] = [branch for branch in branches if branch["properties"]["policy_id"]["enum"]]


def decode_required_cases(wire, context, *, construction_chains=None):
    requested = context.get("requested_checks", [])
    if not requested:
        return
    required = wire.pop("required_cases", None)
    expected = {requirement["policy_id"] for requirement in requested}
    if (type(required) is not dict or set(required) != expected
            or type(wire.get("cases")) is not list):
        raise ValidationFailure(Layer.MODEL, "DESIGN_REQUESTED_CHECKS_MISSING")
    if any(case.get("kind") == "negative" for case in wire["cases"] if type(case) is dict):
        raise ValidationFailure(Layer.MODEL, "DESIGN_NEGATIVE_CASE_OUTSIDE_REQUIRED")
    if context.get("only_required_checks") and wire["cases"]:
        raise ValidationFailure(Layer.MODEL, "DESIGN_NEGATIVE_CASE_OUTSIDE_REQUIRED")
    outside = [step for case in wire["cases"] for step in case.get("plan", {}).get("steps", [])]
    for case in required.values():
        if type(case) is dict:
            for key in ("before", "after"):
                if type(case.get(key)) is list:
                    outside.extend(case[key])
    if any(type(step) is dict and step.get("policy_id") in expected for step in outside):
        raise ValidationFailure(Layer.MODEL, "DESIGN_REQUESTED_CHECK_DUPLICATE")
    negatives = {p["id"] for p in context["policies"] if p.get("negative_test")}
    if any(type(step) is dict and step.get("policy_id") in negatives for step in outside):
        raise ValidationFailure(Layer.MODEL, "DESIGN_NEGATIVE_CHECK_NOT_REQUESTED")
    for identifier, case in required.items():
        if (type(case) is not dict or set(case) != {"title", "before", "check", "after"}
                or type(case["before"]) is not list or type(case["after"]) is not list
                or type(case["check"]) is not dict
                or case["check"].get("policy_id") != identifier
                or len(case["before"]) > 8 or len(case["after"]) > 8):
            raise ValidationFailure(Layer.MODEL, "DESIGN_REQUESTED_CHECKS_MISSING")
        wire["cases"].append(dict(title=case["title"], kind="negative", plan=dict(
            name="requested_check", steps=[*case["before"], case["check"], *case["after"]])))
        if construction_chains is not None:
            construction_chains.append((len(wire['cases']) - 1,
                [step.get('id') for step in wire['cases'][-1]['plan']['steps']]))


def validate_check_coverage(design, requested):
    actual = {step.policy_id for case in design.cases for step in case.plan.steps}
    if {item["policy_id"] for item in requested} - actual:
        raise ValidationFailure(Layer.MODEL, "DESIGN_REQUESTED_CHECKS_MISSING")


def executed_policies(report):
    policies = {step.step_id: step.policy_id for step in report.plan_structure}
    return list(dict.fromkeys(policies[step.step_id] for step in report.steps
                             if step.attempts and step.step_id in policies))
