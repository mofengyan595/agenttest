"""Prove fixed write scope from operator authority and journal this run's resources."""

import re

from agenttest.report import Outcome, fingerprint
from agenttest.selectors import select, validate_selector

READS = {"GET", "HEAD", "OPTIONS"}


def validate_lifecycle_authority(contract, target):
    """Reject ambiguous authority at configuration time, before planning or execution."""
    operations = {o.operation_id: o for o in contract.operations}
    for operation_id, rule in target.resource_creations.items():
        op = operations.get(operation_id)
        validate_selector(rule.id_selector)
        if (not target.allow_mutations or target.environment != "isolated" or not op
                or op.method != "POST" or operation_id not in target.allowed_operations
                or any(r.login_operation == operation_id for r in target.roles.values())):
            raise ValueError("CREATION_RECEIPT_AUTHORITY_INVALID")
    seen = set()
    for rule in target.resource_lifecycles:
        create = operations.get(rule.create_operation)
        delete = operations.get(rule.delete_operation)
        validate_selector(rule.id_selector)
        if (not target.allow_mutations or target.environment != "isolated"
                or rule.create_operation in seen or not create or not delete
                or create.method != "POST" or delete.method != "DELETE"
                or "{" in create.path or delete.path != create.path.rstrip("/")
                    + "/{" + rule.path_parameter + "}"
                or any(r.login_operation == rule.create_operation for r in target.roles.values())):
            raise ValueError("LIFECYCLE_AUTHORITY_INVALID")
        target.permit(rule.create_operation, create.method)
        target.permit(rule.delete_operation, delete.method)
        seen.add(rule.create_operation)


def lifecycle_plan(contract, target, scenario, registry, *, require_fresh_reads=True):
    """Reject unsupported lifecycles; never infer ownership from an operation's name."""
    validate_lifecycle_authority(contract, target)
    operations = {op.operation_id: op for op in contract.operations}
    policies = {p.id: p for p in registry.definitions()}
    creates, deletes = {}, {}
    for step in scenario.steps:
        op = operations[step.operation_id]
        role = target.roles.get(step.role)
        if op.method in READS:
            if require_fresh_reads:
                for binding in step.path_parameters.values():
                    owned = creates.get(binding.from_step)
                    if not owned or binding.select != owned[1].id_selector:
                        raise ValueError("LIFECYCLE_READ_PREPARATION_REQUIRED")
                if any(b.from_step and b.from_step not in creates
                       for b in [*step.query.values(), *step.body.values()]):
                    raise ValueError("LIFECYCLE_READ_PREPARATION_REQUIRED")
            continue
        if role and role.login_operation == step.operation_id:
            continue
        policy = policies[step.policy_id]
        rules = [r for r in target.resource_lifecycles
                 if r.create_operation == step.operation_id]
        if rules:
            if (len(rules) != 1 or op.method != "POST" or step.path_parameters
                    or policy.negative or not policy.assertions.status
                    or policy.assertions.status.codes != [201]):
                raise ValueError("LIFECYCLE_FRESH_CREATE_REQUIRED")
            rule = rules[0]
            if require_fresh_reads and any(b.from_step and b.from_step not in creates
                    for b in [*step.body.values(), *step.query.values()]):
                raise ValueError("LIFECYCLE_READ_PREPARATION_REQUIRED")
            validate_selector(rule.id_selector)
            creates[step.id] = (step, rule)
            continue
        # Every mutating resource address must be bound directly to a fresh creation.
        if len(step.path_parameters) != 1:
            raise ValueError("LIFECYCLE_OWNED_RESOURCE_REQUIRED")
        parameter, binding = next(iter(step.path_parameters.items()))
        owned = creates.get(binding.from_step)
        if not owned:
            raise ValueError("LIFECYCLE_OWNED_RESOURCE_REQUIRED")
        source, rule = owned
        if (binding.select != rule.id_selector or parameter != rule.path_parameter
                or step.role != source.role or source.id in deletes):
            raise ValueError("LIFECYCLE_RESOURCE_SCOPE_MISMATCH")
        # No hidden references to another run or a second resource in mutation data.
        if any(b.from_step for b in [*step.body.values(), *step.query.values()]):
            raise ValueError("LIFECYCLE_MUTATION_DATA_SCOPE_REQUIRED")
        create_op = operations[source.operation_id]
        expected_path = create_op.path.rstrip("/") + "/{" + parameter + "}"
        if op.path != expected_path:
            raise ValueError("LIFECYCLE_RESOURCE_PATH_MISMATCH")
        if op.method == "DELETE":
            if (step.operation_id != rule.delete_operation or step.body or step.query
                    or policy.negative or not policy.assertions.status
                    or any(c not in {200, 204} for c in policy.assertions.status.codes)):
                raise ValueError("LIFECYCLE_CLEANUP_POLICY_REQUIRED")
            deletes[source.id] = step
        elif op.method not in {"PUT", "PATCH"}:
            raise ValueError("LIFECYCLE_MUTATION_UNSUPPORTED")
    if not creates or set(creates) != set(deletes):
        raise ValueError("LIFECYCLE_PREPARATION_AND_CLEANUP_REQUIRED")
    return {key: (source, rule, deletes[key]) for key, (source, rule) in creates.items()}


class CreationJournal:
    """Persist one-shot creation outcomes without claiming cleanup or permitting replay."""

    def __init__(self, target, report):
        self.target, self.report = target, report

    def before(self, step, policy):
        rule = self.target.resource_creations.get(step.operation_id)
        if not rule:
            return None
        row = dict(create_step=step.id, operation_id=step.operation_id,
            owner_run_id=self.report.run_id, role=step.role, status="CREATE_OUTCOME_UNKNOWN",
            resource_id=None, basis=rule.fresh_resource_basis, cleanup_result="NOT_PERFORMED")
        if policy.negative:
            row["negative_policy_id"] = policy.id
        self.report.created_resources.append(row)
        return row

    def after(self, row, result, body, vault):
        if row is None or not result.attempts:
            return
        status = result.attempts[-1].status_code
        if status is None or result.attempts[-1].error_code:
            return
        if status != 201:
            row["status"] = "NOT_CONFIRMED_CREATED"
            return
        rule = self.target.resource_creations[row["operation_id"]]
        try:
            value = select(vault.public_response(body), rule.id_selector)
            if (type(value) not in {str, int} or type(value) is bool
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value))
                    or vault.contains_secret(value)):
                raise ValueError("UNSAFE_RESOURCE_ID")
            row.update(status="OWNED", resource_id=value, id_sha256=fingerprint(value),
                response_sha256=result.attempts[-1].response_sha256,
                checks_outcome=result.outcome.value)
        except (ValueError, KeyError, TypeError, IndexError):
            row["status"] = "RESOURCE_ID_UNVERIFIED"

    def finish(self):
        """Link cleanup only to this run's matching, separately verified lifecycle receipt."""
        for row in self.report.created_resources:
            if row["status"] != "OWNED":
                continue
            for proof in self.report.resource_lifecycle:
                if (proof["create_step"] == row["create_step"]
                        and proof["owner_run_id"] == row["owner_run_id"]
                        and proof["id_sha256"] == row["id_sha256"]):
                    row["cleanup_result"] = proof["cleanup_result"]
                    row["cleanup_step"] = proof["cleanup_step"]


class LifecycleJournal:
    """No resume method. Interrupted journals are evidence, never instructions to retry."""

    def __init__(self, plan, report):
        self.plan, self.report = plan, report
        self.rows = {}
        for key, (_, rule, cleanup) in plan.items():
            row = dict(create_step=key, cleanup_step=cleanup.id, owner_run_id=report.run_id,
                       status="NOT_CREATED", resource_id=None, id_sha256=None,
                       basis=rule.fresh_resource_basis, cleanup_result="NOT_SENT")
            self.rows[key] = row
        report.resource_lifecycle = list(self.rows.values())

    def before(self, step, method):
        if step.id in self.rows:
            self.rows[step.id]["status"] = "CREATE_OUTCOME_UNKNOWN"
        for row in self.rows.values():
            if row["cleanup_step"] == step.id:
                if row["status"] != "OWNED":
                    raise ValueError("LIFECYCLE_OWNERSHIP_NOT_PROVEN")
                row.update(status="CLEANUP_OUTCOME_UNKNOWN", cleanup_result="OUTCOME_UNKNOWN")
        if method in {"PUT", "PATCH"}:
            sources = {b.from_step for b in step.path_parameters.values()}
            for key in sources:
                if key in self.rows:
                    row = self.rows[key]
                    if row["status"] != "OWNED":
                        raise ValueError("LIFECYCLE_OWNERSHIP_NOT_PROVEN")
                    row.update(status="MUTATION_OUTCOME_UNKNOWN", mutation_step=step.id,
                               mutation_result="OUTCOME_UNKNOWN")

    def after(self, step, result, body, vault):
        if step.id in self.rows:
            row = self.rows[step.id]
            if result.outcome == Outcome.PASS:
                try:
                    value = select(vault.public_response(body), self.plan[step.id][1].id_selector)
                    if (type(value) not in {str, int} or type(value) is bool
                            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value))
                            or vault.contains_secret(value)):
                        raise ValueError("UNSAFE_RESOURCE_ID")
                    if any(other is not row and other["resource_id"] == value
                           for other in self.rows.values()):
                        raise ValueError("DUPLICATE_RESOURCE_ID")
                    row.update(status="OWNED", resource_id=value, id_sha256=fingerprint(value))
                except (ValueError, KeyError, TypeError, IndexError):
                    row["status"] = "RESOURCE_ID_UNVERIFIED"
                    raise ValueError("LIFECYCLE_RESOURCE_ID_UNVERIFIED") from None
        for row in self.rows.values():
            if row.get("mutation_step") == step.id:
                row.update(status="OWNED", mutation_result=result.outcome.value)
            if row["cleanup_step"] == step.id:
                row.update(status="CLEANED" if result.outcome == Outcome.PASS
                           else "CLEANUP_PENDING", cleanup_result=result.outcome.value)

    def reconcile_unsent(self):
        """A rejected preflight is not an unknown sent request; retain the failed result."""
        results = {s.step_id: s for s in [*self.report.steps, *self.report.lifecycle_cleanup]}
        for row in self.rows.values():
            for key, state, restored in (("create_step", "CREATE_OUTCOME_UNKNOWN", "NOT_CREATED"),
                    ("cleanup_step", "CLEANUP_OUTCOME_UNKNOWN", "CLEANUP_PENDING"),
                    ("mutation_step", "MUTATION_OUTCOME_UNKNOWN", "OWNED")):
                result = results.get(row.get(key))
                if row["status"] == state and result is not None and not result.attempts:
                    row["status"] = restored
                    if key == "cleanup_step":
                        row["cleanup_result"] = "NOT_SENT_PRECHECK_REJECTED"
                    elif key == "mutation_step":
                        row["mutation_result"] = "NOT_SENT_PRECHECK_REJECTED"

    def finish(self):
        self.reconcile_unsent()
        pending = [r for r in self.rows.values() if r["status"] not in {"NOT_CREATED", "CLEANED"}]
        self.report.cleanup = ("本轮资源已清理。" if not pending else
                               "清理未完成；保留本轮资源和未知结果，请人工核实，不自动重放。")
