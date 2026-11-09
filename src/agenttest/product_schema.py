"""Wire-only constraints from approved input rules; never repair a returned plan."""

from copy import deepcopy

from agenttest.report import fingerprint


def share_repeated_schemas(schema):
    """Intern identical schema nodes through local refs without changing their constraints."""
    import json
    from collections import Counter

    counts, originals = Counter(), {}

    def children(node):
        for key in ('properties', '$defs', 'patternProperties', 'dependentSchemas'):
            for name, child in node.get(key, {}).items():
                if isinstance(child, dict):
                    yield node[key], name, child
        for key in ('items', 'additionalProperties', 'not', 'if', 'then', 'else', 'contains'):
            if isinstance(node.get(key), dict):
                yield node, key, node[key]
        for key in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
            for index, child in enumerate(node.get(key, [])):
                if isinstance(child, dict):
                    yield node[key], index, child

    def signature(node):
        return json.dumps(node, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

    def count(node):
        for _, _, child in children(node):
            encoded = signature(child)
            if len(encoded) >= 120:
                counts[encoded] += 1
                originals[encoded] = child
            count(child)

    count(schema)
    names, reserved = {}, set(schema.get('$defs', {}))
    for encoded in originals:
        if counts[encoded] > 1:
            name = 'S' + str(len(names))
            while name in reserved:
                name += '_'
            names[encoded] = name
            reserved.add(name)
    definitions = {}

    def replace(node):
        result = deepcopy(node)
        for parent, key, child in list(children(result)):
            encoded = signature(child)
            if encoded in names:
                name = names[encoded]
                if name not in definitions:
                    definitions[name] = replace(originals[encoded])
                parent[key] = {'$ref': '#/$defs/' + name}
            else:
                parent[key] = replace(child)
        return result

    result = replace(schema)
    result.setdefault('$defs', {}).update(definitions)
    schema.clear()
    schema.update(result)


def constrain_fixed_resource_setups(schema, context):
    """Keep one fixed fresh-resource setup per identity across ordinary wire cases.

    Only closed, singleton literal inputs qualify. Variable inputs, dependencies and
    explicit negative preparation remain unrestricted; executor validation is unchanged.
    """
    context.pop("fixed_resource_setups", None)
    context.pop("fixed_resource_dependent_policies", None)
    policies = {p["id"]: p for p in context["policies"]}
    fixed = {}
    for policy in policies.values():
        if (policy["operation_id"] not in context.get("fresh_resource_operations", [])
                or policy.get("negative_test") or not policy.get("closed_arguments")):
            continue
        baseline = policies.get(policy.get("binding_rules_from_policy"), policy)
        rules = baseline.get("binding_rules", [])
        if any(len((r.get("literal_schema") or {}).get("enum", [])) != 1 for r in rules):
            continue
        for role in policy["roles"]:
            key = fingerprint([policy["operation_id"], role, rules])
            entry = fixed.setdefault(key, dict(operation_id=policy["operation_id"],
                                               role=role, policy_ids=[]))
            entry["policy_ids"].append(policy["id"])
    if not fixed or len(fixed) > 12:
        return
    entries = {f"fresh_{i + 1}": entry for i, entry in enumerate(fixed.values())}
    pairs = {(identifier, entry["role"]) for entry in entries.values()
             for identifier in entry["policy_ids"]}
    dependent = {identifier for identifier, _ in pairs}
    while True:
        consumers = {p["id"] for p in policies.values() if any(
            r.get("source_policy_id") in dependent for r in policies.get(
                p.get("binding_rules_from_policy"), p).get("binding_rules", []))
            or set(p.get("prerequisite_policy_ids", [])) & dependent}
        if consumers <= dependent:
            break
        dependent.update(consumers)
    dependent.difference_update(identifier for identifier, _ in pairs)
    original_steps = deepcopy(schema["$defs"]["GenericDraftStep"]["anyOf"])

    def choices(entry=None):
        allowed = {(p, entry["role"]) for p in entry["policy_ids"]} if entry else set()
        result = []
        for branch in original_steps:
            props = branch["properties"]
            grouped = {}
            for identifier in props["policy_id"]["enum"]:
                if entry is None and identifier in dependent:
                    continue
                roles = tuple(role for role in props["role"]["enum"]
                              if (identifier, role) not in pairs or (identifier, role) in allowed)
                if roles:
                    grouped.setdefault(roles, []).append(identifier)
            for roles, identifiers in grouped.items():
                current = deepcopy(branch)
                current["properties"]["policy_id"]["enum"] = identifiers
                current["properties"]["role"]["enum"] = list(roles)
                result.append(current)
        return result

    case_type = schema["$defs"]["DesignCase"]
    plan_name = case_type["properties"]["plan"]["$ref"].rsplit("/", 1)[1]
    slots = {}
    for name, entry in entries.items():
        case = deepcopy(case_type)
        plan = deepcopy(schema["$defs"][plan_name])
        plan["properties"]["steps"]["items"] = {"anyOf": choices(entry)}
        case["properties"]["plan"] = plan
        slots[name] = {"anyOf": [dict(type="null"), case]}
    ordinary_steps = choices()
    steps = schema["$defs"][plan_name]["properties"]["steps"]
    if ordinary_steps:
        steps["items"] = {"anyOf": ordinary_steps}
    else:
        # Optional negative wire cases retain independent before/check/after schemas.
        cases = schema["properties"]["cases"]
        alternatives = [b for b in cases["items"].get("anyOf", [])
                        if b.get("$ref") != "#/$defs/DesignCase"]
        if alternatives:
            cases["items"] = {"anyOf": alternatives}
        else:
            cases.update(maxItems=0)
            cases.pop("items", None)
    schema["properties"]["resource_flows"] = dict(type="object", additionalProperties=False,
                                                   properties=slots, required=list(slots))
    schema["required"].append("resource_flows")
    context["fixed_resource_setups"] = [dict(slot=key, **entry) for key, entry in entries.items()]
    context["fixed_resource_dependent_policies"] = sorted(dependent)


def decode_fixed_resource_setups(wire, context):
    """Decode the predeclared wire slots; reject misplaced/duplicate setups without repair."""
    from agenttest.diagnostics import Layer, ValidationFailure

    setups = context.get("fixed_resource_setups", [])
    if not setups:
        return
    flows = wire.pop("resource_flows", None)
    if (type(flows) is not dict or set(flows) != {s["slot"] for s in setups}
            or type(wire.get("cases")) is not list):
        raise ValidationFailure(Layer.MODEL, "DESIGN_RESOURCE_SLOT_INVALID")
    pairs = {(p, s["role"]) for s in setups for p in s["policy_ids"]}
    for case in wire["cases"]:
        if type(case) is dict and "check" not in case and any(
                (s.get("policy_id"), s.get("role")) in pairs
                for s in case.get("plan", {}).get("steps", []) if type(s) is dict):
            raise ValidationFailure(Layer.MODEL, "DESIGN_DUPLICATE_RESOURCE_SETUP")
        if type(case) is dict and "check" not in case and any(
                s.get("policy_id") in context.get("fixed_resource_dependent_policies", [])
                for s in case.get("plan", {}).get("steps", []) if type(s) is dict):
            raise ValidationFailure(Layer.MODEL, "DESIGN_RESOURCE_DEPENDENCY_OUTSIDE_FLOW")
    for setup in setups:
        case = flows[setup["slot"]]
        if case is None:
            continue
        if type(case) is not dict or type(case.get("plan")) is not dict:
            raise ValidationFailure(Layer.MODEL, "DESIGN_RESOURCE_SLOT_INVALID")
        creations = [(s.get("policy_id"), s.get("role"))
                     for s in case["plan"].get("steps", []) if type(s) is dict
                     and (s.get("policy_id"), s.get("role")) in pairs]
        if len(creations) != 1 or creations[0] not in {
                (p, setup["role"]) for p in setup["policy_ids"]}:
            raise ValidationFailure(Layer.MODEL, "DESIGN_RESOURCE_SLOT_INVALID")
        wire["cases"].append(case)


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
    forbidden = {op for r in context.get('request_requirements', [])
                 for op in r.get('forbidden_operation_ids', [])}
    identities = {}
    for requirement in context.get('request_requirements', []):
        if requirement['disposition'] == 'required':
            for action in requirement['actions']:
                identities.setdefault(action['operation_id'], set()).add(action['role'])
    for policy in policies.values():
        if policy['operation_id'] in forbidden:
            continue
        if policy['operation_id'] in identities:
            policy = {**policy, 'roles': [role for role in policy['roles']
                                         if role in identities[policy['operation_id']]]}
            if not policy['roles']:
                continue
        baseline = policies.get(policy.get("binding_rules_from_policy"), policy)
        rules = baseline.get("binding_rules", [])
        role_groups = ([[role] for role in policy['roles']] if context.get('request_requirements')
                       else [policy['roles']])
        for roles in role_groups:
            scoped = {**policy, 'roles': roles}
            signature = fingerprint([policy["operation_id"], roles, rules])
            group = groups.setdefault(signature, dict(policy=scoped, rules=rules, ids=[]))
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
        if "requirement_source_id" in props:
            # Compact nullable slots; the trusted local check refuses unknown clauses. Sending a
            # closed enum repeats the clause vocabulary in every per-policy step branch and
            # pushes the frozen design request over its input budget.
            props["requirement_source_id"] = {
                "type": ["string", "null"],
                "description": "该步骤实现的需求子句 source_id；同一操作重复出现时必填，"
                               "不同子句必须绑定不同实例。"}
            props["requirement_action_index"] = {
                "type": ["integer", "null"], "minimum": 0, "maximum": 19,
                "description": "该子句内从 0 开始的第几次调用，通常为 0。"}
        arguments = []
        for rule in rules:
            literal = rule.get("literal_schema")
            if literal is not None and context.get("input_requirements"):
                from agenttest.product_inputs import (
                    InputRequirement,
                    approved_values,
                    constrain_input_schema,
                )

                alternatives = context.get("approved_input_values") or approved_values(
                    list(context["policies"]))
                literal = constrain_input_schema(literal, policy["operation_id"], rule,
                    [InputRequirement.model_validate(r) for r in context["input_requirements"]],
                    roles=policy['roles'],
                    source_requirements=context.get('request_requirements', []),
                    alternatives=alternatives)
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
