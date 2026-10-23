import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { GoalTasks } from "./GoalTasks";
import type { GoalTask } from "../api/tasks";

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

function setup(status = "FINISHED", result: GoalTask = task) {
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
      return response({ ...result, status });
    });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <StrictMode>
        <GoalTasks />
      </StrictMode>
    </MemoryRouter>,
  );
  return fetcher;
}

test("registered project text submits session protected and shows actual evidence", async () => {
  const fetcher = setup();
  await screen.findByRole("option", { name: "tickets" });
  expect(
    fetcher.mock.calls.filter(([url]) => url === "/api/task-session"),
  ).toHaveLength(1);
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

test("groups retain excluded denominators and show approved negative outcomes", async () => {
  setup("FINISHED", {
    ...task,
    summary: {
      budget_preview: {
        required_business_requests: 11,
        required_planning_calls: 6,
        normal_cases: 1,
        derived_cases: 1,
        boundary_or_enum_cases: 0,
        approved_negative_cases: 1,
        optional_readonly_requests: 1,
        optional_model_calls: 0,
        planning_token_upper_bound: 144576,
        complete_project_within_budget: false,
        executable_groups: ["a", "b"],
        excluded_groups: [
          { case_id: "c", reason: "SUITE_BUDGET_OR_USAGE_STOP" },
        ],
      },
      cases: [
        {
          case_id: "a",
          operation_id: "validateRecord",
          status: "FINISHED",
          outcome: "PASS",
          max_business_requests: 1,
          kinds: ["approved_negative:missing"],
          approved_negative_steps: 1,
          negative_rejections_passed: 1,
          plan_source: "DETERMINISTIC_RULE",
          structural_validation_passed: true,
          task_satisfied: true,
          plan_derivation: {
            base_run_id: "a".repeat(32),
            base_plan_sha256: "b".repeat(64),
          },
        },
        {
          case_id: "c",
          operation_id: "listTickets",
          status: "NOT_EXECUTED",
          max_business_requests: 1,
          reason: "SUITE_BUDGET_OR_USAGE_STOP",
        },
      ],
      coverage: {
        operation_denominator: 2,
        scenario_denominator: 3,
        planned_scenarios: 1,
        executed_scenarios: 1,
        executed_operations: 1,
        assertion_passed_scenarios: 1,
        not_executed: 2,
        assertions_evaluated: 2,
        assertions_passed: 2,
      },
    },
  });
  await screen.findByRole("option", { name: "tickets" });
  await userEvent.type(screen.getByLabelText("测试需求"), "测试项目核心接口");
  await userEvent.click(screen.getByRole("button", { name: "开始测试" }));
  expect(await screen.findByLabelText("分组预算预览")).toHaveTextContent(
    "必需业务请求 11",
  );
  expect(screen.getByText(/按预期拒绝 1\/1 个已构造输入/)).toBeInTheDocument();
  expect(screen.getByText(/合法基础计划的规则派生/)).toBeInTheDocument();
  expect(screen.getByText(/结构校验通过/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看基础计划" })).toHaveAttribute(
    "href",
    `/runs/${"a".repeat(32)}`,
  );
  expect(screen.getByText(/本组未执行，保留在覆盖分母中/)).toBeInTheDocument();
  expect(screen.getByText(/计划合格场景 1\/3/)).toBeInTheDocument();
  expect(screen.getByText(/预算或待审项限制完整执行/)).toBeInTheDocument();
});

test("structured goal drafts remain visibly unverified with unexplained requirements", async () => {
  setup("NEEDS_REVIEW", {
    ...task,
    status: "NEEDS_REVIEW",
    summary: {
      goal_spec: {
        semantic_verified: false,
        structure_valid: true,
        source_accounted: false,
        unaccounted_characters: 5,
        proposed_goals: [{ goal_id: "create", start: 0, end: 4 }],
        question: "请明确退款预期。",
      },
    },
  });
  await screen.findByRole("option", { name: "tickets" });
  await userEvent.type(screen.getByLabelText("测试需求"), "受理工单并核对退款");
  await userEvent.click(screen.getByRole("button", { name: "开始测试" }));
  expect(
    await screen.findByText("模型目标草案：语义待核验"),
  ).toBeInTheDocument();
  expect(screen.getByText(/5 个非空白字符未被解释/)).toBeInTheDocument();
  expect(screen.getByText("请明确退款预期。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "补充澄清" })).toBeInTheDocument();
});
