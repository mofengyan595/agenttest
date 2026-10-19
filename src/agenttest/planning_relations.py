"""Public contract-derived planning aids, independent of task answers and evaluators."""

from copy import deepcopy

PLANNING_CHECKLIST = """
Before returning JSON, account for EVERY requested resource, role and check in user_task.
Resolve explicit first/before/after instructions even between independent resources; source
bindings alone do not capture those instructions. Keep prerequisite and main resources distinct.
Use resource_dependencies to create producers before consumers and obey role relations.
Select each policy by its public purpose and the requested resource state at that point.
A state-changing step can invalidate a prior read policy's expected state; choose the approved
policy for the requested new state. Do not substitute, skip, reorder or weaken requested goals.
The step_options are legal combinations, not a recipe; choose only those needed by the task.
No TaskContract or reference answer is supplied. If no approved complete path exists, abstain.
"""


def planning_relations(context: dict) -> dict:
    policies = {p["id"]: p for p in context["policies"]}
    dependencies = []
    for policy in policies.values():
        for rule in policy.get("binding_rules", []):
            producer = policies.get(rule.get("source_policy_id"))
            if producer is None or rule.get("literal_schema") is not None:
                continue
            dependencies.append(dict(producer_policy=producer["id"],
                producer_operation=producer["operation_id"], consumer_policy=policy["id"],
                consumer_operation=policy["operation_id"], location=rule["location"],
                parameter=rule["name"], selector=rule["source_selector"],
                role_relation=rule.get("role_relation"), producer_before_consumer=True))
    return dict(resource_dependencies=dependencies, step_options=[
        dict(operation_id=p["operation_id"], policy_id=p["id"], roles=p["roles"],
             purpose=p.get("purpose")) for p in policies.values()],
        authentication_prerequisites=deepcopy(context.get("authentication", [])))


def constrain_approved_steps(schema: dict, step_type: str, context: dict) -> None:
    """Intersect operation-specific argument branches with approved policy/role tuples.

    No sequence, task rubric or resource ID is manufactured. Temporal/resource identity
    checks remain in the existing post-generation validators and Executor.
    """
    step = schema["$defs"][step_type]
    existing = step.get("anyOf", [])
    branches = []
    for option in context["policies"]:
        for template in existing:
            if option["operation_id"] not in template["properties"]["operation_id"]["enum"]:
                continue
            roles = [r for r in option["roles"] if r is None or r in context["roles"]]
            if not roles:
                continue
            branch = deepcopy(template)
            props = branch["properties"]
            props["operation_id"] = {"type": "string", "enum": [option["operation_id"]]}
            props["policy_id"] = {"type": "string", "enum": [option["id"]]}
            props["role"] = {"type": ["string", "null"], "enum": roles}
            branches.append(branch)
    if branches:
        step["anyOf"] = branches
    else:
        schema["properties"]["steps"]["maxItems"] = 0
