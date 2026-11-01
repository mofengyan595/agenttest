import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { ProfileSummary, Projects } from "./Projects";

test("a new contract becomes its own reviewed configuration rather than a built-in profile", async () => {
  const catalog = {
    projects: [],
    profiles: [],
    generic_intake: true,
    model_source: "CONFIGURED_MODEL",
    execution_target: "AUTHORIZED_TEST_ENVIRONMENT",
  };
  const fetcher = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      const value =
        url === "/api/task-session"
          ? { csrf_token: "local" }
          : url === "/api/project-intake/analyze"
            ? {
                operations: [
                  {
                    operation_id: "readNovel",
                    method: "GET",
                    path: "/novel",
                    alias: "readNovel",
                    supported: true,
                    enabled: false,
                    role: null,
                    statuses: [200],
                    arguments: [],
                    checks: [],
                  },
                ],
              }
            : init?.method === "POST"
              ? {
                  id: "web_novel",
                  name: "新接口",
                  ready: true,
                  state: "READY",
                  version: 1,
                  missing: [],
                  confirmation: null,
                  profile_id: null,
                }
              : catalog;
      return new Response(JSON.stringify(value));
    });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <Projects />
    </MemoryRouter>,
  );
  await screen.findByLabelText("项目名称");
  expect(screen.getByLabelText("服务端授权配置")).not.toBeVisible();
  await userEvent.click(screen.getByText("其他接入方式"));
  expect(screen.getByLabelText("服务端授权配置")).toBeVisible();
  await userEvent.click(screen.getByText("其他接入方式"));
  await userEvent.type(screen.getByLabelText("项目名称"), "新接口");
  await userEvent.click(screen.getByText("或粘贴接口文档"));
  await userEvent.type(screen.getByLabelText("接口文档文本"), "openapi: 3.0.3");
  await userEvent.click(screen.getByRole("button", { name: "读取接口并配置" }));
  expect(
    await screen.findByRole("checkbox", { name: /^GET \/novel$/ }),
  ).toBeChecked();
  await userEvent.click(
    screen.getByLabelText("我确认这是已授权的隔离测试环境"),
  );
  const confirmation = screen.getByLabelText(
    "我已核对所选接口、输入和检查规则，确认用于此隔离测试环境",
  );
  await userEvent.click(confirmation);
  await userEvent.type(screen.getByLabelText("隔离测试环境地址"), "0");
  expect(confirmation).not.toBeChecked();
  await userEvent.click(confirmation);
  await userEvent.click(screen.getByRole("button", { name: "保存并检查接入" }));
  expect(
    await screen.findByText("已保存并登记，可以开始测试。"),
  ).toBeInTheDocument();
  const call = fetcher.mock.calls.find(
    ([url, init]) => url === "/api/project-intake" && init?.method === "POST",
  );
  const payload = JSON.parse(call?.[1].body as string);
  expect(payload.profile_id).toBeNull();
  expect(payload.confirmed).toBe(true);
  expect(payload.generic.allow_mutations).toBe(false);
  expect(payload.generic.operations).toMatchObject([
    { operation_id: "readNovel", enabled: true, statuses: [200] },
  ]);
  expect(fetcher.mock.calls.every(([url]) => !url.includes("/api/tasks"))).toBe(
    true,
  );
});

test("intake saves an unapproved draft and retains missing steps without pretending success", async () => {
  const project = {
    id: "web_new",
    name: "新项目",
    version: 1,
    ready: false,
    state: "NEEDS_INPUT",
    profile_id: null,
    confirmation: null,
    identity: "x",
    missing: ["尚未选择服务端授权配置。"],
  };
  const fetcher = vi.fn().mockImplementation(
    async (url: string, init: RequestInit) =>
      new Response(
        JSON.stringify(
          url === "/api/task-session"
            ? { csrf_token: "csrf" }
            : init?.method === "POST"
              ? project
              : {
                  projects: [],
                  profiles: [],
                  model_source: "SIMULATED",
                  execution_target: "SYNTHETIC_MEMORY",
                },
        ),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <Projects />
    </MemoryRouter>,
  );
  await screen.findByLabelText("项目名称");
  await userEvent.type(screen.getByLabelText("项目名称"), "新项目");
  await userEvent.click(screen.getByText("或粘贴接口文档"));
  await userEvent.type(screen.getByLabelText("接口文档文本"), "openapi: 3.1.0");
  await userEvent.click(screen.getByRole("button", { name: "保存并检查接入" }));
  expect(
    await screen.findByText("接入进度已保存，请补充下方缺失项。"),
  ).toBeInTheDocument();
  expect(screen.getByText("尚未选择服务端授权配置。")).toBeInTheDocument();
  const sent = fetcher.mock.calls.find(
    ([url, init]) => url === "/api/project-intake" && init?.method === "POST",
  );
  expect(JSON.parse(sent?.[1].body as string)).toMatchObject({
    confirmed: false,
    profile_id: null,
    version: 0,
  });
  expect(
    screen.queryByText("已保存并登记，可以开始测试。"),
  ).not.toBeInTheDocument();
});

test("an unavailable project service gives a concrete start instruction and retry", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("{}", { status: 503 })),
  );
  render(
    <MemoryRouter>
      <Projects />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "scripts/start_web.ps1",
  );
  expect(screen.getByRole("button", { name: "重新连接" })).toBeInTheDocument();
});

test("a late initial catalog cannot silently clear an operator confirmation", async () => {
  const resolve: ((response: Response) => void)[] = [];
  const catalog = {
    projects: [],
    profiles: [
      {
        id: "tickets",
        identity: "b".repeat(64),
        name: "工单",
        environment: "内存模拟目标",
        address: null,
        identities: [],
        rules: [],
        examples: [],
        require_review: false,
      },
    ],
    model_source: "SIMULATED",
    execution_target: "SYNTHETIC_MEMORY",
  };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation((url: string) =>
        url === "/api/task-session"
          ? Promise.resolve(
              new Response(JSON.stringify({ csrf_token: "local" })),
            )
          : new Promise<Response>((r) => resolve.push(r)),
      ),
  );
  render(
    <StrictMode>
      <MemoryRouter>
        <Projects />
      </MemoryRouter>
    </StrictMode>,
  );
  await waitFor(() => expect(resolve).toHaveLength(2));
  await act(async () => {
    resolve[1](new Response(JSON.stringify(catalog)));
  });
  await userEvent.selectOptions(
    screen.getByLabelText("服务端授权配置"),
    "tickets",
  );
  await userEvent.click(
    screen.getByRole("checkbox", {
      name: "我已核对以上行为和预期，确认适用于此项目",
    }),
  );
  expect(screen.getByRole("checkbox")).toBeChecked();
  await act(async () => {
    resolve[0](new Response(JSON.stringify(catalog)));
  });
  expect(screen.getByRole("checkbox")).toBeChecked();
});

test("missing account identifies the server setting and recheck path without exposing a credential", () => {
  render(
    <ProfileSummary
      profile={{
        id: "tickets",
        identity: "a".repeat(64),
        name: "工单",
        environment: "授权测试环境",
        address: "http://127.0.0.1:8099",
        identities: [
          {
            alias: "owner",
            ready: false,
            instruction: "请在启动服务的环境变量中配置：TICKET_OWNER_BEARER",
          },
        ],
        rules: [],
        examples: [],
        require_review: false,
      }}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("owner");
  expect(screen.getByRole("alert")).toHaveTextContent("TICKET_OWNER_BEARER");
  expect(screen.getByRole("alert")).toHaveTextContent(
    "配置后重启服务，再点击重新检查",
  );
  expect(screen.getByText(/缺少配置/)).toBeInTheDocument();
});
