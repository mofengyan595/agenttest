import { expect, test } from "@playwright/test";
import path from "node:path";

const shots = path.resolve("test-results/screenshots");
test("real API: draft, import, history, evidence and responsive layouts", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await request.get("/api/runs?limit=100");
  expect(response.ok()).toBeTruthy();
  const catalog = await response.json();
  expect(catalog.total).toBeGreaterThan(0);
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
  await expect(page.locator("tbody tr")).toHaveCount(catalog.total);
  await page.keyboard.press("Control+Home");
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.screenshot({
    path: path.join(shots, "history-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".run-cards")).toBeVisible();
  await expect(page.locator(".run-table")).toBeHidden();
  await expect(page.locator(".run-card")).toHaveCount(catalog.total);
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
  await page
    .getByLabel("结果", { exact: true })
    .selectOption("ASSERTION_FAILED");
  await expect(page.locator("tbody tr").first()).toContainText(
    "ASSERTION_FAILED",
  );
  let investigationId: string | undefined;
  for (const row of catalog.items) {
    const report = await (await request.get(`/api/runs/${row.run_id}`)).json();
    if (report.investigations?.length) investigationId = row.run_id;
  }
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
  await page.goto(`/runs/${"0".repeat(32)}`);
  await expect(page.getByRole("alert")).toContainText("未找到报告");
});
