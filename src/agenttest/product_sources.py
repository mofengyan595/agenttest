"""Closed references to exact operator text; the model does not retype evidence quotations."""

import re
from copy import deepcopy

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.json_values import json_equal
from agenttest.requirement_ledger import explicit_assignments


def compact_sources(context):
    """Deduplicate evidence in the model view; retain the original validation context."""
    sources = {}

    result = deepcopy(context)
    for policy in result.get("policies", []):
        # Absent optional bindings and polling are the same as their empty defaults.
        # Keep the trusted context intact for all validators and execution checks.
        if policy.get("required_bindings") == {}:
            policy.pop("required_bindings")
        if policy.get("poll_allowed") is False:
            policy.pop("poll_allowed")
    for owner in (result, result.get("previous_result") or {}):
        for key in ("input_requirements", "requested_checks"):
            entries = []
            for entry in owner.get(key, []):
                entry = dict(entry)
                quote = entry.pop("source_quote")
                reference = sources.setdefault(quote, f"source_{len(sources) + 1}")
                entry["source_ref"] = reference
                entries.append(entry)
            if key in owner:
                owner[key] = entries
    result["source_texts"] = {reference: quote for quote, reference in sources.items()}
    rules = {}
    for rule in result.get("confirmed_rules", []):
        if type(rule.get("source")) is str:
            source = rule.pop("source")
            rule["source_ref"] = rules.setdefault(source, f"rule_{len(rules) + 1}")
    if rules:
        result["rule_sources"] = {reference: source for source, reference in rules.items()}
    return result


def input_sources(context):
    result, seen = [], set()

    def add(text, origin):
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            result.append(dict(source_id=f"source_{len(result) + 1}", text=text, origin=origin))

    current = context["user_task"]
    for start in ([0] if len(current) <= 300 else range(0, len(current), 150)):
        add(current[start:start + 300], "current")
    for atom in context.get('request_atoms', []):
        add(atom['text'], 'current_clause')
    for key in ("previous_input_requirements", "previous_requested_checks",
                "previous_expectation_questions"):
        for item in context.get(key, []):
            add(item["source_quote"], "previous_validated")
    for text in context.get("previous_requests", []):
        for start in ([0] if len(text) <= 300 else range(0, len(text), 150)):
            add(text[start:start + 300], "previous_request")
    return result


def literal_texts(sources):
    """Offer exact quoted strings as closed references before model generation."""
    result = {}
    pattern = r'"([^"\n]{1,300})"|“([^”\n]{1,300})”|「([^」\n]{1,300})」|`([^`\n]{1,300})`'
    for source in sources:
        for match in re.finditer(pattern, source["text"]):
            value = next(group for group in match.groups() if group is not None)
            if value not in result.values():
                result[f"text_{len(result) + 1}"] = value
    return result


def constrain_source_refs(schema, sources):
    identifiers = [entry["source_id"] for entry in sources]

    def visit(node):
        if isinstance(node, dict):
            properties = node.get("properties", {})
            if "source_quote" in properties:
                properties.pop("source_quote")
                properties["source_ref"] = dict(type="string", enum=identifiers)
                node["required"] = ["source_ref" if k == "source_quote" else k
                                    for k in node["required"]]
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(schema)


def constrain_input_sources(properties, context, operation, fields):
    """Reuse old operator data, keeping answered or ambiguous fields bound to their own proof."""
    from agenttest.llm_planner import literal_data
    from agenttest.product_inputs import InputRequirement, input_slot
    from agenttest.request_validation import validate_value

    sources = input_sources(context)
    current = [s["source_id"] for s in sources if s["origin"] == "current"]
    confirmed_current = list(current)
    if 'request_atoms' in context:
        current_texts = {a['text'] for a in context['request_atoms'] if not a.get('future_only')}
        confirmed_current = [s['source_id'] for s in sources if s['text'] in current_texts]
    by_ref = {input_slot(f).split("/", 1)[1]: f for f in fields}
    def cited(branch, identifiers):
        branch = deepcopy(branch)
        branch["properties"].pop("source_quote")
        branch["properties"]["source_ref"] = dict(type="string", enum=identifiers)
        branch["required"] = list(branch["properties"])
        return branch
    for key in ("input_requirements", "input_questions"):
        array = properties[key]
        branches = []
        for branch in array.get("items", {}).get("anyOf", []):
            reference = branch["properties"]["field_ref"]["enum"][0]
            field_schema = by_ref[reference]["schema"]
            source_ids = confirmed_current if key == 'input_requirements' else current
            for source in (s for s in sources if s["source_id"] in source_ids):
                direct = cited(branch, [source["source_id"]])
                assignments = explicit_assignments(source["text"])
                named = [a for a in assignments if a["name"] == (
                    by_ref[reference]["path"] or [by_ref[reference]["name"]])[-1]]
                if (key == "input_requirements"
                        and field_schema.get("type") in {"integer", "number"}
                        and (numbers := re.findall(r"-?\d+(?:\.\d+)?", source["text"]))):
                    values = []
                    tokens = [a["value"] for a in named if not a["deferred"]] if named else [
                        float(token) if "." in token else int(token)
                        for token in dict.fromkeys(numbers)]
                    for value in tokens:
                        try:
                            validate_value(value, field_schema)
                        except ValueError:
                            continue
                        # Match the existing post-generation provenance check exactly.
                        if str(value) in numbers:
                            values.append(value)
                    if not values:
                        continue
                    direct["properties"]["value"] = dict(enum=values)
                if (key == "input_requirements"
                        and by_ref[reference]["schema"].get("type") == "string"):
                    choices = []
                    for identifier, value in literal_texts(sources).items():
                        try:
                            validate_value(value, by_ref[reference]["schema"])
                        except ValueError:
                            continue
                        choices.append(identifier)
                    if choices:
                        direct["properties"]["value"] = {"anyOf": [
                            direct["properties"]["value"], dict(type="object",
                                properties=dict(text_ref=dict(type="string", enum=choices)),
                                required=["text_ref"], additionalProperties=False)]}
                branches.append(direct)
            if key != "input_requirements":
                continue
            field = by_ref[reference]
            prior = [p for p in context.get("previous_input_requirements", [])
                     if p["operation_id"] == operation
                     and input_slot(p).split("/", 1)[1] == reference]
            waiting = any(p["operation_id"] == operation
                and input_slot(p).split("/", 1)[1] == reference
                for p in context.get("pending_input_questions", []))
            if not prior and not waiting:
                previous = [s["source_id"] for s in sources
                    if s["origin"] != "current" and any(s["text"] in old
                        for old in context.get("previous_requests", []))]
                if previous:
                    branches.append(cited(branch, previous))
            for previous in context.get("previous_input_requirements", []):
                if (previous["operation_id"] != operation
                        or input_slot(previous).split("/", 1)[1] != reference):
                    continue
                value = InputRequirement.model_validate(previous).value
                try:
                    validate_value(literal_data(value), field["schema"])
                except ValueError:
                    continue
                identifiers = [s["source_id"] for s in sources
                    if s["origin"] == "previous_validated"
                    and s["text"] == previous["source_quote"].strip()]
                if identifiers:
                    old = cited(branch, identifiers)
                    old["properties"]["value"] = dict(enum=[previous["value"]])
                    branches.append(old)
        if branches:
            array["items"] = {"anyOf": branches}
        else:
            array.update(maxItems=0)
            array.pop("items", None)


def numeric_boundary_reference(check, quote):
    """Identify a named numeric source and the single value matching this boundary side."""
    from agenttest.negative import boundary_side

    schema = check.get("field_constraints", {})
    default = check.get("invalid_value")
    if check.get("kind") != "boundary_outside" or type(default) not in {int, float}:
        return 0, None
    approved_side = boundary_side(default, schema)
    if not approved_side:
        return 0, None
    matches = [a for a in explicit_assignments(quote) if a["name"] == check["name"]]
    if len(matches) == 1 and not matches[0]["deferred"]:
        value = matches[0]["value"]
        if type(value) is int or type(value) is float and schema.get("type") == "number":
            if boundary_side(value, schema) == approved_side:
                return 1, value
    return len(matches), None


def constrain_numeric_check_sources(array, checks, context):
    """Use the existing source reference as the numeric value reference in the model wire."""
    if not context.get("request_atoms"):
        return
    sources = input_sources(context)
    atoms = {a["text"] for a in context.get("request_atoms", [])
             if not a["future_only"] and not a["constraint"]}
    branches = []
    for branch in array.get("items", {}).get("anyOf", []):
        unchanged = []
        for ref in branch["properties"]["check_ref"]["enum"]:
            choices = [(s, *numeric_boundary_reference(checks[ref], s["text"])) for s in sources]
            if not any(count and s['origin'].startswith('current') for s, count, _ in choices):
                unchanged.append(ref)
                continue
            for identifiers, exact in (
                ([s["source_id"] for s, _, value in choices if value is not None
                  and s["text"] in atoms], dict(type="null")),
                ([s["source_id"] for s, count, _ in choices if count != 1
                  or s['origin'].startswith('previous')],
                 branch["properties"]["exact_value"]),
            ):
                if identifiers:
                    linked = deepcopy(branch)
                    props = linked["properties"]
                    props.pop("source_quote")
                    props.update(check_ref=dict(type="string", enum=[ref]),
                                 source_ref=dict(type="string", enum=identifiers),
                                 exact_value=exact)
                    linked["required"] = list(props)
                    branches.append(linked)
        if unchanged:
            branch["properties"]["check_ref"]["enum"] = unchanged
            branches.append(branch)
    if branches:
        array["items"] = {"anyOf": branches}
    elif "items" in array:
        array.pop("items")
        array["maxItems"] = 0


def decode_source_refs(wire, sources, context=None):
    index = {entry["source_id"]: entry["text"] for entry in sources}
    texts = literal_texts(sources)
    checks = ({c["policy_id"]: c for c in context.get("approved_checks", [])}
              if context and context.get("request_atoms") else {})
    for key in ("input_requirements", "input_questions", "requested_checks",
                "expectation_questions"):
        entries = wire.get(key, [])
        if type(entries) is not list:
            raise ValidationFailure(Layer.MODEL, "FOCUS_SOURCE_REFERENCE_INVALID")
        for entry in entries:
            if (type(entry) is not dict or "source_quote" in entry
                    or type(entry.get("source_ref")) is not str
                    or entry["source_ref"] not in index):
                raise ValidationFailure(Layer.MODEL, "FOCUS_SOURCE_REFERENCE_INVALID")
            entry["source_quote"] = index[entry.pop("source_ref")]
            if key == "requested_checks" and (check := checks.get(entry.get("policy_id"))):
                named, number = numeric_boundary_reference(check, entry["source_quote"])
                if named and entry.get("exact_value") is None:
                    if number is None or (context.get("request_atoms") and not any(
                            a["text"] == entry["source_quote"] and not a["future_only"]
                            and not a["constraint"] for a in context["request_atoms"])):
                        raise ValidationFailure(Layer.MODEL, "FOCUS_NEGATIVE_SOURCE_INVALID")
                    entry["exact_value"] = dict(value=number)
                elif number is not None and isinstance(entry.get("exact_value"), dict):
                    value = entry["exact_value"].get("value")
                    if not json_equal(value, number) and value != str(number):
                        raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_NUMBER_CHANGED")
            value = entry.get("value")
            if key == "input_requirements" and type(value) is dict and "text_ref" in value:
                reference = value.get("text_ref")
                if (set(value) != {"text_ref"} or type(reference) is not str
                        or reference not in texts
                        or texts[reference] not in entry["source_quote"]):
                    raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_SOURCE_INVALID")
                entry["value"] = texts[reference]
