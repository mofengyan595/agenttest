import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import type { GoalTask } from "../api/tasks";
import { ProductResult } from "./ProductResult";

test("source field omissions remain visible without claiming execution or full coverage", () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "NEEDS_REVIEW", plan_version: 1, created_at: 1,
    summary: {complete: false, requirement_ledger: [{name: "size", value: 0,
      source_quote: "size=0", start: 2, end: 8, deferred: false,
      candidate_operations: ["list"], status: "UNACCOUNTED"}]},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false}
    save={vi.fn()} answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText("size=0")).toBeVisible();
  expect(screen.getByText(/规划漏项/)).toBeVisible();
  expect(screen.getByText(/对应到规划不代表已经执行/)).toBeVisible();
});

test("original clause mapping, actual requests and extra advice stay distinct", () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {complete: false, requirement_sources: [{source_id: "request_1",
      text: "读取后删除同一资源", start: 0, end: 9}],
      focus: {mode: "selected", interpretation: "读取并清理", operation_ids: ["read", "delete"],
        question: null, requirements: [{source_id: "request_1", source_quote: "读取后删除同一资源",
          disposition: "required", actions: [{operation_id: "read", role: "owner"},
            {operation_id: "delete", role: "owner"}], before: [{earlier: "read", later: "delete"}],
          same_resource: [{producer: "create", consumer: "read"}]}]},
      cases: [{case_id: "one", operation_id: null, status: "FINISHED", max_business_requests: 1,
        evidence: [{operation_id: "read", role: "owner", executed: true, method: "GET",
          path: "/items/{id}", outcome: "ASSERTION_FAILED", checks: []}]}],
      suggestions: ["可另行测试更大的分页范围"]},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false}
    save={vi.fn()} answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText("读取后删除同一资源")).toBeVisible();
  expect(screen.getByText(/已有请求记录，需核对下方断言/)).toBeVisible();
  expect(screen.getByText(/尚无对应请求记录/)).toBeVisible();
  expect(screen.getByText("额外测试建议（未在本轮执行）")).toBeVisible();
});

test("a rejected design with no cases exposes the stop reason directly", () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {complete: false, cases: [], design_records: [{group_id: "group_1",
      status: "REJECTED", reason: "DESIGN_DUPLICATE_RESOURCE_SETUP"}]},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false}
    save={vi.fn()} answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText(/本组未执行：模型在不同场景重复创建/)).toBeVisible();
});

test("mixed content checks do not hide requests checked only for rejection status", () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {cases: [{case_id: "mixed", operation_id: null, status: "FINISHED",
      outcome: "PASS", max_business_requests: 2, evidence: [{operation_id: "items",
        outcome: "PASS", method: "GET", path: "/items",
        checks: [{kind: "equals", selector: "$.total", expected: 1, actual: 1, passed: true}]},
      {operation_id: "restricted", outcome: "PASS", method: "GET", path: "/restricted",
        checks: [{kind: "http_status", selector: null,
        expected: [403], actual: 403, passed: true}]}]}]},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false}
    save={vi.fn()} answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText(/本场景包含只检查状态码的请求/)).toBeVisible();
  expect(screen.queryByText(/本轮仅检查 HTTP 状态码/)).not.toBeInTheDocument();
});

test("passing ordinary scenarios do not hide an unexecuted explicitly requested negative check", () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: false,
      coverage: {operation_denominator: 6, scenario_denominator: 2,
        requested_check_denominator: 3, executed_requested_checks: 2,
        completed_scenarios: 2, assertion_passed_scenarios: 2}},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false}
    save={vi.fn()} answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText(/明确指定的异常检查：已执行 2\/3/)).toBeVisible();
  expect(screen.queryByText("全部通过")).not.toBeInTheDocument();
});

test("ambiguous input choices supplement a new task without approving business expectations", async () => {
  const choose = vi.fn(), answer = vi.fn();
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: false, questions: [{id: "input",
      operation_id: "create", kind: "clarification", requested_behavior: "确认输入",
      question: "名称用春季资料还是春季资料任务？", input_label: "name",
      input_choices: ["春季资料", "春季资料任务"]}]},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false} save={vi.fn()}
    answer={answer} chooseInput={choose} /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", {name: "选择“春季资料”并补充"}));
  expect(choose).toHaveBeenCalledWith("name", "春季资料");
  expect(answer).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", {name: /确认应返回/})).not.toBeInTheDocument();
});

test("a cancelled partial flow is not counted as a completed scenario or an environment fault", () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "CANCELLED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", cases: [{case_id: "one", operation_id: null,
      title: "创建后读取", status: "CANCELLED", outcome: "TEST_INFRA_ERROR",
      max_business_requests: 4, planned_steps: 4, executed_steps: 1}],
      coverage: {operation_denominator: 4, scenario_denominator: 1, executed_scenarios: 1,
        started_scenarios: 1, completed_scenarios: 0, executed_operations: 1,
        assertion_passed_scenarios: 0}},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false} save={vi.fn()} answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText(/本轮场景：完整执行 0\/1/)).toBeInTheDocument();
  expect(screen.getByText(/本轮已取消，部分请求可能已经完成/)).toBeInTheDocument();
  expect(screen.queryByText(/存在环境或传输故障/)).not.toBeInTheDocument();
});

test("result separates actual failure, missing scope, model suggestions and a concrete answer", async () => {
  const answer = vi.fn(),
    save = vi.fn();
  const task: GoalTask = {
    id: "a".repeat(32),
    project_id: "web_new",
    project_identity: "v1",
    request_sha256: "b".repeat(64),
    status: "FINISHED",
    plan_version: 1,
    created_at: 1,
    summary: {
      mode: "MODEL_DESIGNED",
      complete: false,
      task_satisfied: null,
      coverage: {
        operation_denominator: 45,
        scenario_denominator: 1,
        executed_operations: 1,
        executed_scenarios: 1,
        assertion_passed_scenarios: 0,
      },
      questions: [
        {
          id: "q",
          operation_id: "get",
          kind: "permission",
          role: "other",
          question: "另一身份访问此资源应返回 403 还是 404？",
          requested_behavior: "读取他人资源",
          status_choices: [403, 404],
        },
      ],
      not_covered: ["上传尚未支持"],
      cases: [
        {
          case_id: "case1",
          title: "检查资源状态",
          operation_id: "get",
          status: "FINISHED",
          max_business_requests: 1,
          run_id: "run1",
          outcome: "ASSERTION_FAILED",
          kinds: ["normal"],
          evidence: [
            {
              operation_id: "get",
              method: "GET",
              path: "/items",
              outcome: "ASSERTION_FAILED",
              checks: [
                {
                  kind: "status",
                  selector: null,
                  expected: [200],
                  actual: 500,
                  passed: false,
                },
              ],
            },
          ],
          investigations: [
            {
              operation_id: "get",
              method: "GET",
              path: "/items",
              requests: 1,
              outcome: "ASSERTION_FAILED",
              reason: "read-probe",
              cause_confirmed: false,
              checks: [],
            },
          ],
        },
      ],
    },
  };
  render(
    <MemoryRouter>
      <ProductResult task={task} busy={false} answer={answer} save={save} />
    </MemoryRouter>,
  );
  expect(screen.getByText("文档接口：已请求 1/45")).toBeInTheDocument();
  expect(screen.getByText(/预期 \[200\]，实际 500/)).toBeInTheDocument();
  expect(screen.getByText(/内部原因尚未确认/)).toBeInTheDocument();
  expect(screen.getByText("上传尚未支持")).toBeInTheDocument();
  expect(screen.queryByText("全部通过")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "确认应返回 403" }));
  expect(answer).toHaveBeenCalledWith("q", 403);
  expect(
    screen.queryByRole("button", { name: "保存测试" }),
  ).not.toBeInTheDocument();
});


test("a historical negative label cannot present an ordinary200 as a completed negative", () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_old", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: true, cases: [{case_id: "one",
      title: "模型命名的错误参数场景", operation_id: "read", status: "FINISHED", outcome: "PASS",
      max_business_requests: 1, kinds: ["negative"], approved_negative_steps: 0,
      negative_rejections_passed: 0, run_id: "r", evidence: [{operation_id: "read",
        method: "GET", path: "/items", outcome: "PASS", checks: [{kind: "http_status",
          selector: null, expected: [200], actual: 200, passed: true}]}]}],
      exports: {r: {available: true, reason: "旧记录仍保留原导出提示。"}}},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false} save={vi.fn()}
    answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText("模型方案类型：异常参数")).toBeVisible();
  expect(screen.getByText("异常检查未执行")).toBeVisible();
  expect(screen.queryByText("检查通过", {exact: true})).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "保存测试"})).not.toBeInTheDocument();
});


test("project capability coverage stays separate from this task and pages every remaining check", async () => {
  const task: GoalTask = {
    id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: true,
      coverage: {operation_denominator: 48, scenario_denominator: 1,
        approved_check_denominator: 25, attempted_approved_checks: 1,
        requested_check_denominator: 1, executed_requested_checks: 1},
      uncovered_negative_checks: Array.from({length: 24}, (_, i) => ({policy_id: `c${i}`,
        operation_id: "read", field: `field${i}`, kind: "type_mismatch", reason: "本轮未安排"}))},
  };
  render(<MemoryRouter><ProductResult task={task} busy={false} save={vi.fn()}
    answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText(/明确指定的异常检查：已执行 1\/1/)).toBeVisible();
  expect(screen.getByText(/本轮尝试的已批准异常检查：1\/25/)).toBeVisible();
  await userEvent.click(screen.getByText("项目中本轮未尝试的已批准异常检查（24 项）"));
  expect(screen.getByText(/field0：参数类型/)).toBeVisible();
  expect(screen.queryByText(/field23：参数类型/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", {name: "下一组检查"}));
  expect(screen.getByText(/field23：参数类型/)).toBeVisible();
  expect(screen.getByRole("button", {name: "下一组检查"})).toBeDisabled();
});

test("a rejected independent scenario explains missing login while keeping its original code", () => {
  const task: GoalTask = {id: "a".repeat(32), project_id: "web_new", project_identity: "v1",
    request_sha256: "b".repeat(64), status: "FINISHED", plan_version: 1, created_at: 1,
    summary: {mode: "MODEL_DESIGNED", complete: false, cases: [{case_id: "rejected",
      operation_id: "create", title: "独立异常检查", status: "REJECTED",
      reason: "DSL_AUTHENTICATION_ORDER", max_business_requests: 1}]}};
  render(<MemoryRouter><ProductResult task={task} busy={false} save={vi.fn()}
    answer={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText(/本场景没有单独登录，相关请求未发送/)).toBeVisible();
  expect(screen.queryByText("DSL_AUTHENTICATION_ORDER", {exact: true})).not.toBeInTheDocument();
  expect(task.summary.cases![0].reason).toBe("DSL_AUTHENTICATION_ORDER");
});
