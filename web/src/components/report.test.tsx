import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { outcomes, taskState, triState } from "../status";
import { ReportView } from "../pages/RunDetail";
import { ValidationLayers } from "./ValidationLayers";
import { StepEvidence } from "./StepEvidence";
import { runFixture } from "../test/fixtures";
import type { Outcome } from "../api/types";

test.each([
  [true, "目标拒绝符合批准预期。"],
  [false, "目标响应违反批准预期。"],
  [null, "尚无完整负例断言结果。"],
] as const)(
  "approved negative verdict %s stays distinct from infrastructure failure",
  async (value, label) => {
    render(
      <StepEvidence
        index={0}
        step={{
          step_id: "reject",
          operation_id: "validateRecord",
          method: "POST",
          path_template: "/validation-records",
          outcome: "PASS",
          reason: "POLICY_EVALUATED",
          policy_sha256: "hash",
          checks: [],
          attempts: [],
          input_validation: "APPROVED_NEGATIVE",
          negative_rejected_as_expected: value,
        }}
      />,
    );
    await userEvent.click(screen.getByText("证据 ↓"));
    expect(screen.getByText("已批准的非法输入")).toBeVisible();
    expect(screen.getByText(label)).toBeVisible();
  },
);

test("rule symptoms and model abstention remain separate from business failure", () => {
  render(
    <MemoryRouter>
      <ReportView
        refresh={() => {}}
        run={runFixture({
          outcome: "ASSERTION_FAILED",
          trusted_symptom: {
            state: "SUPPORTED",
            finding: "ACCESS_BOUNDARY_VIOLATION",
            evidence_refs: ["original", "investigation_" + "a".repeat(32)],
            reason: "RULE_SUPPORTED",
            rule_version: "observable_symptoms_v1",
          },
          feedback: {
            state: "NEEDS_REVIEW",
            finding: "EVIDENCE_INSUFFICIENT",
            reason: "FEEDBACK_EVIDENCE_INSUFFICIENT",
            basis: "证据不足，需人工复核。",
          },
        })}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText("规则支持的可信症状")).toBeInTheDocument();
  expect(screen.getByText("模型行动与结论建议")).toBeInTheDocument();
  expect(screen.getByText("ACCESS_BOUNDARY_VIOLATION")).toBeInTheDocument();
  expect(screen.getByText("EVIDENCE_INSUFFICIENT")).toBeInTheDocument();
  expect(screen.getByText(/ASSERTION_FAILED ·/)).toBeInTheDocument();
  expect(
    screen.getByText(/FEEDBACK_EVIDENCE_INSUFFICIENT/),
  ).toBeInTheDocument();
});

test.each(Object.keys(outcomes) as Outcome[])(
  "renders truthful outcome %s",
  (outcome) => {
    render(
      <MemoryRouter>
        <ReportView run={runFixture({ outcome })} refresh={() => {}} />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(`${outcome} · ${outcomes[outcome].label}`),
    ).toBeInTheDocument();
    expect(screen.getByText(outcomes[outcome].description)).toBeInTheDocument();
  },
);
test.each([
  [true, "任务已满足"],
  [false, "任务未满足"],
  [null, "不适用 / 未确认"],
  [undefined, "未记录"],
] as const)("task_satisfied %s", (value, label) => {
  expect(taskState(runFixture({ task_satisfied: value })).label).toBe(label);
  render(<ValidationLayers run={runFixture({ task_satisfied: value })} />);
  expect(screen.getAllByText(label).length).toBeGreaterThan(0);
});
test("legacy values are not coerced and manual PASS never becomes NL success", () => {
  expect(triState(undefined).label).toBe("未记录");
  expect(triState(null).tone).toBe("neutral");
  render(
    <MemoryRouter>
      <ReportView
        run={runFixture({
          task_mode: "operator_plan",
          task_satisfied: true,
          workflow_completed: true,
        })}
        refresh={() => {}}
      />
    </MemoryRouter>,
  );
  expect(screen.queryByText("任务已满足")).not.toBeInTheDocument();
  expect(screen.getByText(/不表示自然语言目标已经验证/)).toBeInTheDocument();
  expect(screen.getByText("Workflow · 已完整结束")).toBeInTheDocument();
});
test("step expansion exposes only allowed evidence and masks unexpected assertion values", async () => {
  render(
    <StepEvidence
      index={0}
      step={{
        step_id: "lookup",
        operation_id: "readResource",
        method: "GET",
        path_template: "/resource/{id}",
        outcome: "ASSERTION_FAILED",
        reason: "ASSERTION_FAILED",
        policy_sha256: "hash",
        checks: [
          {
            kind: "field",
            expected: "secret-token",
            actual: { token: "hidden" },
            passed: false,
          },
        ],
        attempts: [
          {
            attempt: 1,
            elapsed_ms: 17,
            status_code: 200,
            error_code: "CHECK_FAILED",
          },
        ],
      }}
    />,
  );
  await userEvent.click(screen.getByText("lookup"));
  expect(screen.getByText("CHECK_FAILED")).toBeVisible();
  expect(screen.getAllByText("[REDACTED]")).toHaveLength(2);
  expect(screen.queryByText("secret-token")).not.toBeInTheDocument();
});
test("unknown model usage stays unknown and actual investigations retain original failure", () => {
  const run = runFixture({
    outcome: "ASSERTION_FAILED",
    model_call: {
      calls: 1,
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
    },
    investigations: [
      {
        parent_step_id: "original",
        capability_id: "approved-read",
        step_id: "inspect",
        operation_id: "getResource",
        method: "GET",
        path_template: "/resource/{id}",
        outcome: "PASS",
        policy_sha256: "a",
        reason: "OK",
        requests_used: 1,
        rounds_used: 1,
      },
    ],
  });
  render(
    <MemoryRouter>
      <ReportView run={run} refresh={() => {}} />
    </MemoryRouter>,
  );
  expect(screen.getByText("未知 / 未知")).toBeInTheDocument();
  expect(screen.getByText("approved-read")).toBeInTheDocument();
  expect(screen.getByText("ASSERTION_FAILED · 断言失败")).toBeInTheDocument();
});

test("missing legacy counters never become recorded zeros", () => {
  const run = runFixture();
  delete run.steps;
  delete run.investigations;
  render(
    <MemoryRouter>
      <ReportView run={run} refresh={() => {}} />
    </MemoryRouter>,
  );
  expect(
    screen.getByText("执行步骤", { selector: "span" }).parentElement,
  ).toHaveTextContent("未记录");
  expect(screen.getByText("业务 HTTP 尝试").parentElement).toHaveTextContent(
    "未记录",
  );
  expect(screen.getByText("未记录调查")).toBeInTheDocument();
});

test("approved HTTP status arrays remain readable", () => {
  render(
    <StepEvidence
      index={0}
      step={{
        step_id: "read",
        operation_id: "getResource",
        method: "GET",
        path_template: "/resources/{id}",
        outcome: "PASS",
        reason: "OK",
        policy_sha256: "hash",
        checks: [
          {
            kind: "http_status",
            passed: true,
            expected: [200, 201],
            actual: 200,
          },
        ],
      }}
    />,
  );
  expect(screen.getByText("200, 201")).toBeInTheDocument();
});

test("supported investigation keeps original failure and exposes evidence references", () => {
  render(
    <MemoryRouter>
      <ReportView
        refresh={() => {}}
        run={runFixture({
          outcome: "ASSERTION_FAILED",
          task_satisfied: false,
          feedback: {
            state: "SUPPORTED",
            finding: "PERSISTENT_READBACK_MISMATCH",
            basis: "同角色对同一资源的复核重复出现字段断言失败。",
            evidence_refs: ["original", "inspection_1"],
            requests_used: 1,
            model_calls_used: 2,
            decisions: [
              { index: 0, action: "investigate", reason: "FEEDBACK_PENDING" },
            ],
          },
        })}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText("SUPPORTED")).toBeInTheDocument();
  expect(
    screen.getByText(/证据引用：original · inspection_1/),
  ).toBeInTheDocument();
  expect(screen.getByText("ASSERTION_FAILED · 断言失败")).toBeInTheDocument();
});
