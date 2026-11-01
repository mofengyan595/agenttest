import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const shots = path.resolve("test-results/screenshots");

async function waitForTerminalTask(page: Page, taskId: string, timeout = 120_000) {
  await expect.poll(async () => {
    const response = await page.request.get(`/api/tasks/${taskId}`);
    expect(response.ok()).toBe(true);
    return ["FINISHED", "NEEDS_REVIEW", "CANCELLED", "INTERRUPTED"].includes(
      (await response.json()).status,
    );
  }, {timeout, intervals: [1000]}).toBe(true);
}

async function startTask(page: Page, timeout = 120_000) {
  const submitting = page.waitForResponse(
    (r) => r.url().endsWith("/api/tasks") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  const response = await submitting;
  expect(response.status()).toBe(202);
  const task = await response.json();
  await waitForTerminalTask(page, task.id, timeout);
  return task;
}

async function intake(
  page: Page,
  domain: string,
  name: string,
  confirmed = true,
) {
  await page.goto("/");
  await page.getByLabel("项目名称", { exact: true }).fill(name);
  await page
    .getByLabel("接口文档（OpenAPI 3.0 / 3.1，JSON 或 YAML）", { exact: true })
    .setInputFiles(
      path.resolve(
        `../examples/trusted-testing/projects/${domain}/openapi.json`,
      ),
    );
  await page.getByLabel("服务端授权配置", { exact: true }).selectOption(domain);
  if (confirmed)
    await page.getByLabel("我已核对以上行为和预期，确认适用于此项目").check();
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/project-intake") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "保存并检查接入", exact: true })
    .click();
  const created = await response;
  expect(created.status()).toBe(201);
  const project = await created.json();
  expect(project.id).toMatch(/^web_/);
  expect(project.id).not.toBe(domain);
  expect(project.ready).toBe(confirmed);
  return project;
}

test("browser creates two NEW projects, refreshes, executes flow, saves and runs fixed regression", async ({
  page,
}) => {
  test.setTimeout(360_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const ticket = await intake(page, "tickets", "浏览器新建工单");
  await expect(page.getByText("已保存并登记，可以开始测试。")).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "浏览器新建工单", exact: true }),
  ).toBeVisible();
  const card = page.locator("section.panel").filter({
    has: page.getByRole("heading", { name: "浏览器新建工单", exact: true }),
  });
  await expect(card.getByText("可提交测试", { exact: true })).toBeVisible();
  await page.screenshot({
    path: path.join(shots, "intake-projects-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(shots, "intake-projects-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await card.getByRole("link", { name: "开始一轮测试", exact: true }).click();
  await expect(page.getByLabel("选择项目", { exact: true })).toHaveValue(
    ticket.id,
  );
  await page
    .getByRole("button", { name: "测试创建工单接口", exact: true })
    .click();
  await expect(page.getByLabel("测试需求")).toHaveValue("测试创建工单接口");
  // Examples only fill text. No submit, model call or business request occurs here.
  await expect(
    page.getByRole("heading", { name: "本次需求已完成", exact: true }),
  ).toHaveCount(0);
  await page
    .getByLabel("测试需求")
    .fill("创建工单，关闭工单，查询工单，其他用户不能读取工单");
  const submitted = await startTask(page);
  await expect(
    page.getByRole("heading", { name: "本次需求已完成", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/执行目标：.*内存模拟目标/).last()).toBeVisible();
  await expect(page.getByText(/已记录步骤 4\/4/)).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.screenshot({
    path: path.join(shots, "intake-assistant-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(shots, "intake-assistant-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  const downloadEvent = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "导出 pytest 回归包", exact: true })
    .click();
  const download = await downloadEvent;
  await download.saveAs(path.resolve("test-results/intake-export.zip"));
  await page.getByRole("link", { name: "已保存测试", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已保存的固定测试", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "再次运行固定测试", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "固定回归检查通过", exact: true }),
  ).toBeVisible({timeout: 60_000});
  await expect(
    page.getByText("规划来源：已保存固定计划，模型禁用", { exact: false }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(shots, "intake-records-mobile.png"),
    fullPage: true,
  });
  const fixedTask = new URL(page.url()).searchParams.get("task");
  expect(fixedTask).not.toBe(submitted.id);
  const fixed = await (
    await page.request.get(`/api/tasks/${fixedTask}`)
  ).json();
  expect(fixed.summary.context_usage.model_calls).toBe(0);
  expect(fixed.summary.task_satisfied).toBeNull();
  expect(fixed.summary.outcome).toBe("PASS");

  const course = await intake(page, "courses", "浏览器新建课程");
  await page.goto(`/tasks?project=${course.id}`);
  await expect(page.getByLabel("选择项目", { exact: true })).toHaveValue(
    course.id,
  );
  await page.getByLabel("测试需求").fill("创建课程，查询课程");
  await startTask(page);
  await expect(
    page.getByRole("heading", { name: "本次需求已完成", exact: true }),
  ).toBeVisible({timeout: 60_000});
  await expect(page.getByText(/已记录步骤 3\/3/)).toBeVisible();
  expect(errors).toEqual([]);
});

test("single and project scope, unknown requirement, pending intake and unchanged full denominator", async ({
  page,
}) => {
  test.setTimeout(360_000);
  const draft = await intake(page, "tickets", "待补充接入", false);
  expect(draft.missing.join(" ")).toContain("确认当前版本");
  await expect(
    page.getByText("接入进度已保存，请补充下方缺失项。"),
  ).toBeVisible();
  await page.goto(`/tasks?project=${draft.id}`);
  await expect(
    page.getByRole("button", { name: "开始测试", exact: true }),
  ).toBeDisabled();
  const project = await intake(page, "tickets", "三层任务验证");
  await page.goto(`/tasks?project=${project.id}`);
  await expect(page.getByLabel("选择项目", { exact: true })).toHaveValue(
    project.id,
  );
  await page.getByLabel("测试需求").fill("测试创建工单接口");
  await startTask(page);
  await expect(
    page.getByRole("heading", { name: "本次需求已完成", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "新建独立任务", exact: true }).click();
  await page.getByLabel("测试需求").fill("测试工单项目核心接口");
  await startTask(page, 180_000);
  await expect(page.getByText(/已执行接口/)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "本次需求已完成", exact: true }),
  ).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "新建独立任务", exact: true }).click();
  await page
    .getByLabel("选择项目", { exact: true })
    .selectOption("limited_tickets");
  await page.getByLabel("测试需求").fill("测试工单项目核心接口");
  await startTask(page);
  await expect(
    page.getByRole("heading", { name: "本次需求未全部完成", exact: true }),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/未执行/).first()).toBeVisible();
  await page.screenshot({
    path: path.join(shots, "intake-partial-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "新建独立任务", exact: true }).click();
  await page.getByLabel("选择项目", { exact: true }).selectOption(project.id);
  await page.getByLabel("测试需求").fill("测试工单退款金额是否正确");
  await startTask(page, 60_000);
  await expect(
    page.getByRole("heading", {
      name: "需要补充信息，需求尚未完成",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "补充澄清", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(shots, "intake-clarification-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
