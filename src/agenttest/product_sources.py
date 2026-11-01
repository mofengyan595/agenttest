"""Closed references to exact operator text; the model does not retype evidence quotations."""

from copy import deepcopy

from agenttest.diagnostics import Layer, ValidationFailure


def compact_sources(context):
    """Deduplicate evidence in the model view; retain the original validation context."""
    sources = {}

    result = deepcopy(context)
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
    for key in ("previous_input_requirements", "previous_requested_checks",
                "previous_expectation_questions"):
        for item in context.get(key, []):
            add(item["source_quote"], "previous_validated")
    for text in context.get("previous_requests", []):
        for start in ([0] if len(text) <= 300 else range(0, len(text), 150)):
            add(text[start:start + 300], "previous_request")
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
            if current:
                branches.append(cited(branch, current))
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


def decode_source_refs(wire, sources):
    index = {entry["source_id"]: entry["text"] for entry in sources}
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
