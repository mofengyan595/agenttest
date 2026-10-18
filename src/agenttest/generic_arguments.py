"""Contract-derived planning slots; these restrictions never authorize execution."""

from copy import deepcopy
from typing import TYPE_CHECKING

from agenttest.diagnostics import Layer, ValidationFailure

if TYPE_CHECKING:
    from agenttest.llm_planner import GenericPlannerDraft


def argument_slots(context: dict) -> dict[str, tuple[tuple[str, str], ...]]:
    """Keep operation and location together; executor-owned login has no draft inputs."""
    result = {}
    for operation in context["operations"]:
        slots = set()
        if not operation["authentication_executor_managed"]:
            for parameter in operation["parameters"]:
                location = {"path": "path_parameters", "query": "query"}.get(parameter["in"])
                if location is not None:
                    slots.add((location, parameter["name"]))
            for schema in (operation["request_body"] or {}).get("content", {}).values():
                if schema.get("type") == "object":
                    slots.update(("body", name) for name in schema.get("properties", {}))
        result[operation["id"]] = tuple(sorted(slots))
    return result


def slot_types(context: dict) -> dict[str, dict[tuple[str, str], str | None]]:
    """Restrict literal kinds only when all declared schemas agree; refs still use null."""
    result = {}
    for operation in context["operations"]:
        candidates: dict[tuple[str, str], set[str | None]] = {}
        for parameter in operation["parameters"]:
            location = {"path": "path_parameters", "query": "query"}.get(parameter["in"])
            if location:
                kind = parameter.get("schema", {}).get("type")
                candidates[(location, parameter["name"])] = {
                    kind if isinstance(kind, str) else None}
        for schema in (operation["request_body"] or {}).get("content", {}).values():
            for name, field in schema.get("properties", {}).items():
                kind = field.get("type")
                candidates.setdefault(("body", name), set()).add(
                    kind if isinstance(kind, str) else None)
        result[operation["id"]] = {key: next(iter(kinds)) if len(kinds) == 1 else None
                                    for key, kinds in candidates.items()}
    return result


def constrain_generic_arguments(schema: dict, step_type: str, context: dict,
                                argument: dict) -> None:
    """Intersect the existing source schema with operation-specific named slots.

    Whole-object branches retain strict structured-output shapes. Identical slot
    sets share one branch, without discarding candidates to fit a token budget.
    Nested LiteralMember keys remain data, rather than top-level argument names.
    """
    types = slot_types(context)
    groups: dict[tuple[tuple[str, str, str | None], ...], list[str]] = {}
    for operation, slots in argument_slots(context).items():
        signature = tuple((loc, name, types[operation].get((loc, name))) for loc, name in slots)
        groups.setdefault(signature, []).append(operation)
    step = schema["$defs"][step_type]
    if not groups:
        # Explicit abstention remains possible even with an empty visible catalog.
        schema["properties"]["steps"]["maxItems"] = 0
        for field in ("operation_id", "policy_id"):
            if not step["properties"][field].get("enum"):
                step["properties"][field].pop("enum", None)
        return
    shared_step = deepcopy(step)
    for index, field in enumerate(("id", "role", "policy_id")):
        name = ("I", "R", "P")[index]
        schema["$defs"][name] = shared_step["properties"][field]
        shared_step["properties"][field] = {"$ref": "#/$defs/" + name}
    shared_argument = deepcopy(argument)
    for index, field in enumerate(("value", "from_step", "select")):
        name = "V" if index == 0 else "S"
        schema["$defs"].setdefault(name, shared_argument["properties"][field])
        shared_argument["properties"][field] = {"$ref": "#/$defs/" + name}
    branches, slot_definitions, value_definitions = [], {}, {}
    for slots, operations in groups.items():
        branch = deepcopy(shared_step)
        properties = branch["properties"]
        properties["operation_id"] = {"type": "string", "enum": operations}
        arguments = deepcopy(properties["arguments"])
        arguments.pop("maxItems", None)  # The shared step still enforces the limit.
        if not slots:
            arguments["maxItems"] = 0
        else:
            choices = []
            for location in ("body", "path_parameters", "query"):
                by_type: dict[str | None, list[str]] = {}
                for loc, name, kind in slots:
                    if loc == location:
                        by_type.setdefault(kind, []).append(name)
                for kind, names in by_type.items():
                    choice = deepcopy(shared_argument)
                    choice["properties"]["location"] = {"type": "string", "enum": [location]}
                    choice["properties"]["name"] = {"type": "string", "enum": names}
                    supported = {"string", "integer", "number", "boolean", "null",
                                 "object", "array"}
                    if kind in supported:
                        if kind not in value_definitions:
                            definition = f"T{len(value_definitions)}"
                            value_definitions[kind] = definition
                            literal = ({"$ref": "#/$defs/Literal" + kind.title()}
                                       if kind in {"object", "array"} else {"type": kind})
                            schema["$defs"][definition] = ({"type": "null"} if kind == "null"
                                else {"anyOf": [literal, {"type": "null"}]})
                        choice["properties"]["value"] = {
                            "$ref": "#/$defs/" + value_definitions[kind]}
                    key = (location, tuple(names), kind)
                    if key not in slot_definitions:
                        slot_definitions[key] = f"N{len(slot_definitions)}"
                        schema["$defs"][slot_definitions[key]] = choice
                    choices.append({"$ref": "#/$defs/" + slot_definitions[key]})
            arguments["items"] = choices[0] if len(choices) == 1 else {"anyOf": choices}
        properties["arguments"] = arguments
        branches.append(branch)
    step["anyOf"] = branches
    for field in ("id", "role", "policy_id"):
        step["properties"][field] = shared_step["properties"][field]
    step["properties"]["operation_id"].pop("enum", None)


def validate_generic_arguments(draft: "GenericPlannerDraft", context: dict) -> None:
    """Recheck providers that ignore the output schema, before building a Scenario."""
    slots = argument_slots(context)
    for step in draft.steps:
        allowed = slots.get(step.operation_id)
        if allowed is None or any((a.location, a.name) not in allowed for a in step.arguments):
            raise ValidationFailure(Layer.MODEL, "MODEL_SCHEMA_GENERIC_ARGUMENTS")


def strip_schema_titles(schema: dict) -> None:
    """Drop generated titles and redundant unions without changing accepted JSON values."""
    schema.pop("title", None)
    alternatives = schema.get("anyOf", [])
    if {"type": "integer"} in alternatives and {"type": "number"} in alternatives:
        alternatives.remove({"type": "integer"})
    for keyword in ("properties", "$defs"):
        for child in schema.get(keyword, {}).values():
            strip_schema_titles(child)
    for child in schema.get("anyOf", []):
        strip_schema_titles(child)
    if "items" in schema:
        strip_schema_titles(schema["items"])


def short_container_refs(schema: dict) -> None:
    """Shorten wire-only definition identifiers, preserving every referenced constraint."""
    aliases = {"#/$defs/" + name: "#/$defs/C" + str(index)
               for index, name in enumerate(("LiteralArray", "LiteralMember", "LiteralObject"))}
    for original, alias in aliases.items():
        schema["$defs"][alias.rsplit("/", 1)[1]] = schema["$defs"].pop(original.rsplit("/", 1)[1])

    def replace(value):
        if isinstance(value, dict):
            if value.get("$ref") in aliases:
                value["$ref"] = aliases[value["$ref"]]
            for child in value.values():
                replace(child)
        elif isinstance(value, list):
            for child in value:
                replace(child)

    replace(schema)
