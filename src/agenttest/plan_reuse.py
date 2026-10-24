"""Task-local immutable legal structures, never old IDs or rejected-plan repair."""

import re
from dataclasses import dataclass

from agenttest.credentials import CredentialVault
from agenttest.discovery_schema import fields, variations
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.json_values import json_equal
from agenttest.policies import NeedsReview
from agenttest.report import fingerprint
from agenttest.request_validation import validate_value
from agenttest.task_contract import task_validator


def structure_id(goal):
    return fingerprint(dict(project=goal.project_identity,
        task=goal.user_task_sha256, requirements=goal.task.requirements.model_dump(mode="json")))


def explicit_literal_fixed(text, value):
    return type(value) is str and any(value == (left or right)
        for left, right in re.findall(r'“([^“”]+)”|"([^"\n]+)"', text))


def validate_structure(project, goal, scenario, environ):
    """Run original DSL/OpenAPI/Policy/Intent gates without dispatch or model work."""
    if project.identity() != goal.project_identity:
        raise NeedsReview("PLAN_REUSE_PROJECT_CHANGED")
    vault = CredentialVault(project.target, environ)
    try:
        if vault.contains_secret(scenario.model_dump()):
            raise NeedsReview("DSL_SECRET_LITERAL")
    finally:
        vault.close()
    Executor(project.target, project.contract, environ=environ,
             registry=project.registry).validate(scenario)
    task_validator(goal.task, project.contract, project.target, project.registry)(
        goal.task.user_task, scenario.model_copy(deep=True))


@dataclass(frozen=True, repr=False)
class LegalStructure:
    """Only serialized data is retained; derived runs produce resources again."""

    identity: str
    project_identity: str
    scenario_json: str
    scenario_sha256: str
    source_run_id: str

    @classmethod
    def capture(cls, project, goal, scenario, report, environ):
        if not (report.dsl_valid and report.policy_authorized and report.intent_verified
                and report.scenario_sha256 == fingerprint(scenario.model_dump())):
            raise NeedsReview("PLAN_REUSE_BASE_NOT_VALIDATED")
        validate_structure(project, goal, scenario, environ)
        return cls(structure_id(goal), project.identity(), scenario.model_dump_json(),
                   fingerprint(scenario.model_dump()), report.run_id)

    def derive(self, project, base, goal, environ):
        """Change only a predeclared policy capability or approved literal boundary."""
        if (self.project_identity != project.identity() or self.identity != structure_id(base)
                or base.project_identity != goal.project_identity
                or base.user_task_sha256 != goal.user_task_sha256):
            raise NeedsReview("PLAN_REUSE_BINDING_MISMATCH")
        scenario = Scenario.model_validate_json(self.scenario_json)
        if fingerprint(scenario.model_dump()) != self.scenario_sha256:
            raise NeedsReview("PLAN_REUSE_BASE_CHANGED")
        validate_structure(project, base, scenario, environ)
        source = {s.key: s for s in base.task.requirements.steps}
        dest = {s.key: s for s in goal.task.requirements.steps}
        if (source.keys() != dest.keys()
                or base.task.requirements.before != goal.task.requirements.before):
            raise NeedsReview("PLAN_REUSE_RELATION_CHANGED")
        definitions = {p.id: p for p in project.registry.definitions()}
        changes, negative_rules = [], []
        for key, original in source.items():
            target = dest[key]
            if (original.operation_id, original.role) != (target.operation_id, target.role):
                raise NeedsReview("PLAN_REUSE_ROLE_OR_OPERATION_CHANGED")
            matches = [s for s in scenario.steps if (s.operation_id, s.role, s.policy_id) == (
                original.operation_id, original.role, original.policy_id)]
            if len(matches) != 1:
                raise NeedsReview("PLAN_REUSE_STEP_AMBIGUOUS")
            step = matches[0]
            policy = definitions.get(target.policy_id)
            if policy is None:
                raise NeedsReview("PLAN_REUSE_POLICY_NOT_DERIVABLE")
            if original.policy_id != target.policy_id:
                if not policy.negative or policy.negative.base_policy_id != original.policy_id:
                    raise NeedsReview("PLAN_REUSE_POLICY_NOT_DERIVABLE")
                mutated = next(a for a in original.arguments if (a.location, a.name) == (
                    policy.negative.location, policy.negative.name))
                if explicit_literal_fixed(goal.task.user_task, mutated.value):
                    raise NeedsReview("PLAN_REUSE_LITERAL_OR_SOURCE_FIXED")
                step.policy_id = target.policy_id
                negative_rules.append(dict(policy_id=policy.id,
                    approval=policy.negative.model_dump(mode="json"),
                    approval_sha256=fingerprint(policy.model_dump())))
                changes.append(dict(step_id=step.id, kind="approved_negative",
                    field=f"{policy.negative.location}.{policy.negative.name}",
                    source_policy_id=original.policy_id, approved_policy_id=target.policy_id,
                    approved_policy_sha256=fingerprint(policy.model_dump())))
            before = {(a.location, a.name): a for a in original.arguments}
            after = {(a.location, a.name): a for a in target.arguments}
            if before.keys() != after.keys():
                raise NeedsReview("PLAN_REUSE_ARGUMENT_SET_CHANGED")
            for slot, arg in before.items():
                other = after[slot]
                if arg.model_dump() == other.model_dump():
                    continue
                if (arg.source is not None or other.source is not None
                        or arg.selector != other.selector or policy.negative
                        or explicit_literal_fixed(goal.task.user_task, arg.value)):
                    raise NeedsReview("PLAN_REUSE_LITERAL_OR_SOURCE_FIXED")
                rules = [r for r in policy.binding_rules if (r.location, r.name) == slot]
                if len(rules) != 1 or rules[0].literal_schema is None:
                    raise NeedsReview("PLAN_REUSE_LITERAL_NOT_APPROVED")
                validate_value(other.value, rules[0].literal_schema)
                operation = next(o for o in project.contract.operations
                                 if o.operation_id == original.operation_id)
                specs, _ = fields(operation, project.contract.version, "#/operations")
                spec = next(f for f in specs if (f.location, f.name) == slot)
                if not any(polarity == "normal" and json_equal(value, other.value)
                           for _, polarity, _, value in variations(spec)):
                    raise NeedsReview("PLAN_REUSE_VARIATION_NOT_DECLARED")
                binding = getattr(step, slot[0])[slot[1]]
                if binding.from_step or not json_equal(binding.value, arg.value):
                    raise NeedsReview("PLAN_REUSE_SOURCE_CHANGED")
                binding.value = other.value
                changes.append(dict(step_id=step.id, kind="approved_literal",
                    field=".".join(slot), old_value_sha256=fingerprint(arg.value),
                    new_value_sha256=fingerprint(other.value), approved_policy_id=policy.id,
                    approved_policy_sha256=fingerprint(policy.model_dump())))
        if not changes:
            raise NeedsReview("PLAN_REUSE_NO_APPROVED_VARIATION")
        validate_structure(project, goal, scenario, environ)
        # Report only hashes and approved rule metadata; no credentials or live resource IDs.
        lineage = dict(schema_version="agenttest.plan-derivation.v1",
            source="DETERMINISTIC_RULE", base_structure_id=self.identity,
            base_plan_sha256=self.scenario_sha256, base_run_id=self.source_run_id,
            derived_plan_sha256=fingerprint(scenario.model_dump()), changes=changes,
            approved_rules=[dict(policy_id=r["policy_id"],
                approval_sha256=r["approval_sha256"]) for r in negative_rules],
            model_calls=0, reused_execution_state=False)
        return scenario, lineage
