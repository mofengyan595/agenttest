"""Complete contract inventory and bounded suggestions, never execution authority."""

import re

from agenttest.discovery_schema import FieldSpec, versioned_schema, witness
from agenttest.request_validation import request_schemas, validate_value
from agenttest.selectors import validate_selector


def credential_field(spec):
    return (spec.schema.get("format") == "password" or bool(re.search(
        r"password|passwd|^pwd$|token|api.?key|authorization|cookie", spec.name, re.I)))


def sample(schema, depth=0):
    """Generate one bounded valid JSON example, including nested objects and arrays."""
    if depth > 12:
        raise ValueError("INPUT_DEPTH_LIMIT")
    for key in ("example", "default"):
        if key in schema:
            validate_value(schema[key], schema)
            return schema[key]
    if schema.get("enum"):
        validate_value(schema["enum"][0], schema)
        return schema["enum"][0]
    if schema.get("type") == "object":
        value = {k: sample(s, depth + 1) for k, s in schema.get("properties", {}).items()
                 if not s.get("readOnly")}
    elif schema.get("type") == "array":
        size = max(1, schema.get("minItems", 0))
        size = min(size, schema.get("maxItems", size))
        if size > 8:
            raise ValueError("INPUT_SAMPLE_SIZE_LIMIT")
        value = [sample(schema["items"], depth + 1) for _ in range(size)]
    elif schema.get("type") == "null":
        value = None
    else:
        return witness(schema)
    validate_value(value, schema)
    return value


def input_fields(op, version):
    """Keep historical discovery conservative; generic intake supports nested JSON."""
    content = (op.request_body or {}).get("content", {})
    if content and "application/json" not in content:
        raise ValueError("文件上传或非 JSON 请求暂未接入此产品流程。")
    parameters, body = request_schemas(op, "application/json" if content else None)
    result = []
    for location, entries in parameters.items():
        for name, schema in entries.items():
            versioned_schema(schema, version)
            required = next(p.get("required", False) for p in op.parameters
                            if p["in"] == location and p["name"] == name)
            result.append(FieldSpec("path_parameters" if location == "path" else "query",
                name, schema, required, f"{op.method} {op.path} 参数 {name}", "OpenAPI required"))
    if body is not None:
        versioned_schema(body, version)
        for name, schema in body.get("properties", {}).items():
            if not schema.get("readOnly"):
                result.append(FieldSpec("body", name, schema,
                    name in body.get("required", []), f"{op.method} {op.path} 请求字段 {name}",
                    "OpenAPI required"))
    if len(result) > 30:
        raise ValueError("单个接口一次计划最多支持 30 项输入；请拆分该接口的输入范围。")
    return result


def linked_inputs(contract):
    """Resolve explicit OpenAPI response links only; suggestions need operator approval."""
    result = {}
    for source in contract.operations:
        for response in source.responses.values():
            for link in response.get("links", {}).values():
                target = link.get("operationId")
                for name, expression in link.get("parameters", {}).items():
                    if not isinstance(expression, str) or not expression.startswith(
                            "$response.body#/"):
                        continue
                    parts = expression[len("$response.body#/"):].split("/")
                    selector = "$" + "".join("." + p.replace("~1", "/").replace("~0", "~")
                                              for p in parts)
                    try:
                        validate_selector(selector)
                    except ValueError:
                        continue
                    result.setdefault((target, name), []).append((
                        source.operation_id, selector, "OpenAPI response links"))
    inferred = {key: values[0] for key, values in result.items() if len(values) == 1}
    for consumer in contract.operations:
        for parameter in consumer.parameters:
            key = consumer.operation_id, parameter["name"]
            if parameter["in"] != "path" or key in result:
                continue
            marker = "{" + parameter["name"] + "}"
            prefix = consumer.path.split(marker)[0].rstrip("/")
            if marker not in consumer.path:
                continue
            candidates = {method: [] for method in ("POST", "GET")}
            for producer in contract.operations:
                if producer.path != prefix or producer.method not in candidates:
                    continue
                for field in response_fields(producer):
                    leaf = field["selector"].rsplit(".", 1)[-1].lower().replace("_", "")
                    requested = parameter["name"].lower().replace("_", "")
                    if (leaf not in {requested, "id", "key"}
                            or field["value_type"] != parameter.get("schema", {}).get("type")):
                        continue
                    candidates[producer.method].append((producer.operation_id,
                        field["selector"], "文档结构建议：同一资源路径与兼容标识字段，需确认"))
            choices = candidates["POST"] or candidates["GET"]
            if len(choices) == 1:
                inferred[key] = choices[0]
    return inferred


def operation_reason(op, item, setup, environ=None, version="3.0.3"):
    """Return concrete readiness for each operation; partial blocks stay local."""
    if not item or not item.enabled:
        return "UNAUTHORIZED", "尚未选择并确认此接口。", "在项目中选择需要执行的接口。"
    if not setup.isolated_confirmed:
        return "UNAUTHORIZED", "测试环境尚未确认。", "填写地址并确认隔离环境授权。"
    role = next((r for r in setup.roles if r.alias == item.role), None)
    is_login = bool(role and role.login_operation == op.operation_id and op.method == "POST")
    if op.method not in {"GET", "HEAD", "OPTIONS"} and not setup.allow_mutations and not is_login:
        return ("UNAUTHORIZED", "尚未授权创建、修改或删除测试数据。",
                "确认隔离数据范围后开启写入授权。")
    if op.security and {} not in op.security and role is None:
        return ("NEEDS_INPUT", "文档要求身份认证，尚未选择测试身份。",
                "添加测试身份，并为此接口选择该身份。")
    if item.role and role is None:
        return "NEEDS_INPUT", "所选身份未配置。", "补充测试身份。"
    if role and environ is not None:
        if not identity_ready(role, environ):
            return ("NEEDS_INPUT", "所选身份的账号或令牌尚未在服务端配置。",
                    "配置凭据环境变量并重新检查。")
    for argument in ([] if is_login else item.arguments):
        if argument.needs_input:
            if any(s.name == argument.name and credential_field(s)
                   for s in input_fields(op, version)):
                return ("NEEDS_INPUT", "此接口需要测试登录身份，不能用生成值代替凭据。",
                        "添加登录身份，由执行器从服务端环境变量读取账号与密码。")
            return "NEEDS_INPUT", f"{argument.name} 缺少可用资源或明确输入。", (
                f"请提供 {argument.name} 的测试值，或确认从哪个前置接口取得。")
    if not is_login:
        try:
            specs = {(s.location, s.name): s for s in input_fields(op, version)}
            supplied = {(a.location, a.name): a for a in item.arguments}
            for slot, spec in specs.items():
                if spec.required and slot not in supplied:
                    return "NEEDS_INPUT", f"必填参数 {spec.name} 尚未提供。", f"补充 {spec.name}。"
            for slot, argument in supplied.items():
                if slot not in specs:
                    return ("NEEDS_INPUT", f"参数 {argument.name} 不在当前文档中。",
                            "重新读取文档并核对输入。")
                if not argument.source_operation:
                    validate_value(argument.value, specs[slot].schema)
        except ValueError:
            return "NEEDS_INPUT", "输入值不符合当前文档约束。", "按字段类型、枚举或边界修正输入。"
    return "READY", "已具备文档检查依据和执行授权。", "可以开始测试。"


def identity_ready(role, environ):
    names = [role.bearer_env] if role.bearer_env else [role.username_env, role.password_env]
    return all(n and environ.get(n) and not any(c in environ[n] for c in "\r\n") for n in names)


def inventory(contract, setup, environ=None):
    from agenttest.generic_intake import analyze_contract, effective_setup

    setup = effective_setup(contract, setup)
    suggestions = analyze_contract(contract)
    configured = {item.operation_id: item for item in setup.operations}
    entries = []
    for op, suggestion in zip(contract.operations, suggestions, strict=True):
        state, reason, next_step = (operation_reason(op, configured.get(op.operation_id), setup,
            environ, contract.version) if suggestion["supported"] else (
                "NEEDS_INPUT" if not op.operation_id or not suggestion["statuses"]
                else "UNSUPPORTED", suggestion["reason"],
                "补齐文档中的操作标识与成功预期后重新读取。"
                if not op.operation_id or not suggestion["statuses"] else
                "保留在清单中，使用专用工具或补充支持。"))
        entries.append(dict(operation_id=op.operation_id, method=op.method, path=op.path,
            summary=op.summary, group=op.path.strip("/").split("/")[0] or "根路径",
            state=state, reason=reason, next_step=next_step))
    # Dependencies with a blocked producer cannot be ready even if their own data is configured.
    for _ in range(len(entries)):
        states = {e["operation_id"]: e["state"] for e in entries}
        changed = False
        for entry in entries:
            item = configured.get(entry["operation_id"])
            blocked = next((a.source_operation for a in item.arguments
                if a.source_operation and states.get(a.source_operation) != "READY"),
                None) if item else None
            if item and item.role:
                role = next((r for r in setup.roles if r.alias == item.role), None)
                if role and role.login_operation and role.login_operation != item.operation_id:
                    if states.get(role.login_operation) != "READY":
                        blocked = role.login_operation
            if entry["state"] == "READY" and blocked:
                entry.update(state="NEEDS_INPUT", reason=f"前置接口 {blocked} 尚未就绪。",
                             next_step="先补齐前置接口的授权、身份或输入。")
                changed = True
        if not changed:
            break
    pending = {e["operation_id"] for e in entries if e["state"] == "READY"}
    dependencies = {}
    for key in pending:
        item = configured[key]
        parents = {a.source_operation for a in item.arguments if a.source_operation}
        role = next((r for r in setup.roles if r.alias == item.role), None)
        if role and role.login_operation and role.login_operation != key:
            parents.add(role.login_operation)
        dependencies[key] = parents
    while pending:
        ready = {key for key in pending if not dependencies[key] & pending}
        if not ready:
            break
        pending -= ready
    for entry in entries:
        if entry["operation_id"] in pending:
            entry.update(state="NEEDS_INPUT", reason="资源或登录依赖存在循环。",
                         next_step="移除循环依赖，或提供独立的前置测试资源。")
    pending_checks = []
    for item in setup.operations:
        for check in item.access_checks:
            role = next((r for r in setup.roles if r.alias == check.role), None)
            if role is None or environ is not None and not identity_ready(role, environ):
                pending_checks.append(dict(operation_id=item.operation_id, role=check.role,
                    expected_statuses=check.statuses,
                    confirmed_rule=check.rule, reason="规则已确认，但此身份缺少可用凭据。",
                    next_step="补齐此身份的服务端凭据；其他已授权测试可以继续。"))
    return dict(total=len(entries), counts={s: sum(e["state"] == s for e in entries)
        for s in ("READY", "NEEDS_INPUT", "UNAUTHORIZED", "UNSUPPORTED")}, operations=entries,
        pending_checks=pending_checks)


def response_fields(op):
    """Document field choices for a human-friendly dependency selector."""
    result = []

    def walk(schema, path, label, depth=0):
        if depth > 8 or len(result) >= 50:
            return
        if schema.get("type") == "object":
            for name, child in schema.get("properties", {}).items():
                try:
                    selected = validate_selector(path + "." + name)
                except ValueError:
                    continue
                walk(child, selected, label + name, depth + 1)
        elif schema.get("type") == "array":
            walk(schema.get("items", {}), path + "[0]", label + "第一项的", depth + 1)
        elif path != "$" or schema.get("type") in {"string", "integer", "number", "boolean"}:
            result.append(dict(selector=path, label=label or "整个响应",
                               value_type=schema.get("type")))

    for code, response in op.responses.items():
        if code.isdigit() and 200 <= int(code) < 300:
            walk(response.get("content", {}).get("application/json", {}).get("schema", {}),
                 "$", "")
    return list({r["selector"]: r for r in result}.values())


def response_checks(op):
    """Document-based structural checks only, without invented business expectations."""
    schemas = [r.get("content", {}).get("application/json", {}).get("schema", {})
        for code, r in op.responses.items() if code.isdigit() and 200 <= int(code) < 300]
    if len(schemas) != 1:
        return []
    schema, result = schemas[0], []
    for name in schema.get("required", []):
        try:
            selector = validate_selector("$." + name)
        except ValueError:
            continue
        prop = schema.get("properties", {}).get(name, {})
        result.append(dict(select=selector, operator="exists", expected=None))
        if prop.get("enum"):
            result.append(dict(select=selector, operator="in", expected=prop["enum"]))
    return result[:30]
