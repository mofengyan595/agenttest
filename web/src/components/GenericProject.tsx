import { useEffect, useRef, useState } from "react";
import {
  intakeApi,
  type ContractOperation,
  type GenericOperation,
  type GenericSetup,
  type GenericRole,
} from "../api/tasks";
import { Notice } from "./ui";
import { InputValue } from "./InputValue";

function scalar(text: string): unknown {
  if (text === "true" || text === "false") return text === "true";
  if (/^-?\d+(\.\d+)?$/.test(text)) return Number(text);
  return text;
}

function fieldLabel(selector: string) {
  return selector === "$" ? "整个响应" : selector.replace(/^\$\./, "")
    .replaceAll("[0]", "的第一项").replaceAll(".", " 的 ");
}

function sensitiveInput(name: string, schema?: unknown): boolean {
  if (/password|passwd|^pwd$|token|api.?key|authorization|cookie/i.test(name)) return true;
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) return false;
  const shape = schema as Record<string, unknown>;
  return shape.format === "password" || sensitiveInput("", shape.items) ||
    !!shape.properties && typeof shape.properties === "object" &&
      Object.entries(shape.properties).some(([key, child]) => sensitiveInput(key, child));
}

function loginChoices(operation: ContractOperation | undefined, field: string) {
  return field === "token_selector"
    ? (operation?.response_fields || []).filter((f) => f.value_type === "string")
      .map((f) => ({value: f.selector, label: fieldLabel(f.selector)}))
    : (operation?.arguments || []).filter((a) => a.location === "body" && a.value_type === "string")
      .map((a) => ({value: a.name, label: a.name}));
}

function responseChoices(operation: ContractOperation | undefined, saved: string[] = []) {
  return [...new Set([...(operation?.response_fields || []).map((f) => f.selector),
    ...(operation?.checks || []).map((c) => c.select), ...saved])].filter(Boolean)
    .map((selector) => ({value: selector, label: fieldLabel(selector)}));
}

function suggestLoginFields(operation: ContractOperation | undefined, role: GenericRole) {
  if (!operation) return {};
  const fields = loginChoices(operation, "username_field");
  const password = fields.find((f) => f.value === role.password_field) ||
    fields.find((f) => /password|passwd|^pwd$/i.test(f.value));
  const username = fields.find((f) => f.value === role.username_field) ||
    fields.find((f) => /user.?name|account|login|email|phone/i.test(f.value)) ||
    fields.find((f) => f !== password);
  const tokens = loginChoices(operation, "token_selector");
  const token = tokens.find((f) => f.value === role.token_selector) ||
    tokens.find((f) => /token|jwt/i.test(f.value)) || (tokens.length === 1 ? tokens[0] : undefined);
  return {username_field: username?.value || "", password_field: password?.value || "",
    token_selector: token?.value || role.token_selector || "$.access_token"};
}

function suggestLogin(operations: ContractOperation[]): GenericRole | undefined {
  const candidates = operations.flatMap((operation) => {
    const description = `${operation.operation_id} ${operation.path} ${operation.summary || ""}`;
    if (!/login|sign.?in|authenticate|登录|登陆/i.test(description) ||
        /register|sign.?up|注册|创建账号/i.test(description)) return [];
    if (operation.method !== "POST" || operation.authentication_required || operation.arguments.length !== 2)
      return [];
    const fields = loginChoices(operation, "username_field");
    const passwords = fields.filter((f) => /^(password|passwd|pwd)$/i.test(f.value));
    const usernames = fields.filter((f) => /^(user.?name|account|login|email|phone)$/i.test(f.value));
    const tokens = loginChoices(operation, "token_selector").filter((f) =>
      /token|jwt/i.test(f.value) && !/token.?type|refresh.?token/i.test(f.value));
    return passwords.length === 1 && usernames.length === 1 && tokens.length === 1
      ? [{alias: "tester", login_operation: operation.operation_id,
          username_env: "AGENTTEST_TARGET_USERNAME", password_env: "AGENTTEST_TARGET_PASSWORD",
          username_field: usernames[0].value, password_field: passwords[0].value,
          token_selector: tokens[0].value}]
      : [];
  });
  return candidates.length === 1 ? candidates[0] : undefined;
}

export function GenericProject({
  document,
  csrf,
  value,
  onChange,
  disabled,
}: {
  document: string;
  csrf: string;
  value: GenericSetup | null;
  onChange: (value: GenericSetup | null) => void;
  disabled: boolean;
}) {
  const [operations, setOperations] = useState<ContractOperation[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const revision = useRef(0);
  useEffect(() => {
    revision.current++;
    setOperations([]);
    setBusy(false);
    setError("");
    return () => {
      revision.current++;
    };
  }, [document]);

  useEffect(() => {
    if (!value || !document.trim() || !csrf || operations.length) return;
    let active = true;
    const current = revision.current;
    // Editing loads document metadata only; preserve all existing operator choices.
    intakeApi.analyze(csrf, document).then((result) => {
      if (active && current === revision.current) setOperations(result.operations);
    }).catch(() => {
      if (active && current === revision.current) setError("文档信息暂时无法读取，请重新读取文档。");
    });
    return () => { active = false; };
  }, [document, csrf, !!value, operations.length]);

  async function analyze() {
    const current = ++revision.current;
    setBusy(true);
    setError("");
    try {
      const result = await intakeApi.analyze(csrf, document);
      if (current !== revision.current) return;
      setOperations(result.operations);
      setPage(0);
      const supported = result.operations.filter((o) => o.supported);
      const login = suggestLogin(supported);
      if (value) {
        const existing = new Map(value.operations.map((o) => [o.operation_id, o]));
        onChange({...value, operations: supported.map((o) => existing.get(o.operation_id) || {
          operation_id: o.operation_id, alias: o.alias, enabled: false, role: null,
          statuses: o.statuses, checks: o.checks, arguments: o.arguments.map((a) => ({
            location: a.location, name: a.name, value: a.value,
            source_operation: a.source_operation, selector: a.selector,
            generated: a.generated ?? false, needs_input: a.needs_input ?? false,
          })),
        })});
        return;
      }
      onChange(
        supported.length
          ? {
              base_url: "http://127.0.0.1:8080",
              isolated_confirmed: false,
              allow_mutations: false,
              max_requests: 40,
              assisted: true,
              roles: login ? [login] : [],
              default_role: login?.alias,
              operations: supported.map((o) => ({
                operation_id: o.operation_id,
                alias: o.alias,
                enabled: o.operation_id === login?.login_operation ||
                  ["GET", "HEAD", "OPTIONS"].includes(o.method) &&
                  !o.arguments.some((a) => a.location === "path_parameters" || a.needs_input),
                role: login && (o.authentication_required || o.operation_id === login.login_operation)
                  ? login.alias : null,
                statuses: o.statuses,
                checks: o.checks,
                arguments: o.arguments.map((a) => ({
                  location: a.location,
                  name: a.name,
                  value: a.value,
                  source_operation: a.source_operation,
                  selector: a.selector,
                  generated: a.generated ?? false,
                  needs_input: a.needs_input ?? false,
                })),
              })),
            }
          : null,
      );
      if (!supported.length)
        setError("文档已读取，但没有当前引擎可配置的接口。请查看下方原因。");
    } catch (e) {
      if (current === revision.current)
        setError(e instanceof Error ? e.message : "文档读取失败。");
    } finally {
      if (current === revision.current) setBusy(false);
    }
  }

  function update(change: Partial<GenericSetup>) {
    if (value) onChange({ ...value, ...change });
  }
  function updateOperation(index: number, change: Partial<GenericOperation>) {
    if (value)
      update({
        operations: value.operations.map((o, i) =>
          i === index ? { ...o, ...change } : o,
        ),
      });
  }

  return (
    <div className="generic-project">
      <p>
        系统先整理文档中的输入和响应检查建议。核对环境与建议后，即可测试已就绪的接口。
      </p>
      <button
        type="button"
        disabled={disabled || busy || !document.trim() || !csrf}
        onClick={() => void analyze()}
      >
        {busy
          ? "正在读取接口…"
          : value
            ? "更新接口建议（保留配置）"
            : "读取接口并配置"}
      </button>
      {error && <Notice error>{error}</Notice>}
      {operations.some((o) => !o.supported) && <details>
        <summary>暂不支持的接口（{operations.filter((o) => !o.supported).length}）</summary>
        {operations.filter((o) => !o.supported).map((o) => (
          <p key={o.operation_id} className="caption">
            {o.method} {o.path}：{o.reason}，本轮不执行。
          </p>
        ))}
      </details>}
      {value && (
        <>
          <label htmlFor="generic-origin">隔离测试环境地址</label>
          <input
            id="generic-origin"
            value={value.base_url}
            disabled={disabled}
            onChange={(e) => update({ base_url: e.target.value })}
            placeholder="http://127.0.0.1:8080"
          />
          <p className="caption">
            填写服务地址，不含接口路径。请确认它连接专用测试数据库；
            本机地址不代表数据已隔离，勾选确认也不会自动创建隔离环境。
            私网 IP 需在服务端另行授权；公网和生产环境不可用。
          </p>
          <label className="task-option">
            <input
              type="checkbox"
              disabled={disabled}
              checked={value.isolated_confirmed}
              onChange={(e) => update({ isolated_confirmed: e.target.checked })}
            />
            我确认这是已授权的隔离测试环境
          </label>
          <label className="task-option">
            <input
              type="checkbox"
              disabled={disabled}
              checked={value.allow_mutations}
              onChange={(e) => update({ allow_mutations: e.target.checked })}
            />
            允许所选接口在此隔离环境中创建、修改或删除测试数据
          </label>
          <label className="task-option">
            <input
              type="checkbox"
              disabled={disabled}
              checked={value.approve_negative_tests ?? false}
              onChange={(e) =>
                update({ approve_negative_tests: e.target.checked,
                  ...(!e.target.checked ? {allow_negative_value_variants: false} : {}) })
              }
            />
            允许受控异常参数测试，按文档中的 400 / 422 检查拒绝结果
          </label>
          {value.allow_mutations && operations.filter((o) => o.method === "DELETE").map((o) => {
            const configured = value.operations.find((v) => v.operation_id === o.operation_id);
            const argument = configured?.arguments.find((a) => a.location === "path_parameters"
              && a.source_operation && operations.some((source) => source.method === "POST"
                && source.operation_id === a.source_operation
                && o.path === source.path.replace(/\/$/, "") + "/{" + a.name + "}"));
            if (!argument?.source_operation || !argument.selector) return null;
            const create = argument.source_operation;
            return <label className="task-option" key={o.operation_id}>
              <input type="checkbox" disabled={disabled}
                checked={(value.resource_lifecycles || []).some((r) => r.create_operation === create
                  && r.delete_operation === o.operation_id)}
                onChange={(e) => update({resource_lifecycles: [
                  ...(value.resource_lifecycles || []).filter((r) => r.create_operation !== create),
                  ...(e.target.checked ? [{create_operation: create, delete_operation: o.operation_id,
                    id_selector: argument.selector, path_parameter: argument.name,
                    fresh_resource_basis: "操作员确认：每次成功创建分配全新专用资源，清理仅删除本轮该资源。"}] : [])]})} />
              我确认“{value.operations.find((v) => v.operation_id === create)?.alias || create}”
              每次产生全新测试数据，允许“{configured?.alias}”清理本轮数据并保存复测
            </label>;
          })}
          {value.assisted && value.approve_negative_tests && (
            <label className="task-option">
              <input type="checkbox" disabled={disabled}
                checked={value.allow_negative_value_variants ?? false}
                onChange={(e) => update({allow_negative_value_variants: e.target.checked})} />
              允许在同一字段、同类文档约束内变化异常值
            </label>
          )}
          <label>
            本次任务最多发送的请求数
            <input
              type="number"
              min={1}
              max={64}
              value={value.max_requests}
              disabled={disabled}
              onChange={(e) => update({ max_requests: Number(e.target.value) })}
            />
          </label>
          <details className="configuration-details">
            <summary>配置测试身份（需要登录或 Token 时填写）</summary>
            <p>
              登录字段来自接口文档，请核对。这里只保存变量名；实际账号密码填写在本机 .env 中。
              修改 .env 后重启工作台，再检查身份是否就绪。
            </p>
            {value.roles.map((r, n) => (
              <fieldset key={n} disabled={disabled}>
                <legend>测试身份 {r.alias}</legend>
                <label>
                  身份别名
                  <input
                    value={r.alias}
                    onChange={(e) =>
                      update({
                        roles: value.roles.map((a, i) =>
                          i === n ? { ...a, alias: e.target.value } : a,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  鉴权方式
                  <select
                    value={r.bearer_env != null ? "bearer" : "login"}
                    onChange={(e) =>
                      update({
                        roles: value.roles.map((a, i) =>
                          i !== n
                            ? a
                            : e.target.value === "bearer"
                              ? {
                                  alias: a.alias,
                                  bearer_env: "AGENTTEST_TARGET_BEARER",
                                }
                              : {
                                  alias: a.alias,
                                  login_operation: "",
                                  username_env: "AGENTTEST_TARGET_USERNAME",
                                  password_env: "AGENTTEST_TARGET_PASSWORD",
                                  username_field: "username",
                                  password_field: "password",
                                  token_selector: "$.access_token",
                                },
                        ),
                      })
                    }
                  >
                    <option value="bearer">预置 Bearer Token</option>
                    <option value="login">账号密码登录</option>
                  </select>
                </label>
                {(r.bearer_env != null
                  ? ["bearer_env"]
                  : [
                      "username_env",
                      "password_env",
                      "username_field",
                      "password_field",
                      "token_selector",
                    ]
                ).map((field) => {
                  const current = String(r[field as keyof typeof r] || "");
                  const options = loginChoices(operations.find((o) =>
                    o.operation_id === r.login_operation), field);
                  const isDocumentField = field.endsWith("_field") || field === "token_selector";
                  return (
                  <label key={field}>
                    {
                      (
                        {
                          bearer_env: "Token 环境变量名",
                          username_env: "账号环境变量名",
                          password_env: "密码环境变量名",
                          username_field: "登录请求的账号字段",
                          password_field: "登录请求的密码字段",
                          token_selector: "登录响应的 Token 字段",
                        } as Record<string, string>
                      )[field]
                    }
                    {isDocumentField ? <select value={current}
                      onChange={(e) => update({roles: value.roles.map((a, i) =>
                        i === n ? {...a, [field]: e.target.value} : a)})}>
                      <option value="">请选择文档字段</option>
                      {!!current && !options.some((o) => o.value === current) &&
                        <option value={current}>{fieldLabel(current)}（当前建议）</option>}
                      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select> : <input
                      value={current}
                      onChange={(e) =>
                        update({
                          roles: value.roles.map((a, i) =>
                            i === n ? { ...a, [field]: e.target.value } : a,
                          ),
                        })
                      }
                    />}
                  </label>
                );})}
                {r.bearer_env == null && (
                  <label>
                    登录接口
                    <select
                      value={r.login_operation || ""}
                      onChange={(e) =>
                        update({
                          roles: value.roles.map((a, i) =>
                            i === n
                              ? { ...a, login_operation: e.target.value,
                                ...suggestLoginFields(operations.find((operation) =>
                                  operation.operation_id === e.target.value), a) }
                              : a,
                          ),
                        })
                      }
                    >
                      <option value="">请选择并在下方勾选登录接口</option>
                      {value.operations.map((o) => (
                        <option key={o.operation_id} value={o.operation_id}>
                          {operations.find((info) => info.operation_id === o.operation_id)?.summary
                            || o.alias || o.operation_id}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <button
                  type="button"
                  onClick={() =>
                    update({ roles: value.roles.filter((_, i) => i !== n) })
                  }
                >
                  移除此身份
                </button>
              </fieldset>
            ))}
            <button
              type="button"
              disabled={disabled || value.roles.length >= 8}
              onClick={() =>
                update({
                  roles: [
                    ...value.roles,
                    {
                      alias: `tester${value.roles.length + 1}`,
                      bearer_env: `AGENTTEST_TARGET_BEARER_${value.roles.length + 1}`,
                    },
                  ],
                })
              }
            >
              添加测试身份
            </button>
          </details>
          {value.roles.length > 0 && (
            <label>
              登录和受限接口默认使用的身份
              <select
                value={value.default_role || ""}
                disabled={disabled}
                onChange={(e) =>
                  update({ default_role: e.target.value || null })
                }
              >
                <option value="">逐项选择身份</option>
                {value.roles.map((r) => (
                  <option key={r.alias} value={r.alias}>
                    {r.alias}
                  </option>
                ))}
              </select>
            </label>
          )}
          {value.operations.some((o) =>
            o.arguments.some((a) => a.source_operation),
          ) && (
            <details>
              <summary>系统建议的接口依赖，确认后用于准备测试资源</summary>
              <ul>
                {value.operations.flatMap((o) =>
                  o.arguments
                    .filter((a) => a.source_operation)
                    .map((a) => (
                      <li key={`${o.operation_id}-${a.name}`}>
                        {operations.find(
                          (p) => p.operation_id === a.source_operation,
                        )?.summary || a.source_operation}
                        {" → "}
                        {operations.find(
                          (p) => p.operation_id === o.operation_id,
                        )?.summary || o.alias}
                        ， 传递 {a.name}。
                        <small>
                          {
                            operations
                              .find((p) => p.operation_id === o.operation_id)
                              ?.arguments.find(
                                (f) =>
                                  f.name === a.name &&
                                  f.location === a.location,
                              )?.dependency_basis
                          }
                        </small>
                      </li>
                    )),
                )}
              </ul>
            </details>
          )}
          <h3>文档建议与待补充信息</h3>
          <p>
            共 {operations.length || value.operations.length} 个接口，已选择{" "}
            {value.operations.filter((o) => o.enabled).length} 个。
            首次仅建议无需准备资源的读取接口及登录。写入和资源依赖接口可按需选择。
            文档检查建议仍需确认；未选择、未授权和暂不支持分别记录。
          </p>
          <div className="button-row">
            <button
              type="button"
              disabled={disabled}
              onClick={() =>
                update({
                  operations: value.operations.map((o) => ({
                    ...o,
                    enabled: true,
                  })),
                })
              }
            >
              采用全部受支持接口建议
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() =>
                update({
                  operations: value.operations.map((o) => ({
                    ...o,
                    enabled: false,
                  })),
                })
              }
            >
              清空选择
            </button>
          </div>
          {value.operations
            .slice(page * 10, page * 10 + 10)
            .map((o, offset) => {
              const index = page * 10 + offset;
              const info = operations.find(
                (p) => p.operation_id === o.operation_id,
              );
              const defaultRole = value.assisted && value.default_role
                ? value.roles.find((r) => r.alias === value.default_role) : undefined;
              const role = o.role || (info?.authentication_required ||
                defaultRole?.login_operation === o.operation_id ? defaultRole?.alias : null);
              const loginRole = value.roles.find((r) => r.alias === role &&
                r.login_operation === o.operation_id && info?.method === "POST");
              return (
                <fieldset
                  key={o.operation_id}
                  disabled={disabled}
                  aria-label={
                    info ? `${info.method} ${info.path}` : o.operation_id
                  }
                >
                  <legend>
                    <label className="task-option">
                      <input
                        type="checkbox"
                        checked={o.enabled}
                        onChange={(e) =>
                          updateOperation(index, { enabled: e.target.checked })
                        }
                      />
                      {info ? `${info.method} ${info.path}` : o.operation_id}
                    </label>
                  </legend>
                  {!loginRole && o.arguments.some((a) => a.needs_input) && (
                    <p className="caption">
                      待补充：
                      {o.arguments
                        .filter((a) => a.needs_input)
                        .map((a) => a.name)
                        .join("、")}{" "}
                      的测试资源或取值。
                    </p>
                  )}
                  {o.enabled && (
                    <details>
                      <summary>查看或调整输入与检查</summary>
                      <label>
                        这项操作的名称
                        <input
                          value={o.alias}
                          maxLength={80}
                          onChange={(e) =>
                            updateOperation(index, { alias: e.target.value })
                          }
                        />
                      </label>
                      {info?.basis && (
                        <p className="caption">建议依据：{info.basis}</p>
                      )}
                      <label>
                        执行身份
                        <select
                          value={role || ""}
                          onChange={(e) =>
                            updateOperation(index, {
                              role: e.target.value || null,
                            })
                          }
                        >
                          <option value="">匿名，无需登录</option>
                          {value.roles.map((r) => (
                            <option key={r.alias} value={r.alias}>
                              {r.alias}
                            </option>
                          ))}
                        </select>
                      </label>
                      {info?.authentication_required && !role && (
                        <Notice>
                          文档要求身份认证，请先添加测试身份并在此选择。
                        </Notice>
                      )}
                      <label>
                        预期 HTTP 状态码（多个用逗号分隔）
                        <input
                          value={o.statuses.join(",")}
                          onChange={(e) =>
                            updateOperation(index, {
                              statuses: e.target.value.split(",").map(Number),
                            })
                          }
                        />
                      </label>
                      {!o.checks.length && <p className="caption">
                        当前仅检查 HTTP 状态；可在下方添加响应字段检查，确认内容是否正确。
                      </p>}
                      {loginRole && <p className="caption" role="status">
                        登录账号与密码由本机配置提供：{loginRole.username_env}、{loginRole.password_env}。
                        无需在接口参数里重复填写；登录后由执行器管理 Token。
                      </p>}
                      {(loginRole ? [] : o.arguments).map((a, i) => (
                        sensitiveInput(a.name, info?.arguments.find((f) =>
                          f.location === a.location && f.name === a.name)?.schema)
                        ? <p key={i} className="caption">
                            {a.name} 是敏感输入。当前仅登录支持凭据注入，此接口暂不能执行；不要填写明文凭据。
                          </p>
                        : <div
                          className="generic-argument"
                          key={`${a.location}:${a.name}`}
                        >
                          <strong>
                            {a.name}{" "}
                            <small>
                              (
                              {a.location === "body"
                                ? "请求内容"
                                : a.location === "query"
                                  ? "查询参数"
                                  : "路径参数"}
                              )
                            </small>
                          </strong>
                          <label>
                            输入来源
                            <select
                              aria-label="输入来源"
                              value={a.source_operation || ""}
                              onChange={(e) =>
                                updateOperation(index, {
                                  arguments: o.arguments.map((p, j) =>
                                    i === j
                                      ? {
                                          ...p,
                                          source_operation:
                                            e.target.value || null,
                                          value: e.target.value ? null : "",
                                          selector: e.target.value
                                            ? "$.id"
                                            : "$",
                                          needs_input: false,
                                          generated: false,
                                        }
                                      : p,
                                  ),
                                })
                              }
                            >
                              <option value="">本次固定输入</option>
                              {value.operations
                                .filter(
                                  (p) =>
                                    p.enabled &&
                                    p.operation_id !== o.operation_id,
                                )
                                .map((p) => (
                                  <option
                                    key={p.operation_id}
                                    value={p.operation_id}
                                  >
                                    {p.alias}的响应
                                  </option>
                                ))}
                            </select>
                          </label>
                          {a.source_operation ? (
                            <>
                              {(
                                <label>
                                  从响应中选择字段
                                  <select
                                    value={a.selector}
                                    onChange={(e) =>
                                      updateOperation(index, {
                                        arguments: o.arguments.map((p, j) =>
                                          i === j
                                            ? {
                                                ...p,
                                                selector: e.target.value,
                                                needs_input: false,
                                              }
                                            : p,
                                        ),
                                      })
                                    }
                                  >
                                    {responseChoices(operations.find((p) =>
                                      p.operation_id === a.source_operation), [a.selector]).map((f) => (
                                        <option
                                          key={f.value}
                                          value={f.value}
                                        >
                                          {f.label}
                                        </option>
                                      ))}
                                  </select>
                                </label>
                              )}
                            </>
                          ) : (
                            <>
                              <InputValue
                                name={`输入值：${a.name}`}
                                value={a.value}
                                schema={
                                  info?.arguments.find(
                                    (f) =>
                                      f.name === a.name &&
                                      f.location === a.location,
                                  )?.schema
                                }
                                onChange={(next) =>
                                  updateOperation(index, {
                                    arguments: o.arguments.map((p, j) =>
                                      i === j
                                        ? {
                                            ...p,
                                            value: next,
                                            needs_input: false,
                                            generated: false,
                                          }
                                        : p,
                                    ),
                                  })
                                }
                              />
                              {a.generated && (
                                <p className="caption">
                                  系统可在文档约束内变化输入；修改此值后会固定使用你的值。
                                </p>
                              )}
                            </>
                          )}
                        </div>
                      ))}
                      {o.checks.map((check, i) => (
                        <div className="generic-argument" key={i}>
                          <label>
                            检查响应字段
                            <select
                              value={check.select}
                              onChange={(e) =>
                                updateOperation(index, {
                                  checks: o.checks.map((c, j) =>
                                    j === i
                                      ? { ...c, select: e.target.value,
                                        operator: "exists" as const, expected: null }
                                      : c,
                                  ),
                                })
                              }
                            >
                              {responseChoices(info, o.checks.map((c) => c.select)).map((f) =>
                                <option key={f.value} value={f.value}>{f.label}</option>)}
                            </select>
                          </label>
                          <label>
                            检查方式
                            <select
                              aria-label="检查方式"
                              value={check.operator}
                              onChange={(e) =>
                                updateOperation(index, {
                                  checks: o.checks.map((c, j) =>
                                    j === i
                                      ? {
                                          ...c,
                                          operator: e.target
                                            .value as typeof check.operator,
                                          expected: e.target.value === "in"
                                            ? (info?.checks || o.checks).find((item) =>
                                              item.select === check.select && item.operator === "in")?.expected
                                            : e.target.value === "exists" ? null : c.expected,
                                        }
                                      : c,
                                  ),
                                })
                              }
                            >
                              <option value="exists">应存在</option>
                              <option value="equals">应等于</option>
                              <option value="count">数量应为</option>
                              {(check.operator === "in" || (info?.checks || o.checks).some((item) =>
                                item.select === check.select && item.operator === "in"
                                && Array.isArray(item.expected))) &&
                                <option value="in">应属于已确认的允许值</option>}
                            </select>
                          </label>
                          {check.operator === "in" && Array.isArray(check.expected) &&
                            <p>允许值：{check.expected.map(String).join("、")}</p>}
                          {!["exists", "in"].includes(check.operator) && (
                            <label>
                              预期值
                              <input
                                value={String(check.expected ?? "")}
                                onChange={(e) =>
                                  updateOperation(index, {
                                    checks: o.checks.map((c, j) =>
                                      j === i
                                        ? {
                                            ...c,
                                            expected: scalar(e.target.value),
                                          }
                                        : c,
                                    ),
                                  })
                                }
                              />
                            </label>
                          )}
                          <button
                            type="button"
                            onClick={() =>
                              updateOperation(index, {
                                checks: o.checks.filter((_, j) => j !== i),
                              })
                            }
                          >
                            移除此检查
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        disabled={!responseChoices(info, o.checks.map((c) => c.select)).length}
                        onClick={() =>
                          updateOperation(index, {
                            checks: [
                              ...o.checks,
                              {
                                select: responseChoices(info, o.checks.map((c) => c.select))[0]?.value || "",
                                operator: "exists",
                                expected: null,
                              },
                            ],
                          })
                        }
                      >
                        添加响应字段检查
                      </button>
                      {(!info ||
                        ["GET", "HEAD", "OPTIONS"].includes(info.method)) && (
                        <details>
                          <summary>不同身份的资源访问规则</summary>
                          <p>
                            只填写已经确认的业务规则。例如，另一测试身份访问当前资源应返回
                            403。
                          </p>
                          {(o.access_checks || []).map((rule, i) => (
                            <fieldset key={i}>
                              <legend>身份访问检查 {i + 1}</legend>
                              <label>
                                执行身份
                                <select
                                  value={rule.role}
                                  onChange={(e) =>
                                    updateOperation(index, {
                                      access_checks: o.access_checks!.map(
                                        (r, j) =>
                                          j === i
                                            ? { ...r, role: e.target.value }
                                            : r,
                                      ),
                                    })
                                  }
                                >
                                  <option value="">请选择身份</option>
                                  {value.roles.map((r) => (
                                    <option key={r.alias} value={r.alias}>
                                      {r.alias}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <label>
                                已确认的响应状态
                                <input
                                  type="number"
                                  value={rule.statuses[0]}
                                  onChange={(e) =>
                                    updateOperation(index, {
                                      access_checks: o.access_checks!.map(
                                        (r, j) =>
                                          j === i
                                            ? {
                                                ...r,
                                                statuses: [
                                                  Number(e.target.value),
                                                ],
                                              }
                                            : r,
                                      ),
                                    })
                                  }
                                />
                              </label>
                              <label>
                                规则说明
                                <input
                                  value={rule.rule}
                                  maxLength={160}
                                  onChange={(e) =>
                                    updateOperation(index, {
                                      access_checks: o.access_checks!.map(
                                        (r, j) =>
                                          j === i
                                            ? { ...r, rule: e.target.value }
                                            : r,
                                      ),
                                    })
                                  }
                                />
                              </label>
                              <button
                                type="button"
                                onClick={() =>
                                  updateOperation(index, {
                                    access_checks: o.access_checks!.filter(
                                      (_, j) => i !== j,
                                    ),
                                  })
                                }
                              >
                                移除此规则
                              </button>
                            </fieldset>
                          ))}
                          <button
                            type="button"
                            disabled={!value.roles.length}
                            onClick={() =>
                              updateOperation(index, {
                                access_checks: [
                                  ...(o.access_checks || []),
                                  {
                                    role: "",
                                    statuses: [403],
                                    rule: "另一测试身份访问此资源应被拒绝",
                                  },
                                ],
                              })
                            }
                          >
                            添加身份访问规则
                          </button>
                        </details>
                      )}
                    </details>
                  )}
                </fieldset>
              );
            })}
          {value.operations.length > 10 && (
            <div className="button-row">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                上一组
              </button>
              <span>
                {page + 1}/{Math.ceil(value.operations.length / 10)}{" "}
                组，配置和统计保留全部接口
              </span>
              <button
                type="button"
                disabled={(page + 1) * 10 >= value.operations.length}
                onClick={() => setPage(page + 1)}
              >
                下一组
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
