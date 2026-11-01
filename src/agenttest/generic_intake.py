"""Build a project from an operator-reviewed contract, never from model authority."""

from typing import Any, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from agenttest.contracts import ContractSummary
from agenttest.dsl import AssertionPolicy, ExpectedStatus, FieldAssertion
from agenttest.planner_input import validate_user_task
from agenttest.policies import BindingRule, PolicyRegistry, RegisteredPolicy
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
)
from agenttest.report import fingerprint
from agenttest.request_validation import validate_value
from agenttest.safety import ResourceLifecycle, RoleCredential, StrictModel, TargetConfig
from agenttest.selectors import validate_selector


class IntakeArgument(StrictModel):
    location: Literal["body", "path_parameters", "query"]
    name: str = Field(min_length=1, max_length=80)
    value: Any = None
    source_operation: str | None = None
    selector: str = "$"
    generated: bool = False
    needs_input: bool = False

    @model_serializer(mode="wrap")
    def legacy_defaults(self, handler):
        data = handler(self)
        for key in ("generated", "needs_input"):
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
    access_checks: list["IntakeAccess"] = Field(default_factory=list, max_length=8)

    @model_serializer(mode="wrap")
    def legacy_defaults(self, handler):
        data = handler(self)
        if not self.access_checks:
            data.pop("access_checks", None)
        return data


class IntakeAccess(StrictModel):
    role: str = Field(min_length=1, max_length=40)
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
                "UNSUPPORTED_REQUEST_FORMAT": "输入格式暂未支持，请查看格式约束。",
                "ONLY_OBJECT_REQUEST_BODIES_SUPPORTED":
                    "请求体根节点暂需为 JSON 对象；对象内可嵌套数组。",
                "OPEN_OBJECT_SCHEMA_NOT_SUPPORTED":
                    "任意键对象暂未支持，需提供明确字段。"}.get(code, code))
        statuses = sorted(int(k) for k in op.responses if k.isdigit() and 200 <= int(k) < 300)
        if not statuses:
            reason = reason or "文档没有明确的成功状态码，请先补齐。"
        if statuses == [202]:
            reason = "长异步任务的完成条件和轮询尚未接入此产品流程。"
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
            max_requests=setup.max_requests, resource_lifecycles=setup.resource_lifecycles)
    except ValueError:
        raise ValueError("TARGET_AUTHORIZATION_REQUIRED") from None
    from agenttest.lifecycle import validate_lifecycle_authority

    validate_lifecycle_authority(contract, target)
    policy_ids = {op.operation_id: f"web_rule_{i + 1}" for i, op in enumerate(selected)}
    selected_by_id = {op.operation_id: op for op in selected}
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
        if (op.method not in {"GET", "HEAD", "OPTIONS"}
                and not setup.allow_mutations and not is_login):
            raise ValueError("WRITE_AUTHORIZATION_REQUIRED")
        target.permit(item.operation_id, op.method)
        validate_user_task(item.alias)
        if item.role is not None and item.role not in roles:
            raise ValueError("ROLE_NOT_CONFIGURED")
        if op.security and {} not in op.security and item.role is None:
            raise ValueError("PROTECTED_OPERATION_REQUIRES_ROLE")
        specs = {} if is_login else {(s.location, s.name): s
            for s in input_fields(op, contract.version)}
        rules, seen = [], set()
        for argument in ([] if is_login else item.arguments):
            slot = argument.location, argument.name
            if slot not in specs or slot in seen:
                raise ValueError("ARGUMENT_NOT_IN_CONTRACT")
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
                    "same" if producer.role == item.role and item.role is not None else
                    "different" if producer.role is not None and item.role is not None else None)
                if relation is None:
                    raise ValueError("RESOURCE_ROLE_RELATION_REQUIRED")
                rules.append(BindingRule(location=argument.location, name=argument.name,
                    source_policy_id=policy_ids[producer.operation_id],
                    source_selector=argument.selector, role_relation=relation))
            else:
                validate_value(argument.value, specs[slot].schema)
                # Explicit values stay fixed; generated inputs remain inside the approved schema.
                rules.append(BindingRule(location=argument.location, name=argument.name,
                    literal_schema=(specs[slot].schema if argument.generated else
                                    {**specs[slot].schema, "enum": [argument.value]})))
        if any(spec.required and slot not in seen for slot, spec in specs.items()):
            raise ValueError("REQUIRED_ARGUMENT_MISSING")
        base_policy = RegisteredPolicy(id=policy_ids[item.operation_id],
            operation_id=item.operation_id, roles=[item.role], business_scenario=scenario,
            assertions=AssertionPolicy(reviewed=True, source=source,
                status=ExpectedStatus(codes=item.statuses), fields=item.checks),
            binding_rules=rules, closed_arguments=True)
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
            if op.method not in {"GET", "HEAD", "OPTIONS"} or access.role not in roles:
                raise ValueError("ACCESS_CHECK_REQUIRES_APPROVED_READONLY_ROLE")
            access_rules = []
            for rule in rules:
                copied = rule.model_copy(deep=True)
                if copied.source_policy_id:
                    producer_id = next(k for k, v in policy_ids.items()
                                       if v == copied.source_policy_id)
                    producer_role = selected_by_id[producer_id].role
                    if producer_role is None:
                        raise ValueError("ACCESS_CHECK_REQUIRES_NAMED_RESOURCE_OWNER")
                    copied.role_relation = "same" if producer_role == access.role else "different"
                access_rules.append(copied)
            validate_user_task(access.rule)
            policies.append(base_policy.model_copy(update={
                "id": base_policy.id + f"_access_{number + 1}", "roles": [access.role],
                "binding_rules": access_rules, "planning_purpose": access.rule,
                "assertions": AssertionPolicy(reviewed=True,
                    source=source + ":OPERATOR_ACCESS_RULE",
                    status=ExpectedStatus(codes=access.statuses))}))
        goals.append(GoalTemplate(id=f"action_{index + 1}", aliases=[item.alias],
            policy_id=policy_ids[item.operation_id], role=item.role))
    # Reject cycles before a task can spend model or HTTP budget.
    edges = {item.operation_id: [a.source_operation for a in item.arguments
             if a.source_operation] for item in selected}

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
