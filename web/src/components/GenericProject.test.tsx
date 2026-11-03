import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { GenericSetup } from "../api/tasks";
import { GenericProject } from "./GenericProject";

test("login and response fields are chosen from the document without typing selectors", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({operations: [{
    operation_id: "signIn", alias: "登录", summary: "登录", method: "POST", path: "/session",
    supported: true, authentication_required: false, enabled: true, role: null, statuses: [200],
    arguments: ["account", "password"].map((name) => ({location: "body", name,
      value: null, value_type: "string", source_operation: null, selector: "$"})),
    checks: [{select: "$.data.state", operator: "in", expected: ["ready", "locked"]}],
    response_fields: [{selector: "$.data.sessionToken", label: "会话凭据", value_type: "string"},
      {selector: "$.data.tokenType", label: "凭据类型", value_type: "string"},
      {selector: "$.data.state", label: "状态", value_type: "string"}],
  }]}))));
  function Harness() {
    const [value, setValue] = useState<GenericSetup | null>(null);
    return <GenericProject document="uploaded OpenAPI" csrf="local" value={value}
      onChange={setValue} disabled={false} />;
  }
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", {name: "读取接口并配置"}));
  await userEvent.click(await screen.findByText("配置测试身份（需要登录或 Token 时填写）"));
  expect(screen.getByRole("combobox", {name: "鉴权方式"})).toHaveValue("login");
  expect(screen.getByRole("combobox", {name: "登录接口"})).toHaveValue("signIn");
  expect(screen.getByRole("combobox", {name: "登录请求的账号字段"})).toHaveValue("account");
  expect(screen.getByRole("combobox", {name: "登录请求的密码字段"})).toHaveValue("password");
  const token = screen.getByRole("combobox", {name: "登录响应的 Token 字段"});
  expect(token).toHaveValue("$.data.sessionToken");
  expect(token).toHaveTextContent("data 的 sessionToken");
  expect(screen.queryByRole("textbox", {name: "登录响应的 Token 字段"})).not.toBeInTheDocument();
  await userEvent.click(screen.getByText("查看或调整输入与检查"));
  expect(screen.getByRole("combobox", {name: "执行身份"})).toHaveValue("tester");
  expect(screen.getByText(/登录账号与密码由本机配置提供/)).toBeVisible();
  expect(screen.queryByLabelText("输入值：password")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("输入值：account")).not.toBeInTheDocument();
});

test("new intake suggests reads and login without enabling writes or resource-dependent reads", async () => {
  const base = {supported: true, enabled: true, role: null, statuses: [200], arguments: [],
    checks: [], response_fields: [], authentication_required: false};
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({operations: [
    {...base, operation_id: "health", alias: "健康", method: "GET", path: "/health"},
    {...base, operation_id: "list", alias: "列表", method: "GET", path: "/items"},
    {...base, operation_id: "create", alias: "创建", method: "POST", path: "/items"},
    {...base, operation_id: "detail", alias: "详情", method: "GET", path: "/items/{id}",
      arguments: [{location: "path_parameters", name: "id", value_type: "integer",
        value: null, needs_input: true}]},
  ]}))));
  function Form() {
    const [value, setValue] = useState<GenericSetup | null>(null);
    return <GenericProject document="contract" csrf="local" value={value} disabled={false}
      onChange={setValue} />;
  }
  render(<Form />);
  await userEvent.click(screen.getByRole("button", {name: "读取接口并配置"}));
  expect(await screen.findByRole("checkbox", {name: "GET /health"})).toBeChecked();
  expect(screen.getByRole("checkbox", {name: "GET /items"})).toBeChecked();
  expect(screen.getByRole("checkbox", {name: "POST /items"})).not.toBeChecked();
  expect(screen.getByRole("checkbox", {name: "GET /items/{id}"})).not.toBeChecked();
  expect(screen.getByLabelText(/允许所选接口/)).not.toBeChecked();
});

test("registration returning a token is not suggested as login and cannot collect a plain password", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({operations: [{
    operation_id: "registerMember", alias: "注册", summary: "注册并获取 Token", method: "POST",
    path: "/signup", supported: true, authentication_required: false, enabled: true,
    role: null, statuses: [201], arguments: ["username", "password"].map((name) => ({
      location: "body", name, value: null, value_type: "string", source_operation: null, selector: "$"})),
    checks: [], response_fields: [{selector: "$.accessToken", value_type: "string", label: "凭据"}],
  }]}))));
  function Form() {
    const [value, setValue] = useState<GenericSetup | null>(null);
    return <GenericProject document="contract" csrf="local" value={value} disabled={false}
      onChange={setValue} />;
  }
  render(<Form />);
  await userEvent.click(screen.getByRole("button", {name: "读取接口并配置"}));
  expect(await screen.findByRole("checkbox", {name: "POST /signup"})).not.toBeChecked();
  expect(screen.queryByLabelText("身份别名")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", {name: "POST /signup"}));
  await userEvent.click(screen.getByText("查看或调整输入与检查"));
  expect(screen.queryByLabelText("输入值：password")).not.toBeInTheDocument();
  expect(screen.getByText(/当前仅登录支持凭据注入，此接口暂不能执行/)).toBeVisible();
});

test("a password nested inside an array does not expose a normal JSON input", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({operations: [{
    operation_id: "updateDetails", alias: "详情", method: "POST", path: "/details",
    supported: true, authentication_required: false, statuses: [200], checks: [],
    arguments: [{location: "body", name: "details", value: null, value_type: "array",
      schema: {type: "array", items: {type: "object", properties: {
        secret: {type: "string", format: "password"}}}}}],
  }]}))));
  function Form() {
    const [value, setValue] = useState<GenericSetup | null>(null);
    return <GenericProject document="contract" csrf="local" value={value} disabled={false}
      onChange={setValue} />;
  }
  render(<Form />);
  await userEvent.click(screen.getByRole("button", {name: "读取接口并配置"}));
  await userEvent.click(await screen.findByRole("checkbox", {name: "POST /details"}));
  await userEvent.click(screen.getByText("查看或调整输入与检查"));
  expect(screen.queryByLabelText("输入值：details")).not.toBeInTheDocument();
  expect(screen.getByText(/details 是敏感输入/)).toBeVisible();
});

test("an existing fixed-negative approval does not silently authorize value variation", async () => {
  const changed = vi.fn();
  function Form() {
    const [value, setValue] = useState<GenericSetup | null>({
      base_url: "http://127.0.0.1:18998", isolated_confirmed: true,
      allow_mutations: false, assisted: true, approve_negative_tests: true,
      max_requests: 40, roles: [], operations: [],
    });
    return <GenericProject document="" csrf="local" value={value} disabled={false} onChange={(next) => {
      changed(next); setValue(next);
    }} />;
  }
  render(<Form />);
  const variants = screen.getByLabelText("允许在同一字段、同类文档约束内变化异常值");
  expect(variants).not.toBeChecked();
  await userEvent.click(variants);
  expect(changed).toHaveBeenLastCalledWith(expect.objectContaining({
    approve_negative_tests: true, allow_negative_value_variants: true,
  }));
  await userEvent.click(screen.getByLabelText(/允许受控异常参数测试/));
  expect(changed).toHaveBeenLastCalledWith(expect.objectContaining({
    approve_negative_tests: false, allow_negative_value_variants: false,
  }));
  expect(screen.queryByLabelText("允许在同一字段、同类文档约束内变化异常值"))
    .not.toBeInTheDocument();
});

test("document links suggest a lifecycle but require explicit fresh-resource confirmation", async () => {
  const changed = vi.fn();
  const common = {supported: true, enabled: true, role: null, checks: [],
    authentication_required: false};
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({operations: [
    {...common, operation_id: "create", alias: "创建", method: "POST", path: "/items",
      statuses: [201], arguments: []},
    {...common, operation_id: "delete", alias: "清理", method: "DELETE", path: "/items/{itemId}",
      statuses: [204], arguments: [{location: "path_parameters", name: "itemId", value: null,
        source_operation: "create", selector: "$.id"}]},
  ]}))));
  function Form() {
    const [value, setValue] = useState<GenericSetup | null>(null);
    return <GenericProject document="contract" csrf="local" value={value} disabled={false}
      onChange={(next) => { changed(next); setValue(next); }} />;
  }
  render(<Form />);
  await userEvent.click(screen.getByRole("button", {name: "读取接口并配置"}));
  await userEvent.click(await screen.findByLabelText(/允许所选接口/));
  const confirmation = screen.getByLabelText(/每次产生全新测试数据/);
  expect(confirmation).not.toBeChecked();
  await userEvent.click(confirmation);
  expect(changed).toHaveBeenLastCalledWith(expect.objectContaining({resource_lifecycles: [
    expect.objectContaining({create_operation: "create", delete_operation: "delete",
      id_selector: "$.id", path_parameter: "itemId"}),
  ]}));
  await userEvent.click(confirmation);
  expect(changed).toHaveBeenLastCalledWith(expect.objectContaining({resource_lifecycles: []}));
});

test("editing reads document metadata without replacing the operator's saved configuration", async () => {
  const change = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({operations: [{
    operation_id: "read", alias: "读取", enabled: true, role: null, statuses: [200],
    arguments: [], checks: [], method: "GET", path: "/items", supported: true,
    authentication_required: false,
  }]}))));
  const saved: GenericSetup = {base_url: "http://127.0.0.1:18998", isolated_confirmed: true,
    allow_mutations: false, max_requests: 17, roles: [], operations: [
      {operation_id: "read", alias: "我确认的名称", enabled: true, role: null,
        statuses: [200], arguments: [], checks: []}], assisted: true};
  render(<GenericProject document="saved document" csrf="local" value={saved}
    onChange={change} disabled={false} />);
  await screen.findByRole("group", {name: "GET /items"});
  await userEvent.click(screen.getByText("查看或调整输入与检查"));
  expect(await screen.findByDisplayValue("我确认的名称")).toBeVisible();
  expect(screen.getByLabelText("隔离测试环境地址")).toHaveValue(saved.base_url);
  expect(change).not.toHaveBeenCalled();
});

test("refresh appends new capability without resetting saved identities or enabling new operations", async () => {
  const base = {supported: true, method: "GET", enabled: true, role: null,
    statuses: [200], arguments: [], checks: [], authentication_required: false};
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({operations: [
    {...base, operation_id: "read", alias: "原建议", path: "/items"},
    {...base, operation_id: "newRead", alias: "新增", path: "/members"},
  ]}))));
  const change = vi.fn();
  const saved: GenericSetup = {base_url: "http://127.0.0.1:18998", isolated_confirmed: true,
    allow_mutations: false, max_requests: 17, default_role: "owner", roles: [
      {alias: "owner", bearer_env: "AGENTTEST_TARGET_BEARER"}], operations: [
      {operation_id: "read", alias: "我确认的名称", enabled: true, role: "owner",
        statuses: [200], arguments: [], checks: [{select: "$.state", operator: "equals",
          expected: "ready"}]}], assisted: true};
  render(<GenericProject document="contract" csrf="local" value={saved} disabled={false}
    onChange={change} />);
  await userEvent.click(screen.getByRole("button", {name: "更新接口建议（保留配置）"}));
  expect(change).toHaveBeenLastCalledWith(expect.objectContaining({
    base_url: saved.base_url, roles: saved.roles, max_requests: 17, default_role: "owner",
    operations: [saved.operations[0], expect.objectContaining({operation_id: "newRead", enabled: false})],
  }));
});
