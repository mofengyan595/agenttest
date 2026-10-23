import { expect, test } from "@playwright/test";
import path from "node:path";

const shots = path.resolve("test-results/screenshots");
test("registered goal: session protected submission, progress, evidence and narrow layout", async ({
  page,
}) => {
  await page.goto("/tasks");
  await expect(page.getByRole("option", { name: "tickets" })).toBeAttached();
  await page.getByLabel("测试需求").fill("测试创建工单接口");
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await expect(page.getByText("PASS", { exact: true })).toBeVisible();
  await expect(page.getByText("已满足", { exact: false })).toBeVisible();
  const archive = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 pytest 回归包" }).click();
  expect((await archive).suggestedFilename()).toBe("agenttest-regression.zip");
  await page.screenshot({
    path: path.join(shots, "goals-desktop.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: /tickets.*已结束/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /tickets.*已结束/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(shots, "goals-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(391);
  await page.getByRole("link", { name: "查看业务、模型与规则证据" }).click();
  await expect(
    page.getByText("open / openTicket", { exact: true }),
  ).toBeVisible();
});
test("derived suite: source, validation, export and mobile layout", async ({
  page,
}) => {
  await page.goto("/tasks");
  await expect(page.getByRole("option", { name: "tickets" })).toBeAttached();
  await page.getByLabel("测试需求").fill("请测试新建工单接口");
  await page.getByLabel("包含 Schema 用例候选").check();
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await expect(page.getByText(/合法基础计划的规则派生/).first()).toBeVisible();
  await expect(page.getByText(/结构校验通过/).first()).toBeVisible();
  await expect(
    page.getByRole("link", { name: "查看基础计划" }).first(),
  ).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出该用例 pytest" }).last().click();
  expect((await download).suggestedFilename()).toBe("agenttest-regression.zip");
  await page.screenshot({
    path: path.join(shots, "derivation-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(391);
  await page.screenshot({
    path: path.join(shots, "derivation-mobile.png"),
    fullPage: true,
  });
});

test("display fixture: trusted rule and model abstention stay visibly separate", async ({
  page,
  request,
}) => {
  const catalog = await (await request.get("/api/runs?limit=100")).json();
  const selected = catalog.items.find(
    (row: { outcome: string }) => row.outcome === "ASSERTION_FAILED",
  );
  expect(selected).toBeTruthy();
  const report = await (
    await request.get(`/api/runs/${selected.run_id}`)
  ).json();
  expect(report.investigations[0]?.step_id).toBeTruthy();
  // UI fixture only; execution/rule correctness is covered by ASGI Python tests.
  report.trusted_symptom = {
    state: "SUPPORTED",
    finding: "ACCESS_BOUNDARY_VIOLATION",
    reason: "RULE_SUPPORTED",
    rule_version: "observable_symptoms_v1",
    evidence_refs: ["original", report.investigations[0].step_id],
  };
  report.feedback = {
    state: "NEEDS_REVIEW",
    finding: "EVIDENCE_INSUFFICIENT",
    reason: "FEEDBACK_EVIDENCE_INSUFFICIENT",
    basis: "证据不足，需人工复核。",
  };
  await page.route(`**/api/runs/${selected.run_id}`, (route) =>
    route.fulfill({ json: report }),
  );
  await page.goto(`/runs/${selected.run_id}`);
  await expect(
    page.getByRole("heading", { name: "规则支持的可信症状" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "模型行动与结论建议" }),
  ).toBeVisible();
  await expect(
    page.locator(".run-overview").getByText(/ASSERTION_FAILED ·/),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(shots, "rule-model-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: path.join(shots, "rule-model-390.png"),
    fullPage: true,
  });
});

test("display fixture: group budgets, excluded coverage and approved rejection remain explicit", async ({
  page,
  request,
}) => {
  const catalog = await (await request.get("/api/runs?limit=100")).json();
  const selected = catalog.items.find(
    (row: { outcome: string }) => row.outcome === "PASS",
  );
  expect(selected).toBeTruthy();
  const report = await (
    await request.get(`/api/runs/${selected.run_id}`)
  ).json();
  // Display fixture only; actual negative requests and assertions are verified in Python/ASGI.
  report.steps[0] = {
    ...report.steps[0],
    step_id: "approved_missing",
    operation_id: "validateRecord",
    method: "POST",
    path_template: "/validation-records",
    input_validation: "APPROVED_NEGATIVE",
    negative_rejected_as_expected: true,
    checks: [
      { kind: "http_status", expected: [422], actual: 422, passed: true },
    ],
    attempts: [{ ...report.steps[0].attempts[0], status_code: 422 }],
  };
  await page.route(`**/api/runs/${selected.run_id}`, (route) =>
    route.fulfill({ json: report }),
  );
  const task = {
    id: "f".repeat(32),
    project_id: "tickets",
    project_identity: "b".repeat(64),
    request_sha256: "c".repeat(64),
    status: "FINISHED",
    plan_version: 1,
    created_at: 1,
    summary: {
      budget_preview: {
        required_business_requests: 11,
        required_planning_calls: 6,
        optional_readonly_requests: 1,
        optional_model_calls: 0,
        planning_token_upper_bound: 144576,
        complete_project_within_budget: false,
        executable_groups: ["negative"],
        excluded_groups: [
          { case_id: "excluded", reason: "SUITE_BUDGET_OR_USAGE_STOP" },
        ],
      },
      cases: [
        {
          case_id: "negative",
          operation_id: "validateRecord",
          status: "FINISHED",
          outcome: "PASS",
          run_id: selected.run_id,
          kinds: ["approved_negative:missing"],
          max_business_requests: 1,
          approved_negative_steps: 1,
          negative_rejections_passed: 1,
        },
        {
          case_id: "excluded",
          operation_id: "listTickets",
          status: "NOT_EXECUTED",
          max_business_requests: 1,
          reason: "SUITE_BUDGET_OR_USAGE_STOP",
        },
      ],
      coverage: {
        operation_denominator: 2,
        scenario_denominator: 2,
        executed_operations: 1,
        executed_scenarios: 1,
        planned_scenarios: 1,
        assertion_passed_scenarios: 1,
        not_executed: 1,
        assertions_evaluated: 2,
        assertions_passed: 2,
      },
    },
  };
  await page.route("**/api/tasks", (route) =>
    route.fulfill({
      json: route.request().method() === "GET" ? { tasks: [] } : task,
    }),
  );
  await page.goto("/tasks");
  await expect(page.getByRole("option", { name: "tickets" })).toBeAttached();
  await page.getByLabel("测试需求").fill("测试项目核心接口");
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await expect(page.getByLabel("分组预算预览")).toContainText(
    "必需业务请求 11",
  );
  await expect(page.getByText(/本组未执行，保留在覆盖分母中/)).toBeVisible();
  await expect(page.getByText(/按预期拒绝 1\/1/)).toBeVisible();
  await page.screenshot({
    path: path.join(shots, "trusted-budget-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(391);
  await page.screenshot({
    path: path.join(shots, "trusted-budget-390.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "查看分层证据" }).click();
  await page
    .getByText("approved_missing / validateRecord", { exact: true })
    .click();
  await expect(page.getByText("目标拒绝符合批准预期。")).toBeVisible();
  await page.screenshot({
    path: path.join(shots, "approved-negative-390.png"),
    fullPage: true,
  });
});

test("real API: draft, import, history, evidence and responsive layouts", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await request.get("/api/runs?limit=100");
  expect(response.ok()).toBeTruthy();
  const catalog = await response.json();
  expect(catalog.total).toBe(13);
  expect(catalog.skipped).toBe(2);
  await page.goto("/");
  await expect(page.getByText("API 已连接")).toBeVisible();
  await page
    .getByLabel("自然语言测试目标")
    .fill("使用管理员账号创建一个资源，再查询并验证其属性一致性。");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "保存本地草稿" }).click();
  expect((await download).suggestedFilename()).toBe("agenttest-task-draft.txt");
  await page
    .getByLabel("上传 OpenAPI 契约")
    .setInputFiles(path.resolve("../tests/fixtures/contract.yaml"));
  await page.getByRole("button", { name: "解析契约" }).click();
  await expect(page.getByText("解析完成")).toBeVisible();
  await page.keyboard.press("Control+Home");
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.screenshot({
    path: path.join(shots, "workbench-desktop.png"),
    fullPage: true,
  });
  await page.getByLabel("或粘贴契约文本").fill("password: not-an-openapi");
  await page.getByRole("button", { name: "解析契约" }).click();
  await expect(page.getByRole("alert")).toContainText("输入格式无效");
  await page.getByRole("link", { name: "历史测试报告", exact: true }).click();
  await expect(page.getByRole("heading", { name: "运行记录" })).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(12);
  await expect(page.getByLabel("上一页", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("下一页", { exact: true })).toBeEnabled();
  await expect(
    page.getByText("2 个无效、不完整或不可读取的报告已跳过。"),
  ).toBeVisible();
  const desktopIds = await page.locator("tbody tr a").allTextContents();
  await page.keyboard.press("Control+Home");
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.screenshot({
    path: path.join(shots, "history-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".run-cards")).toBeVisible();
  await expect(page.locator(".run-table")).toBeHidden();
  await expect(page.locator(".run-card")).toHaveCount(12);
  expect(await page.locator(".run-card a").allTextContents()).toEqual(
    desktopIds,
  );
  await expect(
    page
      .locator(".run-card")
      .first()
      .getByText(/PASS|ASSERTION_FAILED/),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: path.join(shots, "history-390.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.getByLabel("下一页", { exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.getByLabel("上一页", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("下一页", { exact: true })).toBeDisabled();
  await expect(page.locator("tbody tr")).toContainText(
    catalog.items[12].run_id,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".run-card")).toHaveCount(1);
  await expect(page.locator(".run-card")).toContainText(
    catalog.items[12].run_id,
  );
  await page.getByLabel("上一页", { exact: true }).click();
  await expect(page.locator(".run-card")).toHaveCount(12);
  await expect(page.getByLabel("上一页", { exact: true })).toBeDisabled();
  await page.getByLabel("下一页", { exact: true }).click();
  await expect(page.locator(".run-card")).toHaveCount(1);
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page
    .getByLabel("结果", { exact: true })
    .selectOption("ASSERTION_FAILED");
  await expect(page.locator("tbody tr").first()).toContainText(
    "ASSERTION_FAILED",
  );
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await expect(page.getByLabel("上一页", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("下一页", { exact: true })).toBeDisabled();
  await page.getByLabel("结果", { exact: true }).selectOption("TOOL_ERROR");
  await expect(page.getByText("没有符合条件的报告")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(0);
  await expect(page.getByLabel("上一页", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("下一页", { exact: true })).toBeDisabled();
  await page.getByLabel("结果", { exact: true }).selectOption("");
  await expect(page.locator("tbody tr")).toHaveCount(12);
  let investigationId: string | undefined;
  let legacyId: string | undefined;
  for (const row of catalog.items) {
    const report = await (await request.get(`/api/runs/${row.run_id}`)).json();
    if (report.investigations?.length) investigationId = row.run_id;
    if (!Object.hasOwn(report, "dsl_valid")) {
      legacyId = row.run_id;
      expect(report).not.toHaveProperty("workflow_completed");
      expect(report).not.toHaveProperty("task_satisfied");
    }
  }
  expect(legacyId).toBeTruthy();
  await page.goto(`/runs/${legacyId}`);
  await expect(
    page
      .locator(".validation-card")
      .filter({ hasText: "DSL Validity" })
      .getByText("未记录", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Workflow · 未记录", { exact: true }),
  ).toBeVisible();
  expect(investigationId).toBeTruthy();
  await page.goto(`/runs/${investigationId}`);
  await expect(
    page.getByRole("heading", { name: "四层校验结果" }),
  ).toBeVisible();
  await expect(page.getByText("任务未满足").first()).toBeVisible();
  await expect(page.getByText("1 次调查", { exact: true })).toBeVisible();
  await page.locator("details summary").first().click();
  await expect(page.getByText("CheckResult · 批准断言").first()).toBeVisible();
  await page.keyboard.press("Control+Home");
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.screenshot({
    path: path.join(shots, "run-detail-desktop.png"),
    fullPage: true,
  });
  for (const viewport of [
    { width: 768, height: 1024 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await expect(
      page.getByRole("heading", { name: "四层校验结果" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.keyboard.press("Control+Home");
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await page.screenshot({
      path: path.join(shots, `run-detail-${viewport.width}.png`),
      fullPage: true,
    });
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "你想验证什么？" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.keyboard.press("Control+Home");
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await page.screenshot({
      path: path.join(shots, `workbench-${viewport.width}.png`),
      fullPage: true,
    });
    await page.goto(`/runs/${investigationId}`);
  }
  expect(errors).toEqual([]);
});

test("real API: missing report and invalid ID", async ({ page }) => {
  await page.goto("/runs/not-a-run");
  await expect(
    page.getByRole("heading", { name: "Run ID 格式无效" }),
  ).toBeVisible();
  for (const character of ["0", "d", "e"]) {
    await page.goto(`/runs/${character.repeat(32)}`);
    await expect(page.getByRole("alert")).toContainText("未找到报告");
  }
});
