"""Wire-only constraints from approved input rules; never repair a returned plan."""

from copy import deepcopy

from agenttest.report import fingerprint


def encoded_literal(value):
    if isinstance(value, dict):
        return {"object": [{"key": k, "value": encoded_literal(v)} for k, v in value.items()]}
    if isinstance(value, list):
        return {"array": [encoded_literal(v) for v in value]}
    return value


def literal_schema(source):
    """Translate approved JSON values to the existing GenericDraftArgument container syntax."""
    kind = source["type"]
    if "enum" in source:
        return {"enum": [encoded_literal(v) for v in source["enum"]]}
    if kind == "object":
        choices = [dict(type="object", additionalProperties=False,
            required=["key", "value"], properties=dict(key=dict(type="string", enum=[name]),
                value=literal_schema(child)))
            for name, child in source.get("properties", {}).items()
            if not child.get("readOnly")]
        members = dict(type="array", maxItems=len(choices))
        if choices:
            members["items"] = {"anyOf": choices}
        else:
            members["maxItems"] = 0
        result = dict(type="object", additionalProperties=False, required=["object"],
                      properties=dict(object=members))
    elif kind == "array":
        items = dict(type="array", items=literal_schema(source["items"]))
        items.update({k: source[k] for k in ("minItems", "maxItems") if k in source})
        result = dict(type="object", additionalProperties=False, required=["array"],
                      properties=dict(array=items))
    else:
        result = {k: source[k] for k in ("type", "minimum", "maximum", "minLength", "maxLength")
                  if k in source}
        if "pattern" in source:
            result["pattern"] = source["pattern"]
        if source.get("format") in {"uuid", "date", "date-time", "email"}:
            result["format"] = source["format"]
        for key, inclusive in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
            value = source.get(key)
            if value is True and inclusive in result:
                result[key] = result.pop(inclusive)
            elif type(value) in {int, float}:
                result[key] = value
    return {"anyOf": [result, {"type": "null"}]} if source.get("nullable") else result


def constrain_product_steps(schema, context):
    """Intersect operation, policy, identity and valid baseline argument choices on the wire."""
    policies = {p["id"]: p for p in context["policies"]}
    groups = {}
    for policy in policies.values():
        baseline = policies.get(policy.get("binding_rules_from_policy"), policy)
        rules = baseline.get("binding_rules", [])
        signature = fingerprint([policy["operation_id"], policy["roles"], rules])
        group = groups.setdefault(signature, dict(policy=policy, rules=rules, ids=[]))
        group["ids"].append(policy["id"])
    original = schema["$defs"]["GenericDraftStep"]
    branches = []
    for group in groups.values():
        branch = deepcopy(original)
        policy, rules = group["policy"], group["rules"]
        props = branch["properties"]
        props["operation_id"] = {"type": "string", "enum": [policy["operation_id"]]}
        props["policy_id"] = {"type": "string", "enum": group["ids"]}
        props["role"] = {"type": ["string", "null"], "enum": policy["roles"]}
        arguments = []
        for rule in rules:
            literal = rule.get("literal_schema")
            if literal is not None and context.get("input_requirements"):
                from agenttest.product_inputs import InputRequirement, constrain_input_schema

                literal = constrain_input_schema(literal, policy["operation_id"], rule,
                    [InputRequirement.model_validate(r) for r in context["input_requirements"]])
            properties = dict(location=dict(type="string", enum=[rule["location"]]),
                name=dict(type="string", enum=[rule["name"]]),
                value=literal_schema(literal) if literal is not None else {"type": "null"},
                from_step=({"type": "null"} if literal is not None else
                           dict(type="string", minLength=1, maxLength=80)),
                select=({"type": "null"} if literal is not None else
                        dict(type="string", enum=[rule["source_selector"]])))
            arguments.append(dict(type="object", additionalProperties=False,
                                  properties=properties, required=list(properties)))
        props["arguments"] = dict(type="array", maxItems=min(len(rules), 30),
                                  items={"$ref": "#/$defs/GenericDraftArgument"})
        if not rules:
            props["arguments"].pop("items")
        if policy.get("closed_arguments"):
            props["arguments"]["minItems"] = len(rules)
        if arguments:
            props["arguments"]["items"] = {"anyOf": arguments}
        branches.append(branch)
    if branches:
        schema["$defs"]["GenericDraftStep"] = {"anyOf": branches}


def prune_schema_definitions(schema):
    """Remove only unreachable local definitions; preserve every reachable reference."""
    definitions = schema.get("$defs", {})
    pending = []

    def references(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
                    pending.append(value.removeprefix("#/$defs/").split("/")[0])
                else:
                    references(value)
        elif isinstance(node, list):
            for item in node:
                references(item)

    references({k: v for k, v in schema.items() if k != "$defs"})
    used = set()
    while pending:
        name = pending.pop()
        if name not in used:
            used.add(name)
            references(definitions[name])
    if used:
        schema["$defs"] = {k: v for k, v in definitions.items() if k in used}
    else:
        schema.pop("$defs", None)


def share_step_arguments(schema):
    """Factor repeated constraints; every referenced step retains its exact policies and inputs."""
    shared = {}

    def visit(node):
        if isinstance(node, dict):
            props = node.get("properties", {})
            if {"id", "operation_id", "policy_id", "arguments"} <= props.keys():
                arguments = props["arguments"]
                digest = fingerprint(arguments)
                if digest not in shared:
                    shared[digest] = (f"ArgumentSet{len(shared)}", deepcopy(arguments))
                props["arguments"] = {"$ref": "#/$defs/" + shared[digest][0]}
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(schema)
    schema["$defs"].update({name: arguments for name, arguments in shared.values()})
    steps = {}

    def collect(node):
        if isinstance(node, dict):
            if {"id", "operation_id", "policy_id", "arguments"} <= node.get(
                    "properties", {}).keys():
                steps.setdefault(fingerprint(node), []).append(node)
            else:
                for child in node.values():
                    collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(schema)
    for nodes in steps.values():
        if len(nodes) < 2:
            continue
        name = f"ApprovedStep{len(schema['$defs'])}"
        schema["$defs"][name] = deepcopy(nodes[0])
        for node in nodes:
            node.clear()
            node["$ref"] = "#/$defs/" + name
