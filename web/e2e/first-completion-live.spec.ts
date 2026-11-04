import {expect, test} from "@playwright/test";
import {readFile, writeFile} from "node:fs/promises";
import path from "node:path";

test("development only: normal web submission to configured model and original CourseInsight", async ({page}) => {
  test.skip(process.env.AGENTTEST_FIRST_COMPLETION_DEV !== "1", "Explicit real development call required");
  test.setTimeout(720_000);
  const origin = "http://127.0.0.1:18530";
  const root = path.resolve("../runs/first-completion-20261104");
  const project = JSON.parse(await readFile(path.join(root, "development-project.json"), "utf8"));
  expect(project.ready).toBe(true);
  expect(project.enabled_operations.sort()).toEqual(["getHealth", "listCourses", "loginUser"].sort());
  const request = "检查健康状态，并使用已就绪身份读取课程列表，分页大小size=3。";
  await page.goto(`${origin}/tasks?project=${project.id}`);
  await expect(page.getByLabel("测试重点（可选）", {exact: true})).toBeVisible();
  await page.getByLabel("测试重点（可选）", {exact: true}).fill(request);
  await writeFile(path.join(root, "development-readonly-v1-claim.json"), JSON.stringify({
    classification: "DEVELOPMENT_ONLY_NOT_FINAL_ACCEPTANCE", request,
    project_id: project.id, created_at: new Date().toISOString(),
    method: "NORMAL_WEB_UI_REAL_MODEL_REAL_COURSEINSIGHT",
  }, null, 2), {flag: "wx"});
  const waiting = page.waitForResponse((r) => r.url() === `${origin}/api/tasks` &&
    r.request().method() === "POST");
  await page.getByRole("button", {name: "开始一轮测试", exact: true}).click();
  const submittedResponse = await waiting;
  const submitted = await submittedResponse.json();
  await writeFile(path.join(root, "development-readonly-v1-submitted.json"),
    JSON.stringify(submitted, null, 2), {flag: "wx"});
  expect(submittedResponse.status()).toBe(202);
  let terminal: Record<string, unknown> | undefined;
  await expect.poll(async () => {
    const current = await (await page.request.get(`${origin}/api/tasks/${submitted.id}`)).json();
    if (["FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "FAILED", "CANCELLED"].includes(current.status)) {
      terminal = current;
      return true;
    }
    return false;
  }, {timeout: 650_000, intervals: [1000]}).toBe(true);
  await writeFile(path.join(root, "development-readonly-v1-terminal.json"),
    JSON.stringify(terminal, null, 2), {flag: "wx"});
  await expect(page.getByRole("region", {name: "本轮测试结果"})).toBeVisible();
  await page.screenshot({path: path.join(root, "development-readonly-v1-desktop.png"), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(root, "development-readonly-v1-mobile.png"), fullPage: true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  // Evidence is retained even when the model rejects or asks a wrong question; never retry here.
});

test("development only: new empty CourseInsight course through ordinary web flow", async ({page}) => {
  test.skip(process.env.AGENTTEST_FIRST_COMPLETION_WRITE_DEV !== "1", "Explicit fresh-resource development required");
  test.setTimeout(720_000);
  const origin = "http://127.0.0.1:18530";
  const root = path.resolve("../runs/first-completion-20261104");
  const input = JSON.parse(await readFile(path.join(root, "development-empty-course-input.json"), "utf8"));
  const attempt = process.env.AGENTTEST_FIRST_COMPLETION_WRITE_ATTEMPT || "v1";
  if (!["v1", "v2"].includes(attempt)) throw new Error("Unknown development attempt");
  await page.goto(`${origin}/projects`);
  let project;
  if (attempt === "v1") {
    const session = await (await page.request.get(`${origin}/api/task-session`)).json();
    const saved = await page.request.post(`${origin}/api/project-intake`, {
      headers: {Origin: origin, "x-agenttest-csrf": session.csrf_token}, data: input.payload,
    });
    project = await saved.json();
    await writeFile(path.join(root, "development-empty-course-project.json"),
      JSON.stringify(project, null, 2), {flag: "wx"});
    expect(saved.status()).toBe(201);
  } else {
    const first = JSON.parse(await readFile(path.join(root,
      "development-empty-course-v1-terminal.json"), "utf8"));
    expect(first.summary.context_usage.requests).toBe(0);
    expect(first.summary.context_usage.model_calls).toBe(0);
    project = JSON.parse(await readFile(path.join(root, "development-empty-course-project.json"), "utf8"));
  }
  expect(project.ready).toBe(true);
  await page.goto(`${origin}/tasks?project=${project.id}`);
  await page.getByLabel("测试重点（可选）", {exact: true}).fill(input.request);
  await writeFile(path.join(root, `development-empty-course-${attempt}-claim.json`), JSON.stringify({
    classification: "DEVELOPMENT_ONLY_NOT_FINAL_ACCEPTANCE", request: input.request,
    code: input.code, project_id: project.id, created_at: new Date().toISOString(),
    method: "NORMAL_WEB_UI_REAL_MODEL_REAL_COURSEINSIGHT_FRESH_RESOURCE",
  }, null, 2), {flag: "wx"});
  const waiting = page.waitForResponse((r) => r.url() === `${origin}/api/tasks` &&
    r.request().method() === "POST");
  await page.getByRole("button", {name: "开始一轮测试", exact: true}).click();
  const submittedResponse = await waiting;
  const submitted = await submittedResponse.json();
  await writeFile(path.join(root, `development-empty-course-${attempt}-submitted.json`),
    JSON.stringify(submitted, null, 2), {flag: "wx"});
  expect(submittedResponse.status()).toBe(202);
  let terminal: Record<string, unknown> | undefined;
  await expect.poll(async () => {
    const current = await (await page.request.get(`${origin}/api/tasks/${submitted.id}`)).json();
    if (["FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "FAILED", "CANCELLED"].includes(current.status)) {
      terminal = current;
      return true;
    }
    return false;
  }, {timeout: 650_000, intervals: [1000]}).toBe(true);
  await writeFile(path.join(root, `development-empty-course-${attempt}-terminal.json`),
    JSON.stringify(terminal, null, 2), {flag: "wx"});
  await expect(page.getByRole("region", {name: "本轮测试结果"})).toBeVisible();
  await page.screenshot({path: path.join(root, `development-empty-course-${attempt}-desktop.png`), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(root, `development-empty-course-${attempt}-mobile.png`), fullPage: true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
