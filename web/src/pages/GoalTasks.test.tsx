import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { GoalTasks } from "./GoalTasks";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const task = {
  id: "a".repeat(32),
  project_id: "tickets",
  project_identity: "b".repeat(64),
  request_sha256: "c".repeat(64),
  status: "FINISHED",
  plan_version: 1,
  created_at: 1,
  summary: {
    requested_goals: ["create"],
    required_steps: 1,
    outcome: "PASS",
    task_satisfied: true,
    run_id: "d".repeat(32),
    context_usage: {
      requests: 1,
      model_calls: 1,
      total_tokens: 100,
      usage_known: true,
    },
  },
};

function setup(status = "FINISHED") {
  const fetcher = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/task-session")
        return response({ csrf_token: "local-csrf" });
      if (url === "/api/projects")
        return response({
          projects: [{ id: "tickets", identity: "b".repeat(64) }],
        });
      if (url === "/api/tasks" && init?.method === "GET")
        return response({ tasks: [] });
      return response({ ...task, status });
    });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <GoalTasks />
    </MemoryRouter>,
  );
  return fetcher;
}

test("registered project text submits session protected and shows actual evidence", async () => {
  const fetcher = setup();
  await screen.findByRole("option", { name: "tickets" });
  await userEvent.type(screen.getByLabelText("测试需求"), "测试创建工单接口");
  await userEvent.click(screen.getByRole("button", { name: "开始测试" }));
  expect(await screen.findByText("PASS")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "查看业务、模型与规则证据" }),
  ).toHaveAttribute("href", `/runs/${"d".repeat(32)}`);
  const call = fetcher.mock.calls.find(
    ([url, init]) => url === "/api/tasks" && init.method === "POST",
  );
  expect(call?.[1].credentials).toBe("same-origin");
  expect(call?.[1].headers["x-agenttest-csrf"]).toBe("local-csrf");
  const payload = JSON.parse(call?.[1].body);
  expect(payload.user_task).toBe("测试创建工单接口");
  expect(payload).not.toHaveProperty("target_url");
  expect(payload).not.toHaveProperty("credentials");
  await userEvent.click(screen.getByRole("button", { name: "开始测试" }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.filter(
        ([url, init]) => url === "/api/tasks" && init.method === "POST",
      ),
    ).toHaveLength(2),
  );
  const retries = fetcher.mock.calls.filter(
    ([url, init]) => url === "/api/tasks" && init.method === "POST",
  );
  expect(JSON.parse(retries[0][1].body).submission_id).toBe(
    JSON.parse(retries[1][1].body).submission_id,
  );
});

test("explicit policy review submits the checked version rather than silently approving", async () => {
  const fetcher = setup("WAITING_REVIEW");
  await screen.findByRole("option", { name: "tickets" });
  await userEvent.type(screen.getByLabelText("测试需求"), "创建工单");
  await userEvent.click(screen.getByRole("button", { name: "开始测试" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "确认此计划并执行" }),
  );
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([url]) => String(url).endsWith("/start")),
    ).toBe(true),
  );
  const started = fetcher.mock.calls.find(([url]) =>
    String(url).endsWith("/start"),
  );
  expect(JSON.parse(started?.[1].body)).toEqual({ plan_version: 1 });
});

test("unconfigured task service does not offer an enabled execution button", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ detail: "hidden-secret" }, 503)),
  );
  render(
    <MemoryRouter>
      <GoalTasks />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("任务服务未启用");
  expect(screen.getByRole("button", { name: "开始测试" })).toBeDisabled();
  expect(screen.queryByText(/hidden-secret/)).not.toBeInTheDocument();
});
