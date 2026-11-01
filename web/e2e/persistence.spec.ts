import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

test("real browser survives server restart with a newly registered project and saved fixed test", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const repository = path.resolve("..");
  const namespace = `runs/web-intake-restart-${randomUUID().replaceAll("-", "")}`;
  const port = 18032;
  const origin = `http://127.0.0.1:${port}`;
  const python =
    process.platform === "win32"
      ? path.join(repository, ".venv/Scripts/python.exe")
      : path.join(repository, ".venv/bin/python");
  let server: ChildProcess | null = null;

  async function start() {
    server = spawn(
      python,
      [
        path.join(repository, "scripts/serve_web_workspace.py"),
        "--demo",
        "--port",
        String(port),
        "--data",
        namespace,
      ],
      {
        cwd: repository,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PYTHONUTF8: "1", AGENTTEST_PROJECTS_FILE: "" },
      },
    );
    server.stdout?.on("data", () => {});
    server.stderr?.on("data", () => {});
    await expect
      .poll(
        async () => {
          if (server?.exitCode !== null) return false;
          try {
            return (
              await request.get(`${origin}/health`, { timeout: 1500 })
            ).ok();
          } catch {
            return false;
          }
        },
        { timeout: 30_000 },
      )
      .toBe(true);
  }
  async function stop() {
    const child = server;
    if (!child || child.exitCode !== null) return;
    const stopped = new Promise<void>((resolve) =>
      child.once("exit", () => resolve()),
    );
    child.kill();
    await stopped;
    server = null;
  }

  try {
    await start();
    await page.goto(origin);
    await expect(page.getByText(/还没有接入项目/)).toBeVisible();
    await page.getByLabel("项目名称", { exact: true }).fill("重启验收新项目");
    await page
      .getByLabel("接口文档（OpenAPI 3.0 / 3.1，JSON 或 YAML）", {
        exact: true,
      })
      .setInputFiles(
        path.join(
          repository,
          "examples/trusted-testing/projects/tickets/openapi.json",
        ),
      );
    await page
      .getByLabel("服务端授权配置", { exact: true })
      .selectOption("tickets");
    await page.getByLabel("我已核对以上行为和预期，确认适用于此项目").check();
    const savedResponse = page.waitForResponse(
      (r) =>
        r.url() === `${origin}/api/project-intake` &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "保存并检查接入", exact: true })
      .click();
    const response = await savedResponse;
    expect(response.status()).toBe(201);
    const created = await response.json();
    expect(created.id).toMatch(/^web_/);
    expect(created.id).not.toBe("tickets");
    expect(created.ready).toBe(true);
    await page
      .getByRole("link", { name: "开始一轮测试 →", exact: true })
      .click();
    await expect(page.getByLabel("选择项目", { exact: true })).toHaveValue(
      created.id,
    );
    await page
      .getByLabel("测试需求")
      .fill("创建工单，关闭工单，查询工单，其他用户不能读取工单");
    await page.getByRole("button", { name: "开始测试", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "本次需求已完成", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    const sourceId = new URL(page.url()).searchParams.get("task");
    const source = await (
      await page.request.get(`${origin}/api/tasks/${sourceId}`)
    ).json();
    expect(source.summary.task_satisfied).toBe(true);
    const archive = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出 pytest 回归包", exact: true })
      .click();
    await (
      await archive
    ).saveAs(path.resolve("test-results/restart-export.zip"));
    await page.goto(`${origin}/saved`);
    await expect(
      page.getByRole("button", { name: "再次运行固定测试", exact: true }),
    ).toBeVisible();
    await stop();
    await start();
    await page.reload();
    // The same browser keeps its valid HttpOnly session; no credentials are serialized to disk.
    await expect(
      page.getByRole("button", { name: "再次运行固定测试", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "再次运行固定测试", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "固定回归检查通过", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    const fixedId = new URL(page.url()).searchParams.get("task");
    const fixed = await (
      await page.request.get(`${origin}/api/tasks/${fixedId}`)
    ).json();
    expect(fixedId).not.toBe(sourceId);
    expect(fixed.summary.context_usage.model_calls).toBe(0);
    expect(fixed.summary.task_satisfied).toBeNull();
    expect(fixed.summary.fixed_regression_passed).toBe(true);
    await page.screenshot({
      path: path.resolve(
        "test-results/screenshots/intake-restart-records-desktop.png",
      ),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.resolve(
        "test-results/screenshots/intake-restart-records-mobile.png",
      ),
      fullPage: true,
    });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.goto(origin);
    await expect(
      page.getByRole("heading", { name: "重启验收新项目", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: path.resolve(
        "test-results/screenshots/intake-restart-project-mobile.png",
      ),
      fullPage: true,
    });
    await mkdir(path.resolve("test-results/evidence"), { recursive: true });
    // Allowlisted receipt only: no cookies, CSRF values, headers, credentials or uploaded bodies.
    await writeFile(
      path.resolve("test-results/evidence/persistence.json"),
      JSON.stringify(
        {
          schema_version: "agenttest.web-intake.browser.v1",
          evidence_class: "BROWSER_SELF_CHECK",
          data_directory: namespace,
          created_via: "POST /api/project-intake",
          create_status: response.status(),
          new_project: {
            id: created.id,
            name: created.name,
            version: created.version,
            ready: created.ready,
            confirmation_source: created.confirmation.source,
          },
          source: {
            task_id: sourceId,
            run_id: source.summary.run_id,
            project_id: source.project_id,
            outcome: source.summary.outcome,
            task_satisfied: source.summary.task_satisfied,
            model_source: source.summary.model_source,
            execution_target: source.summary.execution_target,
            requests: source.summary.context_usage.requests,
          },
          service_restarted: true,
          same_browser_valid_session: true,
          project_read_after_restart: true,
          fixed: {
            task_id: fixedId,
            run_id: fixed.summary.run_id,
            outcome: fixed.summary.outcome,
            model_calls: fixed.summary.context_usage.model_calls,
            task_satisfied: fixed.summary.task_satisfied,
          },
          real_model_quality_verified: false,
          independent_user_reviewed: false,
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await stop();
  }
});
