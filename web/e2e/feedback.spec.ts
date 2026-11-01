import { expect, test } from "@playwright/test";
import path from "node:path";

const shots = path.resolve("test-results/screenshots");

test("actual reports distinguish known defect, infrastructure error and invalid plan", async ({
  page,
  request,
}) => {
  const fixtures = await (await request.get("/api/acceptance-fixtures")).json();
  for (const [key, outcome, message] of [
    [
      "infra",
      "TEST_INFRA_ERROR",
      "环境或传输异常，不能据此认定接口有缺陷。请检查环境与身份配置。",
    ],
    ["tool", "TOOL_ERROR", "计划或工具执行异常。请查看详细错误和未执行步骤。"],
  ]) {
    const report = await (
      await request.get(`/api/runs/${fixtures[key]}`)
    ).json();
    expect(report.outcome).toBe(outcome);
    await page.goto(`/runs/${fixtures[key]}`);
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({
      path: path.join(shots, `intake-${key}-mobile.png`),
      fullPage: true,
    });
  }
  const catalog = await (
    await request.get("/api/runs?outcome=ASSERTION_FAILED&limit=100")
  ).json();
  expect(catalog.items.length).toBeGreaterThan(0);
  await page.goto(`/runs/${catalog.items[0].run_id}`);
  await expect(
    page.getByRole("heading", { name: "问题依据", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/这是确定性检查结果/).first()).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.screenshot({
    path: path.join(shots, "intake-known-fault-desktop.png"),
    fullPage: true,
  });
});

test("review policy, duplicate admission, cancellation and changed project all give concrete feedback", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto("/tasks");
  await page
    .getByLabel("选择项目", { exact: true })
    .selectOption("review_tickets");
  await page.getByLabel("测试需求").fill("创建工单");
  const admission = page.waitForResponse(
    (r) => r.url().endsWith("/api/tasks") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  const response = await admission;
  const initial = await response.json();
  expect(initial.status).toBe("WAITING_REVIEW");
  const duplicate = await page.request.post("/api/tasks", {
    data: response.request().postDataJSON(),
    headers: {
      "x-agenttest-csrf": response.request().headers()["x-agenttest-csrf"],
    },
  });
  expect(duplicate.status()).toBe(202);
  expect((await duplicate.json()).id).toBe(initial.id);
  await expect(
    page.getByRole("button", { name: "确认此计划并执行", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("openTicket", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消后续动作", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已取消后续动作", exact: true }),
  ).toBeVisible();
  const timeline = await (
    await page.request.get(`/api/tasks/${initial.id}/timeline`)
  ).json();
  expect(
    timeline.events.every(
      (event: { requests: number }) => event.requests === 0,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(shots, "intake-cancel-desktop.png"),
    fullPage: true,
  });

  await page.goto("/");
  await page.getByLabel("项目名称", { exact: true }).fill("审阅版本核对");
  await page
    .getByLabel("接口文档（OpenAPI 3.0 / 3.1，JSON 或 YAML）", { exact: true })
    .setInputFiles(
      path.resolve("../examples/trusted-testing/projects/tickets/openapi.json"),
    );
  await page
    .getByLabel("服务端授权配置", { exact: true })
    .selectOption("review_tickets");
  await page.getByLabel("我已核对以上行为和预期，确认适用于此项目").check();
  await page
    .getByRole("button", { name: "保存并检查接入", exact: true })
    .click();
  await page.getByRole("link", { name: "开始一轮测试 →", exact: true }).click();
  await page.getByLabel("测试需求").fill("创建工单");
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认此计划并执行", exact: true }),
  ).toBeVisible();
  const taskUrl = page.url();
  await page.goto("/");
  await page
    .locator("section.panel")
    .filter({
      has: page.getByRole("heading", { name: "审阅版本核对", exact: true }),
    })
    .getByRole("button", { name: "补充信息", exact: true })
    .click();
  await page.getByLabel("项目名称", { exact: true }).fill("审阅版本核对已更新");
  await page.getByLabel("我已核对以上行为和预期，确认适用于此项目").check();
  const updatedResponse = page.waitForResponse(
    (r) =>
      r.url().includes("/api/project-intake/web_") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "保存并检查接入", exact: true })
    .click();
  expect((await updatedResponse).status()).toBe(200);
  await page.goto(taskUrl);
  await page
    .getByRole("button", { name: "确认此计划并执行", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("项目版本");
  const taskId = new URL(taskUrl).searchParams.get("task");
  const changed = await (
    await page.request.get(`/api/tasks/${taskId}/timeline`)
  ).json();
  expect(
    changed.events.every((event: { requests: number }) => event.requests === 0),
  ).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(shots, "intake-config-changed-mobile.png"),
    fullPage: true,
  });
});

test("unavailable service explains how to start and never offers a fake success", async ({
  page,
}) => {
  // Display-only fixture. Python API tests independently verify real disabled-service 503s.
  await page.route("**/api/task-session", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Task execution is not configured" },
    }),
  );
  await page.goto("/tasks");
  await expect(page.getByRole("alert")).toContainText("scripts/start_web.ps1");
  await expect(
    page.getByRole("button", { name: "开始测试", exact: true }),
  ).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(shots, "intake-service-unavailable-mobile.png"),
    fullPage: true,
  });
});
