import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

test("fresh lab has usable projects on desktop and mobile without starting model tasks", async ({page}) => {
  const origin = process.env.AGENTTEST_RELIABLE_SMOKE_URL;
  test.skip(!origin, "Explicit local lab startup and layout check");
  const evidence = process.env.AGENTTEST_RELIABLE_SMOKE_EVIDENCE;
  if (!evidence) throw new Error("Explicit evidence directory required");
  await mkdir(evidence, {recursive: true});
  await page.goto(origin!);
  await expect(page.locator(".project-cards > section")).toHaveCount(3);
  for (const name of ["包裹隔离靶场", "预约隔离靶场", "目录隔离靶场"])
    await expect(page.locator(".project-cards > section").filter({hasText: name})).toBeVisible();
  expect((await (await page.request.get(origin + "/api/tasks")).json()).tasks).toHaveLength(0);
  await page.screenshot({path: path.join(evidence, "desktop.png"), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(evidence, "mobile.png"), fullPage: true});
  await page.screenshot({path: path.join(evidence, "mobile-viewport.png")});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("link", {name: "开始一轮测试"}).first().click();
  await expect(page.getByLabel("测试重点（可选）", {exact: true})).toBeVisible();
  expect((await (await page.request.get(origin + "/api/tasks")).json()).tasks).toHaveLength(0);
});
