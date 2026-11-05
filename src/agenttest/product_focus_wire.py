"""One operation owns its input and check references; contradictory scope copies are impossible."""

from copy import deepcopy

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.product_checks import constrain_requested_checks
from agenttest.product_inputs import constrain_focus_inputs, focus_input_groups, input_slot
from agenttest.report import fingerprint

BUNDLE_KEYS = ("input_requirements", "input_questions", "requested_checks", "expectation_questions")


def operation_checks(index):
    result = {}
    for entry in index:
        choices = result.setdefault(entry["operation_id"], {})
        choices[f"check_{len(choices) + 1}"] = entry
    return result


def public_input_groups(fields):
    groups = focus_input_groups(fields)
    for group in groups:
        group["field_ref"] = group["slots"][0]["slot_id"].split("/", 1)[1]
        group["operation_ids"] = [slot["operation_id"] for slot in group.pop("slots")]
    return groups


def public_check_groups(index):
    groups = {}
    for operation, entries in operation_checks(index).items():
        for reference, entry in entries.items():
            common = {k: v for k, v in entry.items() if k not in {"operation_id", "policy_id"}}
            common["check_ref"] = reference
            group = groups.setdefault(fingerprint(common), dict(**common, operation_ids=[]))
            group["operation_ids"].append(operation)
    return list(groups.values())


def constrain_operation_bundles(schema, context):
    """Share equivalent field schemas across the complete operation index."""
    templates = {key: schema["properties"].pop(key) for key in BUNDLE_KEYS}
    shared, operation_properties = {}, {}
    index = context.get("approved_checks", [])
    local_checks = operation_checks(index)
    from agenttest.request_requirements import future_only_operations

    future = future_only_operations(context)
    for operation in context["approved_ids"]:
        all_fields = [f for f in context.get("input_fields", []) if f["operation_id"] == operation]
        fields = [f for f in all_fields if not f.get("source_dependency")]
        local = dict(properties=deepcopy(templates), **{"$defs": schema["$defs"]})
        constrain_focus_inputs(local, focus_input_groups(fields))
        constrain_requested_checks(local, dict(approved_checks=[
            c for c in index if c["operation_id"] == operation]))
        expectation = deepcopy(schema["$defs"]["ExpectationQuestion"])
        props = expectation["properties"]
        props["kind"] = dict(type="string", enum=["business_rule"])
        props["role"] = dict(type="null")
        props["status_choices"] = dict(type="array", maxItems=0)
        expectation["required"] = list(props)
        questions = [expectation]
        roles = context.get("permission_question_roles", {}).get(operation, [])
        if roles:
            permission = deepcopy(expectation)
            props = permission["properties"]
            props["kind"] = dict(type="string", enum=["permission"])
            props["role"] = dict(type="string", enum=roles)
            props["requested_behavior"]["maxLength"] = 140
            props["status_choices"] = dict(type="array", minItems=2, maxItems=2,
                                           items=dict(type="integer", enum=[403, 404]))
            questions.append(permission)
        local["properties"]["expectation_questions"]["items"] = {"anyOf": questions}
        for key in ("input_requirements", "input_questions"):
            for branch in local["properties"][key].get("items", {}).get("anyOf", []):
                properties = branch["properties"]
                references = properties.pop("slot_id")["enum"]
                properties["field_ref"] = dict(type="string", enum=[
                    reference.split("/", 1)[1] for reference in references])
                branch["required"] = list(properties)
        from agenttest.product_sources import (
            constrain_input_sources,
            constrain_numeric_check_sources,
        )

        constrain_input_sources(local["properties"], context, operation, fields)
        item = local["properties"]["requested_checks"].get("items")
        if item:
            for branch in item.get("anyOf", [item]):
                identifiers = branch["properties"].pop("policy_id")["enum"]
                branch["properties"]["check_ref"] = dict(type="string", enum=[
                    ref for ref, check in local_checks[operation].items()
                    if check["policy_id"] in identifiers])
                branch["required"] = list(branch["properties"])
            constrain_numeric_check_sources(local["properties"]["requested_checks"],
                                            local_checks[operation], context)
        bundle = dict(type="object", additionalProperties=False,
                      properties={**local["properties"], "test_normal": dict(type="boolean"),
                          "existing_resource": dict(
                          type="boolean", enum=[False, True] if len(fields) != len(all_fields)
                          else [False])},
                      required=[*BUNDLE_KEYS, "existing_resource", "test_normal"])
        key = fingerprint(bundle)
        if key not in shared:
            name = f"FocusOperation{len(shared) + 1}"
            schema["$defs"][name] = bundle
            shared[key] = name
        operation_properties[operation] = dict(anyOf=[dict(type="null"),
            {"$ref": "#/$defs/" + shared[key]}])
        if operation in future:
            operation_properties[operation] = dict(type='null')
    return dict(type="object", additionalProperties=False,
                properties=operation_properties, required=list(operation_properties))


def decode_operation_bundles(operations, context):
    if type(operations) is not dict or set(operations) != set(context["approved_ids"]):
        raise ValidationFailure(Layer.MODEL, "FOCUS_WIRE_FLAGS_INCOMPLETE")
    fields = {input_slot(field): field for field in context.get("input_fields", [])}
    checks = operation_checks(context.get("approved_checks", []))
    result = {key: [] for key in (*BUNDLE_KEYS, "source_conflicts", "normal_operation_ids")}
    selected = []
    for operation, bundle in operations.items():
        if bundle is None:
            continue
        if type(bundle) is not dict or set(bundle) != {
                *BUNDLE_KEYS, "existing_resource", "test_normal"}:
            raise ValidationFailure(Layer.MODEL, "FOCUS_WIRE_SELECTION_INVALID")
        if type(bundle["test_normal"]) is not bool:
            raise ValidationFailure(Layer.MODEL, "FOCUS_NORMAL_SCOPE_INVALID")
        if bundle["test_normal"]:
            result["normal_operation_ids"].append(operation)
        selected.append(operation)
        existing = bundle["existing_resource"]
        has_source = any(f["operation_id"] == operation and f.get("source_dependency")
                         for f in fields.values())
        if type(existing) is not bool or existing and not has_source:
            raise ValidationFailure(Layer.MODEL, "FOCUS_SOURCE_CHOICE_INVALID")
        if existing:
            result["source_conflicts"].append(operation)
        for key in BUNDLE_KEYS:
            if type(bundle[key]) is not list:
                raise ValidationFailure(Layer.MODEL, "FOCUS_WIRE_SELECTION_INVALID")
            for supplied in bundle[key]:
                if type(supplied) is not dict:
                    raise ValidationFailure(Layer.MODEL, "FOCUS_WIRE_SELECTION_INVALID")
                entry = dict(supplied)
                if key == "expectation_questions":
                    if set(entry) != {"kind", "role", "status_choices", "question",
                                      "requested_behavior", "source_ref"}:
                        raise ValidationFailure(Layer.MODEL, "FOCUS_EXPECTATION_QUESTION_INVALID")
                    entry["operation_id"] = operation
                elif key == "requested_checks":
                    reference = entry.pop("check_ref", None)
                    approved = (checks.get(operation, {}).get(reference)
                                if type(reference) is str else None)
                    if approved is None or "policy_id" in entry:
                        raise ValidationFailure(Layer.MODEL, "FOCUS_REQUESTED_CHECK_INVALID")
                    entry["policy_id"] = approved["policy_id"]
                else:
                    reference = entry.pop("field_ref", None)
                    prefix = operation.replace("~", "~0").replace("/", "~1") + "/"
                    slot = prefix + reference if type(reference) is str else None
                    if slot not in fields or "slot_id" in entry:
                        raise ValidationFailure(Layer.MODEL, "FOCUS_INPUT_SLOT_INVALID")
                    if fields[slot].get("source_dependency"):
                        raise ValidationFailure(Layer.MODEL, "FOCUS_RUNTIME_VALUE_NOT_LITERAL")
                    entry["slot_id"] = slot
                result[key].append(entry)
    return selected, result
