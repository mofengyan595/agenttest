"""An isolated read of the same resource as a dispatched, unresolved deletion."""

import re
from dataclasses import dataclass

import httpx

from agenttest.dsl import Scenario
from agenttest.product_preconditions import absence_expectation
from agenttest.report import fingerprint
from agenttest.selectors import select
from agenttest.step_recovery import DISPATCHED, EVIDENCE, load_receipt


@dataclass(frozen=True)
class ReadObservation:
    step_id: str
    source_id: str
    source_body: dict
    resource_sha256: str


def deletion_candidates(project, scenarios):
    operations = {op.operation_id: op for op in project.contract.operations}
    policies = {p.id: p for p in project.registry.definitions()}
    for plan in scenarios:
        if isinstance(plan, ValueError):
            continue
        for delete, read in zip(plan.steps, plan.steps[1:]):
            deletion = operations[delete.operation_id]
            lookup = operations[read.operation_id]
            if (deletion.method != "DELETE" or lookup.method != "GET"
                    or deletion.path != lookup.path or lookup.security
                    or any(step.role or step.poll or step.read_retries or step.body
                           or step.query or step.headers or step.upload
                           or policies[step.policy_id].negative
                           or step.operation_id in project.target.credential_inputs
                           for step in (delete, read))
                    or not absence_expectation(policies[read.policy_id])
                    or not policies[delete.policy_id].assertions.status
                    or not set(policies[delete.policy_id].assertions.status.codes) <= {200, 204}
                    or delete.path_parameters != read.path_parameters
                    or len(read.path_parameters) != 1):
                continue
            name, binding = next(iter(read.path_parameters.items()))
            source = next((step for step in plan.steps[:plan.steps.index(delete)]
                           if step.id == binding.from_step), None)
            if source is None or source.role or policies[source.policy_id].negative:
                continue
            rule = next((rule for rule in project.target.resource_lifecycles
                         if rule.create_operation == source.operation_id
                         and rule.delete_operation == delete.operation_id
                         and rule.id_selector == binding.select
                         and rule.path_parameter == name), None)
            if rule is None or not policies[source.policy_id].assertions.status or (
                    policies[source.policy_id].assertions.status.codes != [201]):
                continue
            yield dict(method="DELETE", plan_sha256=fingerprint(plan.model_dump()),
                       step_id=delete.id, source_id=source.id, read_id=read.id,
                       scenario=plan.model_dump(mode="json"))


def deletion_observation(project, candidate, attempts, row, identity, decode_identity):
    plan = Scenario.model_validate(candidate["scenario"])
    if fingerprint(plan.model_dump()) != identity["plan_sha256"]:
        return None
    valid = list(deletion_candidates(project, [plan]))
    if candidate not in valid:
        return None
    # The only admissible unknown dispatch in the group is the deletion itself.
    # In particular, no later write may have changed the resource before verification.
    same_run = [item for item in attempts if item["run_id"] == row["run_id"]]
    position = next(i for i, step in enumerate(plan.steps) if step.id == row["step_id"])
    preceding = {step.id for step in plan.steps[:position]}
    if (sum(item["phase"] == DISPATCHED for item in attempts) != 1
            or any(item != row and item["step_id"] not in preceding for item in same_run)):
        return None
    sources = [item for item in same_run if item["step_id"] == candidate["source_id"]]
    if len(sources) != 1 or sources[0]["phase"] != EVIDENCE:
        return None
    source_identity = decode_identity(sources[0])
    if (source_identity["authority"], source_identity["plan_sha256"],
            source_identity["method"]) != (identity["authority"], identity["plan_sha256"], "POST"):
        return None
    receipt = load_receipt(sources[0])
    if (not receipt["replayable"] or receipt["attempt"]["status_code"] != 201
            or receipt["attempt"]["error_code"] or not isinstance(receipt["body"], dict)):
        return None
    read = next(step for step in plan.steps if step.id == candidate["read_id"])
    op = next(op for op in project.contract.operations if op.operation_id == read.operation_id)
    name, binding = next(iter(read.path_parameters.items()))
    try:
        resource = select(receipt["body"], binding.select)
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if (type(resource) not in {str, int} or
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(resource))):
        return None
    path = op.path.replace("{" + name + "}", str(resource))
    resource_sha = fingerprint(httpx.URL(project.target.base_url + path).path)
    if resource_sha != identity["resource_sha256"]:
        return None
    return plan, ReadObservation(read.id, binding.from_step, receipt["body"], resource_sha)
