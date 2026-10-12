"""Planner contexts can only be constructed from public tasks and trusted inputs."""

import json

from agenttest.discovery_models import DiscoveryResult
from agenttest.eval_v2.models import PublicTask
from agenttest.planner_input import planning_context
from agenttest.report import fingerprint

FORBIDDEN_KEYS = frozenset(
    {
        "labels",
        "rubric",
        "expected_behavior",
        "expected_goal",
        "required_goals",
        "required_operations",
        "required_roles",
        "required_parameters",
        "required_dependencies",
        "rejection_basis",
        "faults",
        "fault_mechanism",
        "review_status",
        "review_source",
        "label_provenance",
        "correct_plan",
        "scoring_labels",
        "support_status",
    }
)


def assert_no_labels(context: dict) -> None:
    pending = [context]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if FORBIDDEN_KEYS & value.keys():
                raise ValueError("EVAL_LABEL_LEAK")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def augment_context(
    base: dict, discovery: DiscoveryResult | None, *, max_bytes: int = 6000
) -> dict:
    """Only normal, visible candidates; no negative requests, scores or execution status."""
    if not 256 <= max_bytes <= 16_000:
        raise ValueError("Candidate context budget out of range")
    result = json.loads(json.dumps(base))
    assert_no_labels(result)
    if discovery is None:
        return result
    operations = {op["id"] for op in result["operations"]}
    policies = {p["id"] for p in result["policies"]}
    roles = set(result["roles"])
    extra = {
        "algorithm_version": discovery.algorithm_version,
        "artifact_sha256": fingerprint(discovery.model_dump()),
        "contract_sha256": discovery.contract_sha256,
        "policy_sha256": discovery.policy_sha256,
        "max_bytes": max_bytes,
        "candidates": [],
    }
    for candidate in sorted(discovery.candidates, key=lambda c: c.candidate_id):
        runtime_only = {
            "DSL_MISSING_AUTHENTICATION_CREDENTIALS",
            "RUNTIME_CREDENTIALS_REQUIRED",
            "RUNTIME_RESOURCE_VALUE_VALIDATION",
        }
        if (
            candidate.polarity != "normal"
            or not set(candidate.missing_evidence) <= runtime_only
            or candidate.policy_match is not True
            or not set(candidate.operation_ids) <= operations
            or not set(candidate.policy_refs) <= policies
            or not set(candidate.roles) <= roles
        ):
            continue
        row = {
            "candidate_id": candidate.candidate_id,
            "operation_ids": candidate.operation_ids,
            "policy_refs": candidate.policy_refs,
            "roles": candidate.roles,
            "scenario_kind": candidate.scenario_kind,
            "runtime_requirements": candidate.missing_evidence,
            "dependencies": [
                d.model_dump()
                for d in candidate.dependencies
                if d.policy_id in policies and d.source_policy_id in policies
            ],
        }
        extra["candidates"].append(row)
        if len(json.dumps(extra, ensure_ascii=False).encode()) > max_bytes:
            extra["candidates"].pop()
            break
    if len(json.dumps(extra, ensure_ascii=False).encode()) > max_bytes:
        raise ValueError("Candidate metadata exceeds budget")
    result["candidate_discovery"] = extra
    assert_no_labels(result)
    return result


def build_context(
    task: PublicTask, contract, config, registry, schema, *, discovery=None, max_bytes=6000
):
    if type(task) is not PublicTask:
        raise TypeError("Only PublicTask may enter planning context construction")
    if discovery is not None and (
        discovery.contract_sha256 != fingerprint(contract.model_dump())
        or discovery.policy_sha256 != registry.digest()
        or discovery.target_config_sha256 != fingerprint(config.model_dump())
    ):
        raise ValueError("Candidate input identity mismatch")
    return augment_context(
        planning_context(contract, config, registry, task.user_task, schema),
        discovery,
        max_bytes=max_bytes,
    )
