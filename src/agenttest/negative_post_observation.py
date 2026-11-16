"""Approved before/after observations around one rejected POST."""

from uuid import uuid4

from agenttest.dsl import ArgumentBinding, Scenario, Step
from agenttest.policies import NeedsReview
from agenttest.report import fingerprint


def _empty_lookup(policy, rule):
    return (policy.negative is None and policy.poll is None and policy.csv_fixture is None
            and policy.assertions.status and policy.assertions.status.codes == [200]
            and any(check.select == rule.count_selector and check.operator == "equals"
                    and type(check.expected) is int and check.expected == 0
                    for check in policy.assertions.fields))


def add_observations(project, scenario, *, namespace=None):
    """Wrap opted-in negative POSTs with two ordinary, independently validated GET steps."""
    operations = {op.operation_id: op for op in project.contract.operations}
    policies = {policy.id: policy for policy in project.registry.definitions()}
    steps = []
    for original in scenario.steps:
        rule = project.target.negative_post_observations.get(original.operation_id)
        policy = policies[original.policy_id]
        if not rule or not policy.negative:
            steps.append(original)
            continue
        candidates = [p for p in policies.values()
                      if p.operation_id == rule.operation_id and original.role in p.roles
                      and _empty_lookup(p, rule)]
        headers = [name for name in original.headers
                   if name.casefold() == rule.request_header.casefold()]
        if (operations[original.operation_id].method != "POST"
                or operations[rule.operation_id].method != "GET"
                or len(candidates) != 1 or len(headers) != 1):
            raise NeedsReview("NEGATIVE_POST_OBSERVATION_NOT_APPROVED")
        # Each durable case keeps its own observation across worker restarts.
        key = (uuid4().hex if namespace is None
               else fingerprint((namespace, original.id))[:32])
        nonce = ArgumentBinding(value=key)
        write = original.model_copy(deep=True)
        write.headers[headers[0]] = nonce
        before, after = [Step(id=f"observe_{phase}_{uuid4().hex if namespace is None else key}",
                             operation_id=rule.operation_id, role=write.role,
                             query={rule.query_parameter: nonce}, policy_id=candidates[0].id)
                         for phase in ("before", "after")]
        steps.extend((before, write, after))
    return Scenario(name=scenario.name, steps=steps)


def observations(target, scenario):
    """Yield the validated scenario's reserved wrappers, in dispatch order."""
    for before, write, after in zip(scenario.steps, scenario.steps[1:], scenario.steps[2:]):
        rule = target.negative_post_observations.get(write.operation_id)
        if (rule is not None and before.id.startswith("observe_before_")
                and after.id.startswith("observe_after_")
                and before.operation_id == after.operation_id == rule.operation_id):
            yield before, write, after, rule


def observation_receipts(project, scenario, case):
    """Match the actual on-wire key, approved policy, phase order and zero-count checks."""
    measured = {entry["step_id"]: entry for entry in case.get("evidence", ())}
    policies = {policy.id: policy for policy in project.registry.definitions()}
    receipts = {}
    for before, write, after, rule in observations(project.target, scenario):
        if (before.policy_id != after.policy_id or not _empty_lookup(
                policies[before.policy_id], rule) or not policies[write.policy_id].negative):
            continue
        actual = measured.get(write.id, {})
        reads = [measured.get(step.id, {}) for step in (before, after)]
        values = ((actual.get("request") or {}).get("values") or {}).get("headers") or {}
        key = next((value for name, value in values.items()
                    if name.casefold() == rule.request_header.casefold()), None)
        status = (actual.get("attempt") or {}).get("status_code")
        if (not isinstance(key, str) or len(key) != 32 or not actual.get("executed")
                or actual.get("outcome") != "PASS" or type(status) is not int
                or not 400 <= status < 500):
            continue
        if all(read.get("executed") and read.get("outcome") == "PASS"
               and read.get("operation_id") == rule.operation_id
               and read.get("policy_id") == step.policy_id
               and read.get("role") == write.role
               and (read.get("attempt") or {}).get("status_code") == 200
               and ((read.get("request") or {}).get("values") or {}).get(
                   "query", {}).get(rule.query_parameter) == key
               and any(check.get("selector") == rule.count_selector and check.get("passed")
                       and check.get("kind") == "equals"
                       for check in read.get("checks", []))
               for read, step in zip(reads, (before, after), strict=True)):
            receipts[write.id] = dict(before_step=before.id, after_step=after.id,
                                      lookup_policy_id=before.policy_id,
                                      correlation_sha256=fingerprint(key))
    return receipts
