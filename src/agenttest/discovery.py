"""Deterministic, bounded candidate discovery. No HTTP, environment reads or execution."""

import json

from agenttest.contracts import ContractSummary
from agenttest.diagnostics import ValidationFailure
from agenttest.discovery_models import (
    Candidate,
    CoverageRatio,
    DependencyEvidence,
    DiscoveryResult,
    OperationCoverage,
    PolicyCoverageRow,
    ScenarioSlot,
    StaticCoverage,
)
from agenttest.discovery_schema import describe, fields, variations, witness
from agenttest.dsl import ArgumentBinding, Scenario, Step, validate_plan
from agenttest.planner_input import PRIVATE_LITERAL
from agenttest.policies import (
    NeedsReview,
    PolicyRegistry,
    RegisteredPolicy,
    dependency_roles_match,
)
from agenttest.report import fingerprint
from agenttest.request_validation import validate_request, validate_value
from agenttest.safety import TargetConfig
from agenttest.selectors import validate_selector

MAX_CANDIDATES = 2000
MAX_OPERATIONS = 200


def trusted(policy: RegisteredPolicy) -> bool:
    return bool(
        policy.assertions.reviewed
        and policy.assertions.source.strip()
        and (policy.assertions.status or policy.assertions.fields)
    )


def _metadata(value: str) -> None:
    if len(value) > 512 or PRIVATE_LITERAL.search(value) or any(ord(c) < 32 for c in value):
        raise ValueError("DISCOVERY_UNSAFE_METADATA")


def discover(
    contract: ContractSummary,
    registry: PolicyRegistry,
    config: TargetConfig | None = None,
    *,
    business_scenario: str = "default",
    excluded_operation_ids: list[str] | None = None,
) -> DiscoveryResult:
    """Discover opportunities, never authorize a candidate or create an executable plan.

    STATIC_VALIDATED means the unchanged DSL and Registry checks passed with an empty
    credential environment. Runtime target authorization and operator review still apply.
    """
    contract = contract.model_copy(deep=True)
    registry = registry.snapshot()
    config = config.model_copy(deep=True) if config else None
    scope = config.business_scenario if config else business_scenario
    excluded = sorted(set(excluded_operation_ids or []))
    definitions = registry.definitions()
    input_identity = {
        "algorithm_version": "scalar-subset-v1",
        "contract": fingerprint(contract.model_dump()),
        "policies": registry.digest(),
        "target": fingerprint(config.model_dump()) if config else None,
        "scope": scope,
        "excluded": excluded,
    }
    if len(contract.operations) > MAX_OPERATIONS or len(definitions) > 500:
        raise ValueError("DISCOVERY_INPUT_BUDGET")
    _metadata(scope)
    operations = {op.operation_id: op for op in contract.operations if op.operation_id}
    for op in contract.operations:
        _metadata(op.path)
        if op.operation_id:
            _metadata(op.operation_id)
    for item in excluded:
        _metadata(item)
    for policy in definitions:
        for value in [policy.id, policy.operation_id, *[r for r in policy.roles if r]]:
            _metadata(value)
        for rule in policy.binding_rules:
            for value in (rule.name, rule.source_selector, rule.source_policy_id or ""):
                _metadata(value)
    policies = {p.id: p for p in definitions if p.business_scenario == scope}
    by_operation = {op: [p for p in policies.values() if p.operation_id == op] for op in operations}
    included = set(operations) - set(excluded)
    candidates: dict[str, Candidate] = {}
    unsupported: set[str] = set()
    slots: set[tuple[str, str]] = set()
    issues: set[str] = set()
    schema_cache = {}
    basis = {
        op.operation_id: f"#/operations/{i}"
        for i, op in enumerate(contract.operations)
        if op.operation_id
    }

    def get_fields(operation_id: str):
        if operation_id not in schema_cache:
            result = fields(operations[operation_id], contract.version, basis[operation_id])
            for field in result[0]:
                _metadata(field.name)
            schema_cache[operation_id] = result
        return schema_cache[operation_id]

    def emit(candidate: Candidate) -> None:
        candidate.missing_evidence = sorted(set(candidate.missing_evidence))
        candidate.policy_refs = sorted(set(candidate.policy_refs))
        candidate.roles = sorted(set(candidate.roles))
        candidate.schema_basis = sorted(set(candidate.schema_basis))
        candidate.candidate_id = "cand_" + fingerprint(
            {"inputs": input_identity, "candidate": candidate.model_dump(exclude={"candidate_id"})}
        )
        candidates[candidate.candidate_id] = candidate
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError("DISCOVERY_CANDIDATE_BUDGET")

    def build(
        operation_id: str,
        policy: RegisteredPolicy | None,
        role: str | None,
        flow: bool,
        override: tuple | None = None,
    ):
        steps: list[Step] = []
        descriptions = []
        dependencies = []
        auth_order = []
        active: set[tuple[str, str | None]] = set()
        built: dict[tuple[str, str | None], Step] = {}
        missing: set[str] = set()

        def visit(op_id: str, p: RegisteredPolicy | None, r: str | None) -> Step:
            key = (p.id if p else op_id, r)
            if key in active or len(active) >= 20 or len(steps) >= 100:
                raise NeedsReview("DEPENDENCY_CYCLE_OR_BUDGET")
            if key in built:
                return built[key]
            if op_id not in included:
                raise NeedsReview("DEPENDENCY_OPERATION_UNAVAILABLE")
            active.add(key)
            op = operations[op_id]
            specs, media = get_fields(op_id)
            managed = bool(
                config and r in config.roles and config.roles[r].login_operation == op_id
            )
            step = Step(id="pending", operation_id=op_id, role=r, policy_id=p.id if p else None)
            rules = {(rule.location, rule.name): rule for rule in p.binding_rules} if p else {}
            if len(rules) != (len(p.binding_rules) if p else 0):
                raise NeedsReview("POLICY_DUPLICATE_BINDING_RULE")
            if managed:
                descriptions.append(
                    {"location": "body", "name": "*", "action": "executor_managed_credentials"}
                )
                missing.add("RUNTIME_CREDENTIALS_REQUIRED")
            else:
                include_body = bool(
                    (op.request_body or {}).get("required")
                    or any(location == "body" for location, _ in rules)
                    or override and op_id == operation_id and override[0] == "body"
                )
                for field in specs:
                    if field.location == "body" and not include_body:
                        continue
                    rule = rules.get((field.location, field.name))
                    selected = bool(
                        override
                        and op_id == operation_id
                        and field.location == override[0]
                        and field.name == override[1]
                    )
                    if not field.required and rule is None and not selected:
                        continue
                    if rule and rule.source_policy_id:
                        validate_selector(rule.source_selector)
                        dependencies.append(
                            DependencyEvidence(
                                policy_id=p.id,
                                source_policy_id=rule.source_policy_id,
                                location=rule.location,
                                name=rule.name,
                                source_selector=rule.source_selector,
                                role_relation=rule.role_relation,
                            )
                        )
                        if rule.source_selector == "$":
                            raise NeedsReview("MISSING_RESOURCE_SELECTOR")
                        if not flow:
                            raise NeedsReview("RESOURCE_SOURCE_STEP_REQUIRED")
                        source = policies.get(rule.source_policy_id)
                        if source is None:
                            raise NeedsReview("UNKNOWN_SOURCE_POLICY")
                        if source.operation_id not in included:
                            raise NeedsReview("DEPENDENCY_OPERATION_UNAVAILABLE")
                        if rule.role_relation == "anonymous" and None in source.roles:
                            roles = [None]
                        else:
                            roles = sorted(x for x in source.roles
                                           if x is not None and config and x in config.roles)
                        allowed = [
                            x
                            for x in roles
                            if dependency_roles_match(x, r, rule.role_relation)
                        ]
                        if not allowed:
                            raise NeedsReview("DEPENDENCY_ROLE_EVIDENCE_MISSING")
                        previous = visit(source.operation_id, source, allowed[0])
                        binding = ArgumentBinding(
                            from_step=previous.id, select=rule.source_selector
                        )
                        descriptions.append(
                            {
                                "location": field.location,
                                "name": field.name,
                                "action": "approved_resource_binding",
                            }
                        )
                        missing.add("RUNTIME_RESOURCE_VALUE_VALIDATION")
                    else:
                        # A free literal cannot stand in for a resource ID without a rule.
                        if field.location == "path_parameters" and (
                            rule is None or rule.literal_schema is None
                        ):
                            raise NeedsReview("RESOURCE_BINDING_EVIDENCE_MISSING")
                        extra = rule.literal_schema if rule else None
                        value = witness(field.schema, extra)
                        if (
                            override
                            and op_id == operation_id
                            and field.location == override[0]
                            and field.name == override[1]
                        ):
                            value = override[2]
                            validate_value(value, field.schema)
                            if extra is not None:
                                validate_value(value, extra)
                        binding = ArgumentBinding(value=value)
                        descriptions.append(describe(
                            field, value, private_enum=bool(extra and "enum" in extra),
                        ).model_dump())
                    getattr(step, field.location)[field.name] = binding
                if not any(
                    b.from_step
                    for name in ("body", "query", "path_parameters")
                    for b in getattr(step, name).values()
                ):
                    validate_request(
                        op,
                        {k: v.value for k, v in step.path_parameters.items()},
                        {k: v.value for k, v in step.query.items()},
                        {k: v.value for k, v in step.body.items()},
                        media if include_body else None,
                    )
                if op.request_body and op.request_body.get("required") and not step.body:
                    raise NeedsReview("EMPTY_REQUIRED_BODY_NOT_REPRESENTABLE")
                if include_body and not step.body:
                    raise NeedsReview("EMPTY_BODY_NOT_REPRESENTABLE")
            if op.security and {} not in op.security:
                if not config or r not in config.roles:
                    raise NeedsReview("AUTHENTICATION_METADATA_REQUIRED")
                login = config.roles[r].login_operation
                if flow and login and login != op_id:
                    matches = [
                        p for p in by_operation.get(login, []) if r in p.roles and trusted(p)
                    ]
                    if not matches:
                        raise NeedsReview("LOGIN_POLICY_REQUIRED")
                    first = visit(login, matches[0], r)
                    auth_order.append(f"{first.operation_id}:{r} -> {op_id}:{r}")
                else:
                    missing.add("AUTHENTICATION_PREDECESSOR_REQUIRED")
            if config:
                config.permit(op_id, op.method)
                if r is not None and r not in config.roles:
                    raise NeedsReview("UNKNOWN_ROLE")
            else:
                missing.add("TARGET_CONFIGURATION_REQUIRED")
            step.id = f"s{len(steps) + 1}"
            if p:
                registry.resolve(step, scope, {s.id: s for s in steps})
            else:
                missing.add("TRUSTED_EXPECTATION_REQUIRED")
            active.remove(key)
            steps.append(step)
            built[key] = step
            return step

        schema_valid = policy_match = None
        error = None
        try:
            visit(operation_id, policy, role)
            schema_valid = (
                None
                if dependencies
                or any(item["action"] == "executor_managed_credentials" for item in descriptions)
                else True
            )
            policy_match = policy is not None
            if config:
                try:
                    validate_plan(
                        Scenario(name="static discovery preflight", steps=steps),
                        contract,
                        config,
                        environ={},
                    )
                except ValidationFailure as exc:
                    if exc.diagnostic.code not in {
                        "DSL_MISSING_AUTHENTICATION_CREDENTIALS",
                        "DSL_AUTHENTICATION_ORDER",
                    }:
                        raise
                    missing.add(exc.diagnostic.code)
        except ValueError as exc:
            error = (
                exc.diagnostic.code
                if isinstance(exc, ValidationFailure)
                else "STATIC_PREFLIGHT_REJECTED"
            )
            missing.add(error)
        return (
            steps,
            descriptions,
            dependencies,
            auth_order,
            sorted(missing),
            schema_valid,
            policy_match,
            error,
        )

    for operation_id in sorted(included):
        op = operations[operation_id]
        slots.add((operation_id, "normal"))
        matching = by_operation[operation_id]
        for p in matching:
            if any(r is not None for r in p.roles):
                slots.add((operation_id, "role_scope"))
            if any(rule.source_policy_id for rule in p.binding_rules):
                slots.add((operation_id, "resource_flow"))
        try:
            specs, _ = get_fields(operation_id)
        except ValueError as exc:
            unsupported.add(operation_id)
            code = (
                exc.diagnostic.code if isinstance(exc, ValidationFailure) else "UNSUPPORTED_SCHEMA"
            )
            emit(
                Candidate(
                    operation_id=operation_id,
                    operation_ids=[operation_id],
                    method=op.method,
                    path=op.path,
                    scenario_kind="unsupported_schema",
                    polarity="review",
                    schema_basis=[basis[operation_id]],
                    executable_status="BLOCKED",
                    missing_evidence=[code],
                    rejection_reason=code,
                )
            )
            continue
        pairs = [(p, r) for p in matching for r in sorted(p.roles, key=lambda r: r or "")]
        if not pairs:
            pairs = [(None, None)]
        for policy, role in pairs:
            variants = [("normal", "normal", None)]
            if role is not None:
                variants.append(("role_scope", "normal", None))
            if policy and any(rule.source_policy_id for rule in policy.binding_rules):
                variants.append(("resource_flow", "normal", None))
            managed = bool(
                config
                and role in config.roles
                and config.roles[role].login_operation == operation_id
            )
            if not managed:
                if op.request_body and op.request_body.get("required"):
                    slots.add((operation_id, "required_missing"))
                    emit(
                        Candidate(
                            operation_id=operation_id,
                            operation_ids=[operation_id],
                            method=op.method,
                            path=op.path,
                            scenario_kind="required_missing",
                            polarity="negative",
                            schema_basis=[basis[operation_id] + "/request_body/required"],
                            input_variations=[
                                {"location": "body", "name": "*", "action": "omit_required_body"}
                            ],
                            schema_valid=False,
                            policy_match=False,
                            executable_status="STATIC_ONLY_NEGATIVE",
                            missing_evidence=[
                                "NEGATIVE_EXECUTION_POLICY_NOT_IMPLEMENTED",
                                "TRUSTED_NEGATIVE_EXPECTATION_REQUIRED",
                            ],
                            rejection_reason="EXECUTOR_REQUEST_VALIDATION_MUST_NOT_BE_BYPASSED",
                        )
                    )
                for field in specs:
                    for kind, polarity, variation, value in variations(field):
                        slots.add((operation_id, kind))
                        variants.append((kind, polarity, (field, variation, value)))
            for kind, polarity, variant in variants:
                slots.add((operation_id, kind))
                schema_basis = [basis[operation_id], *[f.basis for f in specs]]
                if variant:
                    keys = {
                        "required_missing": "required",
                        "enum_boundary": "enum",
                        "numeric_lower": "minimum",
                        "numeric_upper": "maximum",
                        "string_min_length": "minLength",
                        "string_max_length": "maxLength",
                        "type_mismatch": "type",
                    }
                    key = keys[kind]
                    if (
                        kind.startswith("numeric_")
                        and "exclusive" + key.capitalize() in variant[0].schema
                    ):
                        key = "exclusive" + key.capitalize()
                    schema_basis = [
                        variant[0].required_basis
                        if kind == "required_missing"
                        else variant[0].basis + "/" + key
                    ]
                    if kind.startswith("numeric_"):
                        bound = keys[kind]
                        schema_basis = [variant[0].basis + "/" + constraint
                                        for constraint in (bound, "exclusive" + bound.capitalize())
                                        if constraint in variant[0].schema]
                if polarity == "negative":
                    emit(
                        Candidate(
                            operation_id=operation_id,
                            operation_ids=[operation_id],
                            method=op.method,
                            path=op.path,
                            scenario_kind=kind,
                            polarity="negative",
                            schema_basis=schema_basis,
                            input_variations=[variant[1]],
                            executable_status="STATIC_ONLY_NEGATIVE",
                            schema_valid=False,
                            policy_match=False,
                            missing_evidence=[
                                "NEGATIVE_EXECUTION_POLICY_NOT_IMPLEMENTED",
                                "TRUSTED_NEGATIVE_EXPECTATION_REQUIRED",
                            ],
                            rejection_reason="EXECUTOR_REQUEST_VALIDATION_MUST_NOT_BE_BYPASSED",
                        )
                    )
                    continue
                override = (variant[0].location, variant[0].name, variant[2]) if variant else None
                result = build(operation_id, policy, role, kind == "resource_flow", override)
                steps, descriptions, deps, auth, missing, valid, approved, error = result
                # Conflicting valid-edge/enum requests are review records, not normal witnesses.
                candidate_polarity = "review" if error else "normal"
                emit(
                    Candidate(
                        operation_id=operation_id,
                        operation_ids=list(dict.fromkeys(s.operation_id for s in steps))
                        + (
                            []
                            if operation_id in {s.operation_id for s in steps}
                            else [operation_id]
                        ),
                        method=op.method,
                        path=op.path,
                        scenario_kind=kind,
                        polarity=candidate_polarity,
                        schema_basis=schema_basis,
                        input_variations=descriptions,
                        policy_refs=[s.policy_id for s in steps if s.policy_id]
                        + ([policy.id] if policy else []),
                        roles=[s.role for s in steps if s.role] + ([role] if role else []),
                        dependencies=deps,
                        authentication_order=auth,
                        missing_evidence=missing,
                        schema_valid=valid,
                        policy_match=approved,
                        rejection_reason=error,
                        executable_status="STATIC_VALIDATED" if not missing else "BLOCKED",
                    )
                )

    ordered = sorted(
        candidates.values(),
        key=lambda c: (c.operation_id, c.scenario_kind, c.polarity, c.candidate_id),
    )
    discovered = {(c.operation_id, c.scenario_kind) for c in ordered if c.polarity != "review"}
    covered = {op_id for op_id, _ in discovered}
    for p in definitions:
        if p.operation_id not in operations:
            issues.add("POLICY_OPERATION_ABSENT:" + p.id)
        if p.business_scenario != scope:
            issues.add("POLICY_OUTSIDE_SCOPE:" + p.id)
    for op_id in set(excluded) - set(operations):
        issues.add("UNKNOWN_EXCLUDED_OPERATION:" + op_id)
    scenario_slots = [
        ScenarioSlot(operation_id=op_id, scenario_kind=kind, discovered=(op_id, kind) in discovered)
        for op_id, kind in sorted(slots)
    ]
    return DiscoveryResult(
        openapi_version=contract.version,
        contract_sha256=fingerprint(contract.model_dump()),
        policy_sha256=registry.digest(),
        target_config_sha256=fingerprint(config.model_dump()) if config else None,
        business_scenario=scope,
        excluded_operation_ids=excluded,
        candidates=ordered,
        issues=sorted(issues),
        coverage=StaticCoverage(
            operation=OperationCoverage(
                ratio=CoverageRatio(numerator=len(covered), denominator=len(included)),
                with_normal_candidates=CoverageRatio(
                    numerator=len({c.operation_id for c in ordered if c.polarity == "normal"}),
                    denominator=len(included)),
                static_preflight=CoverageRatio(
                    numerator=len({c.operation_id for c in ordered
                                   if c.executable_status == "STATIC_VALIDATED"}),
                    denominator=len(included)),
                trusted_policy=CoverageRatio(
                    numerator=len({p.operation_id for p in policies.values()
                                   if p.operation_id in included and trusted(p)}),
                    denominator=len(included)),
                execution=CoverageRatio(numerator=0, denominator=len(included)),
                contract_operations=len(contract.operations),
                missing_operation_id=[
                    f"{op.method} {op.path}" for op in contract.operations if not op.operation_id
                ],
                excluded_operations=sorted(set(excluded) & set(operations)),
                unsupported_operations=sorted(unsupported),
                without_candidates=sorted(included - covered),
            ),
            scenario=CoverageRatio(
                numerator=sum(s.discovered for s in scenario_slots), denominator=len(scenario_slots)
            ),
            scenario_slots=scenario_slots,
            policy=[
                PolicyCoverageRow(
                    policy_id=p.id,
                    operation_id=p.operation_id,
                    in_contract=p.operation_id in operations,
                    in_scope=p.business_scenario == scope,
                    trusted_assertions=trusted(p),
                    has_candidate=any(p.id in c.policy_refs for c in ordered),
                    static_preflight_met=any(
                        p.id in c.policy_refs and c.executable_status == "STATIC_VALIDATED"
                        for c in ordered
                    ),
                )
                for p in definitions
            ],
        ),
    )


def discovery_json(result: DiscoveryResult) -> str:
    """Byte-stable UTF-8 JSON, without timestamps, random IDs, input paths or credentials."""
    return (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
