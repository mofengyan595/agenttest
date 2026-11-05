"""Source-backed input requirements, constrained independently of model test design."""

import re
from copy import deepcopy
from typing import Literal

from pydantic import Field

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.llm_planner import LiteralValue, literal_data
from agenttest.planner_input import public_schema
from agenttest.report import fingerprint
from agenttest.request_validation import request_schemas, validate_value
from agenttest.safety import StrictModel


class InputRequirement(StrictModel):
    operation_id: str
    location: Literal["body", "query", "path_parameters"]
    name: str
    path: list[str] = Field(max_length=10)
    value: LiteralValue
    source_quote: str = Field(min_length=1, max_length=300)


class InputQuestion(StrictModel):
    operation_id: str
    location: Literal["body", "query", "path_parameters"]
    name: str
    path: list[str] = Field(max_length=10)
    source_quote: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=4, max_length=300)
    choices: list[str] = Field(min_length=2, max_length=2)


def validate_input_questions(questions, requirements, context, selected):
    """Ambiguity alternatives must be distinct, source-backed approved string values."""
    seen = set()
    slots = {(r.operation_id, r.location, r.name, tuple(r.path)) for r in requirements}
    for question in questions:
        key = (question.operation_id, question.location, question.name, tuple(question.path))
        if key in seen or key in slots or len(set(question.choices)) != 2:
            raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_QUESTION_CONFLICT")
        seen.add(key)
        for choice in question.choices:
            validate_requirements([InputRequirement(**question.model_dump(
                exclude={"question", "choices"}), value=choice)], context, selected,
                allow_ambiguity_source=True)


def input_blocked_operations(project, questions, *, operation_ids=()):
    """Hold the uncertain operation and its dependency consumers, leaving others runnable."""
    blocked = {q.operation_id for q in questions} | set(operation_ids)
    policies = {p.id: p for p in project.registry.definitions()}
    changed = True
    while changed:
        before = len(blocked)
        for policy in policies.values():
            if (any(r.source_policy_id in policies and
                   policies[r.source_policy_id].operation_id in blocked
                   for r in policy.binding_rules) or any(
                       project.target.roles[role].login_operation in blocked
                       for role in policy.roles if role in project.target.roles)):
                blocked.add(policy.operation_id)
        changed = len(blocked) != before
    return sorted(blocked)


def unresolved_input_questions(previous, current, requirements, request, relevant_operations):
    """Persist an unanswered choice even if a later model omits it; never choose a default."""
    def slot(value):
        return (value.operation_id, value.location, value.name, tuple(value.path))
    questions = {slot(q): q for q in current}
    answered = {slot(r) for r in requirements if r.source_quote in request}
    for raw in previous:
        question = InputQuestion.model_validate(raw)
        key = slot(question)
        if (question.operation_id in relevant_operations and key not in questions
                and key not in answered):
            questions[key] = question
    return list(questions.values())


def input_fields(project):
    """Index approved fields without credentials or runtime resource values."""
    fields = []
    seen = set()
    operations = {o.operation_id: o for o in project.contract.operations}

    def leaves(schema, path=()):
        if schema.get("type") == "object":
            for name, child in schema.get("properties", {}).items():
                if not child.get("readOnly"):
                    yield from leaves(child, (*path, name))
        else:
            yield list(path), public_schema(schema)

    for policy in project.registry.definitions():
        if policy.negative or policy.operation_id not in project.target.allowed_operations:
            continue
        for rule in policy.binding_rules:
            source = rule.literal_schema
            if source is None:
                operation = operations[policy.operation_id]
                parameters, body = request_schemas(operation,
                    "application/json" if rule.location == "body" and operation.request_body
                    else None)
                source = ((body or {}).get("properties", {}).get(rule.name)
                    if rule.location == "body" else parameters[
                        "path" if rule.location == "path_parameters" else "query"].get(rule.name))
            if source is None:
                continue
            for path, schema in leaves(source):
                key = (policy.operation_id, rule.location, rule.name, tuple(path))
                if key not in seen:
                    seen.add(key)
                    fields.append(dict(operation_id=key[0], location=key[1], name=key[2],
                                       path=path, schema=schema,
                                       source_dependency=rule.source_policy_id is not None))
    return fields


def validate_requirements(requirements, context, selected, *, allow_ambiguity_source=False,
                          requested_checks=(), clauses=()):
    """Validate quoted provenance, approved slots and values, without granting authority."""
    request = context["user_task"]
    fields = context.get("input_fields", [])
    normal = []
    for index, requirement in enumerate(requirements):
        current = requirement.model_dump()
        known_previous = any(
            all(json_equal(previous.get(k), current[k])
                for k in ("operation_id", "location", "name", "path", "value"))
            and requirement.source_quote in previous.get("source_quote", "")
            for previous in context.get("previous_input_requirements", []))
        if ('request_atoms' in context and not allow_ambiguity_source and not known_previous
                and requirement.source_quote in request and requirement.source_quote not in {
                    a['text'] for a in context['request_atoms'] if not a.get('future_only')}):
            raise ValidationFailure(Layer.MODEL, 'FOCUS_INPUT_SOURCE_NOT_CURRENT')
        if requirement.operation_id not in selected:
            raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_OPERATION_NOT_SELECTED")
        if requirement.source_quote not in request and not known_previous:
            slot_keys = ("operation_id", "location", "name", "path")
            def same_slot(item):
                return all(item.get(k) == current[k] for k in slot_keys)
            if (not any(requirement.source_quote in old for old in
                        context.get("previous_requests", []))
                    or any(same_slot(p) for p in context.get("previous_input_requirements", []))
                    or any(same_slot(p) for p in context.get("pending_input_questions", []))):
                error = ValidationFailure(Layer.MODEL, "FOCUS_INPUT_SOURCE_INVALID")
                error.diagnostic.fields = [f"input_requirements.{index}.source_quote"]
                raise error
        field = next((f for f in fields if all(getattr(requirement, k) == f[k]
                     for k in ("operation_id", "location", "name", "path"))), None)
        if field is None:
            raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_SLOT_INVALID")
        value = literal_data(requirement.value)
        duplicate = False
        try:
            validate_value(value, field["schema"])
        except ValidationFailure as exc:
            from agenttest.product_checks import duplicates_boundary_check

            boundary_codes = {"OPENAPI_VALUE_BOUND", "OPENAPI_VALUE_EXCLUSIVE_BOUND",
                              "OPENAPI_VALUE_FORMAT_BOUND", "OPENAPI_VALUE_LENGTH"}
            duplicate = (exc.diagnostic.code in boundary_codes and duplicates_boundary_check(
                requirement, value, requested_checks, context, clauses))
            if not duplicate:
                exc.diagnostic.fields = [f"input_requirements.{index}.value",
                                         f"approved_input_fields.{fields.index(field)}"]
                raise
        try:
            _check_quote(value, field["schema"], requirement.source_quote)
            from agenttest.requirement_ledger import explicit_assignments

            named = {a['name'] for a in explicit_assignments(requirement.source_quote)
                     if json_equal(a['value'], value) and not a['deferred']}
            name = requirement.path[-1] if requirement.path else requirement.name
            if named and name not in named:
                raise ValidationFailure(Layer.MODEL, 'FOCUS_INPUT_FIELD_CHANGED')
        except ValidationFailure as exc:
            exc.diagnostic.fields = [f"input_requirements.{index}.value",
                                     f"approved_input_fields.{fields.index(field)}"]
            raise
        # Repeated evidence is retained, but never becomes another execution step.
        if not duplicate:
            normal.append(requirement)
    return normal


def input_slot(field):
    parts = [field["operation_id"], field["location"], field["name"], *field["path"]]
    return "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def decode_input_slots(wire, fields):
    """Resolve one closed reference, never repair free-form field names or values."""
    slots = {input_slot(field): field for field in fields}
    for key, supplied in (("input_requirements", {"value", "source_quote"}),
                          ("input_questions", {"question", "choices", "source_quote"})):
        entries = wire[key]
        if type(entries) is not list:
            raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_SLOT_INVALID")
        resolved = []
        for entry in entries:
            if type(entry) is not dict or set(entry) != {"slot_id", *supplied}:
                raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_SLOT_INVALID")
            field = slots.get(entry["slot_id"]) if type(entry["slot_id"]) is str else None
            if field is None:
                raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_SLOT_INVALID")
            resolved.append({**{k: field[k] for k in (
                "operation_id", "location", "name", "path")},
                **{k: entry[k] for k in supplied}})
        wire[key] = resolved


def focus_input_groups(fields):
    """Share identical input leaves across the index, without dropping operations."""
    groups = {}
    for field in fields:
        shared = {k: v for k, v in field.items() if k != "operation_id"}
        group = groups.setdefault(fingerprint(shared), dict(**shared, slots=[]))
        group["slots"].append(dict(slot_id=input_slot(field), operation_id=field["operation_id"]))
    return list(groups.values())


def constrain_focus_inputs(schema, groups):
    """Intersect extracted values with approved field types before generation."""
    from agenttest.product_schema import literal_schema

    branches = []
    for group in groups:
        branch = deepcopy(schema["$defs"]["InputRequirement"])
        fields = branch["properties"]
        for key in ("operation_id", "location", "name", "path"):
            fields.pop(key)
        fields["slot_id"] = {"type": "string", "enum": [s["slot_id"] for s in group["slots"]]}
        fields["value"] = literal_schema(group["schema"])
        branch["required"] = list(fields)
        branches.append(branch)
    array = schema["properties"]["input_requirements"]
    if branches:
        array["items"] = {"anyOf": branches}
    else:
        array["maxItems"] = 0
        array.pop("items", None)
    questions = []
    for group in groups:
        if (group["schema"].get("type") != "string"
                or "enum" in group["schema"] and len(group["schema"]["enum"]) < 2):
            continue
        branch = deepcopy(schema["$defs"]["InputQuestion"])
        props = branch["properties"]
        for key in ("operation_id", "location", "name", "path"):
            props.pop(key)
        props["slot_id"] = {"type": "string", "enum": [s["slot_id"] for s in group["slots"]]}
        props["choices"]["items"] = literal_schema(group["schema"])
        branch["required"] = list(props)
        questions.append(branch)
    array = schema["properties"]["input_questions"]
    if questions:
        array["items"] = {"anyOf": questions}
    else:
        array.update(maxItems=0)
        array.pop("items", None)


def _check_quote(value, schema, quote):
    # Enum, boolean and written-number translations remain model interpretations;
    # free text and explicit decimal tokens retain their exact source values.
    if type(value) is str and "enum" not in schema and value not in quote:
        raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_TEXT_CHANGED")
    if type(value) in {int, float}:
        numbers = re.findall(r"-?\d+(?:\.\d+)?", quote)
        if numbers and str(value) not in numbers:
            raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_NUMBER_CHANGED")
    if isinstance(value, list):
        for item in value:
            _check_quote(item, schema["items"], quote)
    if isinstance(value, dict):
        for name, item in value.items():
            _check_quote(item, schema["properties"][name], quote)


def input_requirement_roles(requirement, source_requirements):
    """Bind a value to actors from its exact source clause; never infer actors from plan order."""
    clauses = [r.model_dump() if hasattr(r, 'model_dump') else r for r in source_requirements]
    candidates = [r for r in clauses if (r['source_quote'] in requirement.source_quote or
        requirement.source_quote in r['source_quote']) and any(
            a['operation_id'] == requirement.operation_id for a in r['actions'])]
    exact = [r for r in candidates if r['source_quote'] == requirement.source_quote]
    if exact:
        candidates = exact
    else:
        value = literal_data(requirement.value)

        def quoted(current, quote):
            if isinstance(current, dict):
                return all(quoted(v, quote) for v in current.values())
            if isinstance(current, list):
                return all(quoted(v, quote) for v in current)
            if type(current) is str:
                return current in quote
            if type(current) in {int, float}:
                return str(current) in re.findall(r'-?\d+(?:\.\d+)?', quote)
            return False

        anchored = []
        for clause in candidates:
            if quoted(value, clause['source_quote']):
                anchored.append(clause)
        if anchored:
            candidates = anchored
        elif len({a['role'] for r in candidates for a in r['actions']
                  if a['operation_id'] == requirement.operation_id}) > 1:
            raise ValidationFailure(Layer.MODEL, 'FOCUS_INPUT_IDENTITY_AMBIGUOUS')
    roles = {a['role'] for r in candidates for a in r['actions']
             if a['operation_id'] == requirement.operation_id}
    return roles or None


def constrain_input_schema(schema, operation, rule, requirements, *, roles=None,
                           source_requirements=()):
    """Restrict literals before generation; do not rewrite returned arguments."""
    result = deepcopy(schema)
    by_path = {}
    for requirement in requirements:
        scope = input_requirement_roles(requirement, source_requirements)
        if roles is not None and scope is not None and not set(roles) & scope:
            continue
        if (requirement.operation_id, requirement.location, requirement.name) == (
                operation, rule["location"], rule["name"]):
            by_path.setdefault(tuple(requirement.path), []).append(literal_data(requirement.value))
    for path, values in by_path.items():
        node = result
        for name in path:
            node = node["properties"][name]
        for value in values:
            validate_value(value, node)
        node["enum"] = values
        # Requiring a nested leaf also requires its ancestors in generated input.
        parent = result
        for name in path:
            parent["required"] = list(dict.fromkeys([*parent.get("required", []), name]))
            parent = parent["properties"][name]
    return result


def validate_design_inputs(project, design, requirements, *, normal_operation_ids=(),
                           source_requirements=()):
    """Preserve requested values across every baseline; negatives remain executor-owned."""
    policies = {p.id: p for p in project.registry.definitions()}
    steps = [s for c in design.cases for s in c.plan.scenario().steps
             if s.policy_id in policies]
    alternatives = {}
    for requirement in requirements:
        key = (requirement.operation_id, requirement.location,
               requirement.name, tuple(requirement.path))
        alternatives.setdefault(key, []).append((literal_data(requirement.value),
            input_requirement_roles(requirement, source_requirements)))
    for step in steps:
        for (operation, location, name, path), candidates in alternatives.items():
            if step.operation_id != operation:
                continue
            allowed = [value for value, scope in candidates if scope is None or step.role in scope]
            if not allowed:
                continue
            binding = getattr(step, location).get(name)
            if binding is not None and binding.from_step is not None:
                raise ValueError("DESIGN_EXPLICIT_INPUT_REQUIRES_SOURCE_REVIEW")
            if binding is None:
                raise ValueError("DESIGN_EXPLICIT_INPUT_NOT_PRESERVED")
            value = binding.value
            try:
                for field in path:
                    value = value[field]
            except (KeyError, TypeError, IndexError):
                raise ValueError("DESIGN_EXPLICIT_INPUT_NOT_PRESERVED") from None
            if not any(json_equal(value, candidate) for candidate in allowed):
                raise ValueError("DESIGN_EXPLICIT_INPUT_NOT_PRESERVED")
    for requirement in requirements:
        wanted = literal_data(requirement.value)
        scope = input_requirement_roles(requirement, source_requirements)
        observed = False
        for step in steps:
            if step.operation_id != requirement.operation_id:
                continue
            if scope is not None and step.role not in scope:
                continue
            if (requirement.operation_id in normal_operation_ids
                    and policies[step.policy_id].negative):
                continue
            binding = getattr(step, requirement.location).get(requirement.name)
            if binding is None or binding.from_step is not None:
                continue
            value = binding.value
            try:
                for name in requirement.path:
                    value = value[name]
            except (KeyError, TypeError, IndexError):
                continue
            if json_equal(value, wanted):
                observed = True
                break
        if not observed:
            raise ValueError("DESIGN_EXPLICIT_INPUT_NOT_PRESERVED")
