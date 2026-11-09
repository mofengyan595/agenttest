"""Build a project from an operator-reviewed contract, never from model authority."""

from typing import Any, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from agenttest.contracts import ContractSummary
from agenttest.dsl import AssertionPolicy, CSVUpload, ExpectedStatus, FieldAssertion, PollPolicy
from agenttest.json_values import json_equal
from agenttest.planner_input import validate_user_task
from agenttest.policies import ApprovedCSVFixture, BindingRule, PolicyRegistry, RegisteredPolicy
from agenttest.project_goals import (
    GoalCatalog,
    GoalProject,
    GoalTemplate,
    ProjectBudget,
    RegisteredProject,
)
from agenttest.project_inventory import (
    credential_field,
    identity_ready,
    input_fields,
    inventory,
    linked_inputs,
    response_checks,
    response_fields,
    sample,
    upload_field,
)
from agenttest.report import fingerprint
from agenttest.request_validation import validate_value
from agenttest.response_content import validate_csv_assertions
from agenttest.safety import (
    ResourceCreation,
    ResourceLifecycle,
    RoleCredential,
    StrictModel,
    TargetConfig,
)
from agenttest.selectors import validate_selector

# Operator-approved non-success outcomes an expectation policy may declare.
NON_SUCCESS_EXPECTATIONS = frozenset({400, 401, 403, 404, 409, 410, 422})


class IntakeArgument(StrictModel):
    location: Literal["body", "path_parameters", "query", "headers"]
    name: str = Field(min_length=1, max_length=80)
    value: Any = None
    # Approved alternatives for a generated input: the model may only pick one of them.
    values: list[Any] | None = Field(default=None, min_length=2, max_length=8)
    source_operation: str | None = None
    selector: str = "$"
    generated: bool = False
    negative_enabled: bool = False
    needs_input: bool = False

    @model_serializer(mode="wrap")
    def legacy_defaults(self, handler):
        data = handler(self)
        if self.values is None:
            data.pop("values", None)
        for key in ("generated", "needs_input", "negative_enabled"):
            if not getattr(self, key):
                data.pop(key, None)
        return data

    @field_validator("selector")
    @classmethod
    def selector_is_safe(cls, value):
        return validate_selector(value)


class IntakeOperation(StrictModel):
    operation_id: str = Field(min_length=1, max_length=160)
    alias: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    role: str | None = None
    statuses: list[int] = Field(min_length=1, max_length=20)
    arguments: list[IntakeArgument] = Field(default_factory=list, max_length=100)
    checks: list[FieldAssertion] = Field(default_factory=list, max_length=30)
    check_source: str | None = Field(default=None, min_length=1, max_length=500)
    access_checks: list["IntakeAccess"] = Field(default_factory=list, max_length=8)
    expectations: list["IntakeExpectation"] = Field(default_factory=list, max_length=6)
    credential_inputs: dict[str, str] = Field(default_factory=dict)
    upload: CSVUpload | None = None
    poll: PollPolicy | None = None
    creation: ResourceCreation | None = None
    public_evidence: list[str] = Field(default_factory=list, max_length=30)
    after_operations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("credential_inputs")
    @classmethod
    def private_aliases(cls, value):
        from agenttest.controlled_inputs import credential_alias

        for alias in value.values():
            credential_alias(alias)
        return value

    @model_serializer(mode="wrap")
    def legacy_defaults(self, handler):
        data = handler(self)
        if not self.access_checks:
            data.pop("access_checks", None)
        if not self.expectations:
            data.pop("expectations", None)
        if not self.credential_inputs:
            data.pop("credential_inputs", None)
        for key in ("upload", "poll", "check_source", "creation"):
            if getattr(self, key) is None:
                data.pop(key, None)
        if not self.public_evidence:
            data.pop("public_evidence", None)
        if not self.after_operations:
            data.pop("after_operations", None)
        return data


class IntakeExpectation(StrictModel):
    """An operator-approved non-2xx expectation for the same operation and identity."""

    statuses: list[int] = Field(min_length=1, max_length=4)
    rule: str = Field(min_length=1, max_length=160)
    arguments: list[IntakeArgument] = Field(default_factory=list, max_length=6)

    @model_serializer(mode="wrap")
    def legacy_defaults(self, handler):
        data = handler(self)
        if not self.arguments:
            data.pop("arguments", None)
        return data


class IntakeAccess(StrictModel):
    role: str | None = Field(min_length=1, max_length=40)
    statuses: list[int] = Field(min_length=1, max_length=10)
    rule: str = Field(min_length=1, max_length=160)
    source_question: str | None = Field(default=None, pattern=r"^question_[a-f0-9]{20}$")

    @model_serializer(mode="wrap")
    def legacy_defaults(self, handler):
        data = handler(self)
        if self.source_question is None:
            data.pop("source_question", None)
        return data


IntakeOperation.model_rebuild()


class IntakeRole(StrictModel):
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    bearer_env: str | None = None
    login_operation: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    username_field: str = "username"
    password_field: str = "password"
    token_selector: str = "$.access_token"

    @field_validator("bearer_env", "username_env", "password_env")
    @classmethod
    def credential_name(cls, value):
        import re

        if value is not None and (not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value)
                or not value.startswith("AGENTTEST_TARGET_")):
            raise ValueError("TARGET_CREDENTIAL_ALIAS_REQUIRED")
        return value


class GenericSetup(StrictModel):
    base_url: str = Field(min_length=1, max_length=200)
    isolated_confirmed: bool = False
    allow_mutations: bool = False
    max_requests: int = Field(default=40, ge=1, le=64)
    run_timeout: float = Field(default=60.0, gt=0, le=300)
    read_timeout: float = Field(default=5.0, gt=0, le=30)
    roles: list[IntakeRole] = Field(default_factory=list, max_length=8)
    operations: list[IntakeOperation] = Field(min_length=1, max_length=1000)
    assisted: bool = False
    approve_negative_tests: bool = False
    allow_negative_value_variants: bool = False
    default_role: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    resource_lifecycles: list[ResourceLifecycle] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_identities(self):
        aliases = [role.alias for role in self.roles]
        if len(aliases) != len(set(aliases)):
            raise ValueError("DUPLICATE_ROLE")
        if self.allow_negative_value_variants and not (
                self.assisted and self.approve_negative_tests):
            raise ValueError("NEGATIVE_VARIANTS_REQUIRE_CONTROLLED_TEST_APPROVAL")
        return self

    @model_serializer(mode="wrap")
    def legacy_defaults(self, handler):
        data = handler(self)
        for key in ("assisted", "approve_negative_tests", "allow_negative_value_variants",
                    "default_role", "resource_lifecycles"):
            if not getattr(self, key):
                data.pop(key, None)
        for key, default in (("run_timeout", 60.0), ("read_timeout", 5.0)):
            if getattr(self, key) == default:
                data.pop(key, None)
        return data


def effective_setup(contract: ContractSummary, setup: GenericSetup) -> GenericSetup:
    """Apply the operator's single default identity without changing the saved declaration."""
    if not setup.assisted or setup.default_role is None:
        return setup
    result = setup.model_copy(deep=True)
    operations = {op.operation_id: op for op in contract.operations}
    default = next((r for r in result.roles if r.alias == result.default_role), None)
    for item in result.operations:
        op = operations.get(item.operation_id)
        if item.role is None and op and (op.security and {} not in op.security
                or default and default.login_operation == item.operation_id):
            item.role = result.default_role
    return result


def analyze_contract(contract: ContractSummary):
    """Suggestions only: parsing neither approves a target nor asserts business meaning."""
    suggestions = []
    links = linked_inputs(contract)
    for op in contract.operations:
        parameters, reason = [], None
        try:
            if not op.operation_id:
                raise ValueError("接口文档缺少 operationId，请补齐后重新上传。")
            if op.method == "TRACE":
                raise ValueError("TRACE 不在允许的 HTTP 方法范围内。")
            for spec in input_fields(op, contract.version):
                relation = links.get((op.operation_id, spec.name))
                credential_input = credential_field(spec)
                parameters.append(dict(location=spec.location, name=spec.name,
                    value=None if relation or credential_input else sample(spec.schema),
                    source_operation=relation[0] if relation else None,
                    selector=relation[1] if relation else "$",
                    generated=not relation and spec.location != "path_parameters",
                    needs_input=credential_input or not relation
                        and spec.location == "path_parameters"
                        and "example" not in spec.schema and "default" not in spec.schema
                        and "enum" not in spec.schema,
                    value_type=spec.schema.get("type"), required=spec.required,
                    schema=spec.schema, basis=spec.basis,
                    dependency_basis=relation[2] if relation else None))
        except ValueError as exc:
            code = getattr(getattr(exc, "diagnostic", None), "code", str(exc))
            reason = ({"UNSUPPORTED_REQUEST_SCHEMA": "输入包含暂未支持的组合或自定义约束。",
                "UNSUPPORTED_REQUEST_PATTERN":
                    "参数的格式规则暂未支持；当前支持文字候选、简单字符范围及其长度限制。",
                "UNSUPPORTED_REQUEST_FORMAT": "输入格式暂未支持，请查看格式约束。",
                "ONLY_OBJECT_REQUEST_BODIES_SUPPORTED":
                    "请求体根节点暂需为 JSON 对象；对象内可嵌套数组。",
                "OPEN_OBJECT_SCHEMA_NOT_SUPPORTED":
                    "任意键对象暂未支持，需提供明确字段。"}.get(code, code))
        statuses = sorted(int(k) for k in op.responses if k.isdigit() and 200 <= int(k) < 300)
        if not statuses:
            reason = reason or "文档没有明确的成功状态码，请先补齐。"
        if op.security and {} not in op.security and not any(
                requirement and all(contract.security_schemes.get(name, {}).get("type") == "http"
                    and contract.security_schemes[name].get("scheme", "").lower() == "bearer"
                    for name in requirement) for requirement in op.security):
            reason = "当前通用接入仅支持匿名或 HTTP Bearer 鉴权，可配置登录接口取得 Bearer。"
        suggestions.append(dict(operation_id=op.operation_id, method=op.method, path=op.path,
            summary=op.summary, supported=reason is None, reason=reason,
            alias=op.operation_id or op.method + " " + op.path,
            enabled=False, role=None, statuses=statuses, arguments=parameters,
            checks=response_checks(op), response_fields=response_fields(op),
            basis=f"OpenAPI {op.method} {op.path} responses / parameters / requestBody",
            authentication_required=bool(op.security) and {} not in op.security))
        try:
            file_field = upload_field(op)
        except ValueError:
            file_field = None
        if file_field:
            suggestions[-1]["upload_field"] = file_field
    return suggestions


def build_generic_project(key, document, contract, setup, authorized_ips=(), environ=None):
    """Only the explicit local operator confirmation may activate this configuration."""
    if not setup.isolated_confirmed:
        raise ValueError("ISOLATED_ENVIRONMENT_CONFIRMATION_REQUIRED")
    setup = effective_setup(contract, setup)
    selected = [op for op in setup.operations if op.enabled]
    if setup.assisted:
        ready = {e["operation_id"] for e in inventory(contract, setup, environ)["operations"]
                 if e["state"] == "READY"}
        selected = [op for op in selected if op.operation_id in ready]
    ids = [op.operation_id for op in selected]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("SELECT_UNIQUE_OPERATIONS")
    operations = {op.operation_id: op for op in contract.operations if op.operation_id}
    if set(ids) - operations.keys():
        raise ValueError("CONTRACT_OPERATION_UNKNOWN")
    for item in selected:
        validate_csv_assertions(operations[item.operation_id], item.statuses, item.checks)
    roles = {}
    for role in setup.roles:
        if setup.assisted and environ is not None and not identity_ready(role, environ):
            continue
        if setup.assisted and not any(item.role == role.alias or any(
                access.role == role.alias for access in item.access_checks) for item in selected):
            continue
        if role.alias in roles:
            raise ValueError("DUPLICATE_ROLE")
        if role.bearer_env:
            if role.login_operation or role.username_env or role.password_env:
                raise ValueError("AMBIGUOUS_AUTHENTICATION")
        elif not (role.login_operation in ids and role.username_env and role.password_env):
            raise ValueError("LOGIN_CONFIGURATION_REQUIRED")
        roles[role.alias] = RoleCredential.model_validate(role.model_dump(exclude={"alias"}))
    scenario = "web_contract_v2" if setup.assisted else "web_contract_v1"
    try:
        target = TargetConfig(base_url=setup.base_url, environment="isolated",
            authorized_ips=list(authorized_ips), allow_mutations=setup.allow_mutations,
            allowed_operations=ids, roles=roles, business_scenario=scenario,
            max_requests=setup.max_requests, resource_lifecycles=setup.resource_lifecycles,
            run_timeout=setup.run_timeout, read_timeout=setup.read_timeout)
        target.credential_inputs = {item.operation_id: item.credential_inputs
                                    for item in selected if item.credential_inputs}
        target.resource_creations = {item.operation_id: item.creation
                                     for item in selected if item.creation}
        target = TargetConfig.model_validate(target.model_dump())
    except ValueError:
        raise ValueError("TARGET_AUTHORIZATION_REQUIRED") from None
    from agenttest.lifecycle import validate_lifecycle_authority

    validate_lifecycle_authority(contract, target)
    policy_ids = {op.operation_id: f"web_rule_{i + 1}" for i, op in enumerate(selected)}
    selected_by_id = {op.operation_id: op for op in selected}
    if any(len(set(item.after_operations)) != len(item.after_operations)
           or any(parent not in selected_by_id or parent == item.operation_id
                  for parent in item.after_operations) for item in selected):
        raise ValueError("EXECUTION_PREREQUISITE_NOT_SELECTED")
    for alias, role in roles.items():
        if role.login_operation:
            primary = selected_by_id[role.login_operation].role
            if (primary not in roles or roles[primary].login_operation != role.login_operation
                    or not setup.assisted and primary != alias):
                raise ValueError("LOGIN_IDENTITY_MISMATCH")
    source = "WEB_OPERATOR_CONFIRMED:" + fingerprint(setup.model_dump())
    policies, goals = [], []
    for index, item in enumerate(selected):
        op = operations[item.operation_id]
        role = roles.get(item.role)
        is_login = bool(role and role.login_operation == item.operation_id and op.method == "POST")
        if item.creation:
            validate_selector(item.creation.id_selector)
            validate_user_task(item.creation.fresh_resource_basis)
            if is_login or op.method != "POST" or item.statuses != [201]:
                raise ValueError("FRESH_CREATION_REQUIRES_POST_201")
        if (op.method not in {"GET", "HEAD", "OPTIONS"}
                and not setup.allow_mutations and not is_login):
            raise ValueError("WRITE_AUTHORIZATION_REQUIRED")
        target.permit(item.operation_id, op.method)
        validate_user_task(item.alias)
        if item.check_source:
            validate_user_task(item.check_source)
        if item.role is not None and item.role not in roles:
            raise ValueError("ROLE_NOT_CONFIGURED")
        if op.security and {} not in op.security and item.role is None:
            raise ValueError("PROTECTED_OPERATION_REQUIRES_ROLE")
        specs = {} if is_login else {(s.location, s.name): s
            for s in input_fields(op, contract.version)}
        file_field = upload_field(op)
        if item.upload and (not file_field or item.upload.field != file_field):
            raise ValueError("UPLOAD_NOT_DECLARED_IN_CONTRACT")
        if file_field and not item.upload:
            raise ValueError("CSV_FIXTURE_REQUIRED")
        if item.poll and (op.method not in {"GET", "HEAD"} or item.upload):
            raise ValueError("POLL_REQUIRES_READ_OPERATION")
        if item.poll and not any(check.select == item.poll.select
                and check.operator in {"equals", "in"}
                and (check.expected in item.poll.terminal_values if check.operator == "equals"
                     else bool(check.expected) and all(v in item.poll.terminal_values
                                                       for v in check.expected))
                for check in item.checks):
            raise ValueError("POLL_TERMINAL_ASSERTION_REQUIRED")
        from agenttest.controlled_inputs import controlled_payload

        if is_login and item.credential_inputs:
            raise ValueError("LOGIN_CREDENTIALS_ALREADY_MANAGED")
        controlled_payload(op, item.credential_inputs, environ)
        if any(credential_field(spec) and not (
                spec.location == "body" and spec.name in item.credential_inputs)
                for spec in specs.values()):
            raise ValueError("SENSITIVE_INPUT_BINDING_REQUIRED")
        specs = {slot: spec for slot, spec in specs.items()
                 if not (slot[0] == "body" and slot[1] in item.credential_inputs)}
        rules, seen = [], set()
        for argument in ([] if is_login else item.arguments):
            slot = argument.location, argument.name
            if argument.location == "body" and argument.name in item.credential_inputs:
                if (argument.value is not None or argument.source_operation
                        or argument.generated or argument.negative_enabled):
                    raise ValueError("CONTROLLED_INPUT_CANNOT_HAVE_PUBLIC_VALUE")
                continue
            if slot not in specs or slot in seen:
                raise ValueError("ARGUMENT_NOT_IN_CONTRACT")
            if argument.negative_enabled and (argument.location == "path_parameters"
                    or argument.source_operation or credential_field(specs[slot])):
                raise ValueError("NEGATIVE_FIELD_NOT_ELIGIBLE")
            seen.add(slot)
            if argument.source_operation:
                producer = selected_by_id.get(argument.source_operation)
                if producer is None or producer.operation_id == item.operation_id:
                    raise ValueError("RESOURCE_SOURCE_NOT_SELECTED")
                if argument.value is not None:
                    raise ValueError("RESOURCE_SOURCE_REQUIRES_NULL_LITERAL")
                if any(r.login_operation == producer.operation_id for r in roles.values()):
                    raise ValueError("AUTHENTICATION_RESPONSE_IS_NOT_A_BUSINESS_RESOURCE")
                relation = ("anonymous" if producer.role is None and item.role is None else
                    "anonymous_source" if producer.role is None and item.role is not None else
                    "same" if producer.role == item.role and item.role is not None else
                    "different" if producer.role is not None and item.role is not None else None)
                if relation is None:
                    raise ValueError("RESOURCE_ROLE_RELATION_REQUIRED")
                rules.append(BindingRule(location=argument.location, name=argument.name,
                    source_policy_id=policy_ids[producer.operation_id],
                    source_selector=argument.selector, role_relation=relation))
            else:
                if argument.values is not None:
                    # Approved alternatives keep a generated input inside an operator-owned set.
                    if (not argument.generated or argument.value is not None
                            or argument.negative_enabled):
                        raise ValueError("APPROVED_VALUES_REQUIRE_GENERATED_INPUT")
                    for candidate in argument.values:
                        validate_value(candidate, specs[slot].schema)
                    if len({fingerprint(value) for value in argument.values}) != len(
                            argument.values):
                        raise ValueError("APPROVED_VALUES_NOT_DISTINCT")
                    rules.append(BindingRule(location=argument.location, name=argument.name,
                        literal_schema={**specs[slot].schema,
                                        "enum": list(argument.values)}))
                    continue
                validate_value(argument.value, specs[slot].schema)
                # Explicit values stay fixed; generated inputs remain inside the approved schema.
                rules.append(BindingRule(location=argument.location, name=argument.name,
                    literal_schema=(specs[slot].schema if argument.generated else
                                    {**specs[slot].schema, "enum": [argument.value]})))
        if any(spec.required and slot not in seen for slot, spec in specs.items()):
            raise ValueError("REQUIRED_ARGUMENT_MISSING")
        base_policy = RegisteredPolicy(id=policy_ids[item.operation_id],
            operation_id=item.operation_id, roles=[item.role], business_scenario=scenario,
            assertions=AssertionPolicy(reviewed=True, source=source + (
                ":" + item.check_source if item.check_source else ""),
                status=ExpectedStatus(codes=item.statuses), fields=item.checks,
                public_evidence=item.public_evidence),
            binding_rules=rules, closed_arguments=True, poll=item.poll,
            prerequisite_policy_ids=[policy_ids[parent] for parent in item.after_operations],
            csv_fixture=(ApprovedCSVFixture(id="csv_" + fingerprint(item.upload.model_dump())[:20],
                classification="DEVELOPMENT_ONLY", upload=item.upload) if item.upload else None))
        policies.append(base_policy)
        if setup.assisted and is_login:
            for alias, configured in roles.items():
                if alias != item.role and configured.login_operation == item.operation_id:
                    policies.append(base_policy.model_copy(update={
                        "id": base_policy.id + "_identity_" + alias, "roles": [alias],
                        "planning_purpose": "登录测试身份 " + alias}))
        if setup.assisted and setup.approve_negative_tests and not is_login:
            from agenttest.intake_variations import negative_capabilities

            negative_status = [int(code) for code in op.responses if code in {"400", "422"}]
            if negative_status:
                for number, approval in enumerate(negative_capabilities(
                        base_policy, op, specs, item.arguments)):
                    if setup.allow_negative_value_variants:
                        approval = approval.model_copy(update={"allow_value_variants": True})
                    policies.append(base_policy.model_copy(update={
                        "id": base_policy.id + f"_negative_{number + 1}", "negative": approval,
                        "assertions": AssertionPolicy(reviewed=True,
                            source=source + ":OPENAPI_INVALID_INPUT_RESPONSE",
                            status=ExpectedStatus(codes=negative_status)),
                        "planning_purpose": f"参数 {approval.name}：{approval.kind}"}))
        for number, access in enumerate(item.access_checks):
            if setup.assisted and access.role not in roles and any(
                    r.alias == access.role for r in setup.roles):
                continue  # Pending credentials are visible in the complete inventory.
            readonly = op.method in {"GET", "HEAD", "OPTIONS"}
            if not readonly and not (
                    # A mutation may only carry a pre-approved identity denial (401/403);
                    # ordinary write permission is never widened here.
                    setup.approve_negative_tests and set(access.statuses) <= {401, 403}
                    and access.role != item.role):
                raise ValueError("ACCESS_CHECK_REQUIRES_APPROVED_READONLY_ROLE")
            if access.role is not None and access.role not in roles:
                raise ValueError("ACCESS_CHECK_REQUIRES_APPROVED_READONLY_ROLE")
            if access.role is None and (access.statuses != [401] or "401" not in op.responses
                    or not op.security or {} in op.security):
                raise ValueError("UNAUTHENTICATED_CHECK_REQUIRES_DOCUMENTED_401")
            access_rules = []
            for rule in rules:
                copied = rule.model_copy(deep=True)
                if copied.source_policy_id:
                    producer_id = next(k for k, v in policy_ids.items()
                                       if v == copied.source_policy_id)
                    producer_role = selected_by_id[producer_id].role
                    if producer_role is None:
                        raise ValueError("ACCESS_CHECK_REQUIRES_NAMED_RESOURCE_OWNER")
                    copied.role_relation = ("unauthenticated" if access.role is None else
                        "same" if producer_role == access.role else "different")
                copied.source_body_equalities = {}
                copied.response_selector = None
                access_rules.append(copied)
            validate_user_task(access.rule)
            access_id = base_policy.id + f"_access_{number + 1}"
            if access.role is None:
                target.unauthenticated_checks.setdefault(item.operation_id, []).append(access_id)
            policies.append(base_policy.model_copy(update={
                "id": access_id, "roles": [access.role], "poll": None, "csv_fixture": None,
                "binding_rules": access_rules, "planning_purpose": access.rule,
                "assertions": AssertionPolicy(reviewed=True,
                    source=source + ":OPERATOR_ACCESS_RULE",
                    status=ExpectedStatus(codes=access.statuses))}))
        for number, expectation in enumerate(item.expectations):
            # Expected denial/conflict outcomes are operator-authored, documented and bounded;
            # they never extend the approved success statuses of the base operation.
            if not (setup.assisted and setup.approve_negative_tests):
                raise ValueError("EXPECTATION_REQUIRES_APPROVED_NEGATIVE_TESTS")
            if not set(expectation.statuses) <= NON_SUCCESS_EXPECTATIONS:
                raise ValueError("EXPECTATION_STATUS_NOT_APPROVABLE")
            if any(str(code) not in op.responses for code in expectation.statuses):
                raise ValueError("EXPECTATION_STATUS_NOT_DOCUMENTED")
            expectation_rules = [rule.model_copy(deep=True) for rule in rules]
            approved_slots = {(rule.location, rule.name): rule for rule in expectation_rules}
            for argument in expectation.arguments:
                slot = argument.location, argument.name
                base_rule = approved_slots.get(slot)
                if (base_rule is None or argument.source_operation or argument.generated
                        or argument.values is not None or argument.negative_enabled):
                    raise ValueError("EXPECTATION_ARGUMENT_NOT_APPROVED")
                approved = (base_rule.literal_schema or {}).get("enum") or []
                if not any(json_equal(argument.value, value) for value in approved):
                    raise ValueError("EXPECTATION_VALUE_NOT_APPROVED")
                base_rule.literal_schema = {**(base_rule.literal_schema or {"type": "string"}),
                                            "enum": [argument.value]}
            validate_user_task(expectation.rule)
            policies.append(base_policy.model_copy(update={
                "id": base_policy.id + f"_expectation_{number + 1}", "poll": None,
                "csv_fixture": None, "binding_rules": expectation_rules,
                "planning_purpose": expectation.rule,
                "assertions": AssertionPolicy(reviewed=True,
                    source=source + ":OPERATOR_EXPECTATION_RULE",
                    status=ExpectedStatus(codes=expectation.statuses))}))
        goals.append(GoalTemplate(id=f"action_{index + 1}", aliases=[item.alias],
            policy_id=policy_ids[item.operation_id], role=item.role))
    # Reject cycles before a task can spend model or HTTP budget.
    edges = {item.operation_id: [*item.after_operations, *[a.source_operation
             for a in item.arguments if a.source_operation]] for item in selected}

    def visit(node, active, done):
        if node in active:
            raise ValueError("RESOURCE_DEPENDENCY_CYCLE")
        if node not in done:
            for parent in edges[node]:
                visit(parent, active | {node}, done)
            done.add(node)

    done = set()
    for node in edges:
        visit(node, set(), done)
    registration = RegisteredProject(schema_version="agenttest.registered-project.v1",
        id=key, contract="openapi.json", target="target.json", policies="policies.json",
        goals="goals.json", budget=(ProjectBudget(max_input_tokens=60_000,
            max_output_tokens=8192, max_total_tokens=70_000, suite_max_model_calls=9,
            suite_max_total_tokens=500_000)
            if setup.assisted else ProjectBudget()))
    return GoalProject(registration, document.encode(), contract, target,
        PolicyRegistry(policies), GoalCatalog(resource_aliases=["本项目"], goals=goals))
