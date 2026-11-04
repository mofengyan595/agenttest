import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { GoalTasks } from "./GoalTasks";
import type { GoalTask } from "../api/tasks";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

test("fixed regression reads the completed evidence instead of caching a partial report", async () => {
  const id = "a".repeat(32), runId = "b".repeat(32);
  let ended = false, reportRequests = 0;
  const pending: GoalTask = {id, project_id: "records", project_identity: "v1",
    request_sha256: "c".repeat(64), status: "RUNNING", plan_version: 1, created_at: 1,
    summary: {mode: "FIXED_REGRESSION", run_id: runId, model_source: "DISABLED"}};
  const methods = ["POST", "POST", "PUT", "GET", "DELETE"];
  const report = {run_id: runId, completed: true, outcome: "PASS",
    plan_structure: methods.map((method, i) => ({step_id: `s${i}`, method})),
    steps: methods.map((method, i) => ({step_id: `s${i}`, method,
      path_template: `/records/${i}`, outcome: "PASS", reason: "POLICY_EVALUATED",
      attempts: [{status_code: 200}], checks: []}))};
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/task-session") return response({csrf_token: "local"});
    if (url === "/api/projects") return response({projects: []});
    if (url === "/api/project-intake") return response({projects: [], profiles: []});
    if (url === "/api/tasks") return response({tasks: [pending]});
    if (url === "/api/saved-tests") return response({tests: []});
    if (url.endsWith("/timeline")) return response({events: []});
    if (url === `/api/tasks/${id}`) return response(ended ? {...pending, status: "FINISHED",
      summary: {...pending.summary, outcome: "PASS", fixed_regression_passed: true}} : pending);
    if (url === `/api/runs/${runId}`) {
      reportRequests++;
      return response(ended ? report : {...report, completed: false, outcome: "TEST_INFRA_ERROR",
        steps: report.steps.slice(0, 3).map((s, i) => i === 2 ? {...s,
          outcome: "TEST_INFRA_ERROR", reason: "IN_PROGRESS"} : s)});
    }
    throw new Error("Unexpected request");
  }));
  render(<MemoryRouter initialEntries={[`/saved?task=${id}`]}><GoalTasks savedOnly /></MemoryRouter>);
  expect(await screen.findByText("正在执行，完成后显示完整请求与检查证据。"))
    .toBeVisible();
  expect(reportRequests).toBe(0);
  ended = true;
  expect(await screen.findByText("固定回归检查通过", {}, {timeout: 4000})).toBeVisible();
  expect(await screen.findByText(/已记录步骤 5\/5；未执行 0/)).toBeVisible();
  expect(screen.queryByText(/测试环境异常/)).not.toBeInTheDocument();
  expect(reportRequests).toBe(1);
});

test("cancelled result appears after reload while the full project catalog is still loading", async () => {
  const cancelled: GoalTask = {
    id: "c".repeat(32), project_id: "web_cancelled", project_identity: "v1",
    request_sha256: "d".repeat(64), status: "CANCELLED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: false, reason: "已停止后续动作；证据保留。",
      cases: [], questions: []},
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/task-session") return response({csrf_token: "local"});
    if (url === "/api/projects" || url === "/api/project-intake") return new Promise<Response>(() => {});
    if (url === "/api/tasks") return response({tasks: [cancelled]});
    if (url === `/api/tasks/${cancelled.id}`) return response(cancelled);
    if (url === "/api/saved-tests") return response({tests: []});
    if (url.endsWith("/timeline")) return response({events: []});
    throw new Error("Unexpected request");
  }));
  render(<MemoryRouter initialEntries={[`/tasks?task=${cancelled.id}`]}><GoalTasks /></MemoryRouter>);
  expect(await screen.findByRole("region", {name: "本轮测试结果"}))
    .toHaveTextContent("本轮停止原因：已停止后续动作；证据保留。");
  expect(screen.queryByText(/当前本地会话无法读取此任务/)).not.toBeInTheDocument();
  expect(screen.getByText("接口测试记录")).toBeVisible();
  expect(screen.queryByText("web_cancelled")).not.toBeInTheDocument();
});

test("saved tests load independently of a slow project intake catalog", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/task-session") return response({ csrf_token: "local" });
    if (url === "/api/projects") return new Promise<Response>(() => {});
    if (url === "/api/tasks") return response({ tasks: [] });
    if (url === "/api/project-intake") return new Promise<Response>(() => {});
    if (url === "/api/saved-tests") return response({ tests: [{
      task_id: "a".repeat(32), run_id: "b".repeat(32), project_id: "web_new",
      project_name: "新项目", available: true, reason: "独立只读检查",
    }] });
    throw new Error("Unexpected request");
  }));
  render(<MemoryRouter><GoalTasks savedOnly /></MemoryRouter>);
  expect(await screen.findByRole("button", {name: "再次运行固定测试"})).toBeEnabled();
  expect(screen.getByText("新项目")).toBeVisible();
  expect(screen.queryByText(/还没有可选项目/)).not.toBeInTheDocument();
  expect(screen.queryByText(/尚未保存固定测试/)).not.toBeInTheDocument();
});

test("a new product project starts without a memorized sentence and prevents repeat clicks while running", async () => {
  const pending = {
    id: "a".repeat(32),
    project_id: "web_new",
    project_identity: "v1",
    request_sha256: "b".repeat(64),
    status: "QUEUED",
    plan_version: 1,
    created_at: 1,
    summary: {
      mode: "MODEL_DESIGNED",
      cases: [],
      questions: [],
      complete: false,
    },
  };
  const fetcher = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/task-session") return response({ csrf_token: "local" });
      if (url === "/api/projects")
        return response({
          projects: [
            {
              id: "web_new",
              identity: "v1",
              name: "新项目",
              ready: true,
              testing_mode: "MODEL_DESIGNED",
            },
          ],
        });
      if (url === "/api/project-intake")
        return response({
          projects: [],
          profiles: [],
          model_source: "CONFIGURED_MODEL",
          execution_target: "AUTHORIZED_TEST_ENVIRONMENT",
        });
      if (url === "/api/tasks" && init?.method === "GET")
        return response({ tasks: [] });
      if (url.endsWith("/timeline")) return response({ events: [] });
      if (url === "/api/saved-tests") return response({ tests: [] });
      return response(pending);
    });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <GoalTasks />
    </MemoryRouter>,
  );
  await screen.findByLabelText("测试重点（可选）");
  const start = screen.getByRole("button", { name: "开始一轮测试" });
  expect(start).toBeEnabled();
  await userEvent.click(start);
  await waitFor(() => expect(start).toBeDisabled());
  const submits = fetcher.mock.calls.filter(
    ([url, init]) => url === "/api/tasks" && init?.method === "POST",
  );
  expect(submits).toHaveLength(1);
  expect(JSON.parse(submits[0][1].body as string).user_task).toBe("");
});
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
  expect(await screen.findByText("本次需求已完成")).toBeInTheDocument();
  expect(screen.getByText("检查通过")).toBeInTheDocument();
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
  expect(await screen.findByRole("alert")).toHaveTextContent("测试服务未启用");
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

test("new demand after a fixed regression starts an independent task", async () => {
  const fetcher = setup("FINISHED", {
    ...task,
    summary: {
      mode: "FIXED_REGRESSION",
      fixed_regression_passed: true,
      model_source: "DISABLED",
    },
  });
  await screen.findByRole("option", { name: "tickets" });
  await userEvent.type(screen.getByLabelText("测试需求"), "创建工单");
  await userEvent.click(screen.getByRole("button", { name: "开始测试" }));
  expect(await screen.findByText("固定回归检查通过")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "新建测试需求" }));
  expect(screen.getByLabelText("测试需求")).toHaveValue("");
  await userEvent.type(screen.getByLabelText("测试需求"), "查询工单");
  await userEvent.click(screen.getByRole("button", { name: "开始测试" }));
  const requests = fetcher.mock.calls.filter(
    ([url, init]) => url === "/api/tasks" && init?.method === "POST",
  );
  expect(JSON.parse(requests.at(-1)?.[1].body)).toMatchObject({
    parent_task_id: null,
    user_task: "查询工单",
  });
});

test("requested result does not wait for complete history or a cold project catalog", async () => {
  const task: GoalTask = {
    id: "e".repeat(32), project_id: "web_large", project_identity: "v1",
    request_sha256: "f".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: false, cases: [],
      questions: [{id: "missing", operation_id: "private", question: "请补充测试身份。",
        requested_behavior: "需要测试身份", kind: "clarification", role: null, status_choices: []}]},
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/task-session") return response({csrf_token: "local"});
    if (url === `/api/tasks/${task.id}`) return response(task);
    if (url.endsWith("/timeline")) return response({events: []});
    if (url === "/api/saved-tests") return response({tests: []});
    if (["/api/tasks", "/api/projects", "/api/project-intake"].includes(url))
      return new Promise<Response>(() => {});
    throw new Error("Unexpected request");
  }));
  render(<MemoryRouter initialEntries={[`/tasks?task=${task.id}`]}><GoalTasks /></MemoryRouter>);
  expect(await screen.findByRole("region", {name: "本轮测试结果"}))
    .toHaveTextContent("请补充测试身份。");
  expect(screen.queryByText("提交或选择任务后查看执行范围与结果。"))
    .not.toBeInTheDocument();
});


test("an old complete flag cannot hide a known unexecuted negative scenario", async () => {
  const old: GoalTask = {
    id: "e".repeat(32), project_id: "web_old", project_identity: "v1",
    request_sha256: "f".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: true, checks_passed: true, cases: [{
      case_id: "one", title: "错误参数检查", operation_id: "read", status: "FINISHED",
      outcome: "PASS", max_business_requests: 1, kinds: ["negative"], approved_negative_steps: 0}]},
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/task-session") return response({csrf_token: "local"});
    if (url === "/api/projects") return response({projects: []});
    if (url === "/api/project-intake") return response({projects: [], profiles: []});
    if (url === "/api/tasks") return response({tasks: [old]});
    if (url === `/api/tasks/${old.id}`) return response(old);
    if (url === "/api/saved-tests") return response({tests: []});
    if (url.endsWith("/timeline")) return response({events: []});
    throw new Error("Unexpected request");
  }));
  render(<MemoryRouter initialEntries={[`/tasks?task=${old.id}`]}><GoalTasks /></MemoryRouter>);
  expect(await screen.findByRole("heading", {name: "本次未全部完成"})).toBeVisible();
  expect(screen.queryByRole("heading", {name: "本轮方案执行完成"})).not.toBeInTheDocument();
  expect(screen.getByText(/1 项方案标为异常场景/)).toBeVisible();
});
