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
  await userEvent.click(screen.getByRole("button", {name: "添加测试身份"}));
  await userEvent.selectOptions(screen.getByRole("combobox", {name: "鉴权方式"}), "login");
  await userEvent.selectOptions(screen.getByRole("combobox", {name: "登录接口"}), "signIn");
  expect(screen.getByRole("combobox", {name: "登录请求的账号字段"})).toHaveValue("account");
  expect(screen.getByRole("combobox", {name: "登录请求的密码字段"})).toHaveValue("password");
  const token = screen.getByRole("combobox", {name: "登录响应的 Token 字段"});
  expect(token).toHaveValue("$.data.sessionToken");
  expect(token).toHaveTextContent("data 的 sessionToken");
  expect(screen.queryByRole("textbox", {name: "登录响应的 Token 字段"})).not.toBeInTheDocument();
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
