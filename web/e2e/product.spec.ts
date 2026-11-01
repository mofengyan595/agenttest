import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

test("product intake uses a new contract, real configured model and actual loopback HTTP", async ({
  page,
}) => {
  test.skip(
    process.env.AGENTTEST_LIVE_ACCEPTANCE !== "1" &&
      process.env.AGENTTEST_PRODUCT_PREFLIGHT !== "1",
    "Explicit product acceptance required",
  );
  test.setTimeout(180_000);
  page.setDefaultTimeout(15000);
  const repository = path.resolve("..");
  const namespace = path.join(
    repository,
    "runs/product-next/live-" + randomUUID().replaceAll("-", ""),
  );
  await mkdir(namespace, { recursive: true });
  let requests = 0;
  const target = createServer((req, res) => {
    requests++;
    res.setHeader("content-type", "application/json");
    if (req.method !== "GET") {
      res.writeHead(405);
      res.end("{}");
      return;
    }
    if (req.url === "/depots")
      res.end(JSON.stringify({ depot_id: "depot-live-7", status: "open" }));
    else if (req.url === "/depots/depot-live-7")
      res.end(JSON.stringify({ depot_id: "depot-live-7", status: "open" }));
    else {
      res.writeHead(404);
      res.end("{}");
    }
  });
  await new Promise<void>((resolve) => target.listen(0, "127.0.0.1", resolve));
  const targetAddress = target.address();
  if (!targetAddress || typeof targetAddress === "string")
    throw new Error("Target did not start");
  const targetOrigin = `http://127.0.0.1:${targetAddress.port}`;
  const python = path.join(
    repository,
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python",
  );
  let server: ChildProcess | null = null;
  const origin = "http://127.0.0.1:18042";
  try {
    server = spawn(
      python,
      [
        path.join(repository, "scripts/serve_web_workspace.py"),
        "--port",
        "18042",
        "--data",
        namespace,
      ],
      {
        cwd: repository,
        windowsHide: true,
        stdio: ["ignore", "ignore", "ignore"],
        env: {
          ...process.env,
          PYTHONUTF8: "1",
          AGENTTEST_PROJECTS_FILE: "",
          AGENTTEST_SYNTHETIC_TARGETS: "0",
        },
      },
    );
    await expect
      .poll(
        async () => {
          try {
            return (
              await page.request.get(origin + "/health", { timeout: 1000 })
            ).ok();
          } catch {
            return false;
          }
        },
        { timeout: 30000 },
      )
      .toBe(true);
    await page.goto(origin);
    await expect(page.getByText(/已读取真实模型配置/)).toBeVisible();
    await page.getByLabel("项目名称", { exact: true }).fill("独立仓库接口实测");
    const document = {
      openapi: "3.0.3",
      info: { title: "Independent depot API", version: "1" },
      paths: {
        "/depots": {
          get: {
            operationId: "obtainDepotKey",
            responses: { "200": { description: "depot key" } },
          },
        },
        "/depots/{depotId}": {
          get: {
            operationId: "inspectDepot",
            parameters: [
              {
                in: "path",
                name: "depotId",
                required: true,
                schema: { type: "string" },
              },
            ],
            responses: { "200": { description: "depot details" } },
          },
        },
      },
    };
    await page
      .getByLabel("接口文档（OpenAPI 3.0 / 3.1，JSON 或 YAML）", {
        exact: true,
      })
      .setInputFiles({
        name: "independent-depots.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(document)),
      });
    await page
      .getByRole("button", { name: "读取接口并配置", exact: true })
      .click();
    await page
      .getByLabel("隔离测试环境地址", { exact: true })
      .fill(targetOrigin);
    await page
      .getByLabel("我确认这是已授权的隔离测试环境", { exact: true })
      .check();
    for (const [route, alias] of [
      ["GET /depots", "获取仓库编号"],
      ["GET /depots/{depotId}", "读取仓库详情"],
    ]) {
      const group = page.getByRole("group", { name: route, exact: true });
      await group.getByRole("checkbox").check();
      await group.getByLabel("这项操作的名称", { exact: true }).fill(alias);
      await group.getByRole("button", { name: "添加响应字段检查" }).click();
      await group.getByLabel("检查响应字段", { exact: true }).fill("$.status");
      await group
        .getByLabel("检查方式", { exact: true })
        .selectOption("equals");
      await group.getByLabel("预期值", { exact: true }).fill("open");
    }
    const second = page.getByRole("group", {
      name: "GET /depots/{depotId}",
      exact: true,
    });
    await second
      .getByLabel("输入来源", { exact: true })
      .selectOption("obtainDepotKey");
    await second
      .getByLabel("响应中的取值位置，例如 $.id", { exact: true })
      .fill("$.depot_id");
    await page
      .getByLabel("我已核对所选接口、输入和检查规则，确认用于此隔离测试环境", {
        exact: true,
      })
      .check();
    const savedResponse = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/project-intake") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "保存并检查接入", exact: true })
      .click();
    const saved = await (await savedResponse).json();
    expect(saved.ready).toBe(true);
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "独立仓库接口实测", exact: true }),
    ).toBeVisible();
    if (process.env.AGENTTEST_LIVE_ACCEPTANCE !== "1") {
      expect(requests).toBe(0);
      await writeFile(
        path.join(namespace, "preflight-receipt.json"),
        JSON.stringify(
          {
            project_id: saved.id,
            ready: saved.ready,
            source: "PRODUCT_BROWSER_PREFLIGHT",
            real_model_calls: 0,
            business_http_requests: requests,
          },
          null,
          2,
        ),
      );
      await page.screenshot({
        path: path.join(namespace, "product-intake-desktop.png"),
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(391);
      await page.screenshot({
        path: path.join(namespace, "product-intake-mobile.png"),
        fullPage: true,
      });
      return;
    }
    await page
      .getByRole("link", { name: "开始测试", exact: true })
      .first()
      .click();
    await page
      .getByLabel("测试需求", { exact: true })
      .fill("获取仓库编号，然后读取仓库详情");
    const submitted = page.waitForResponse(
      (r) => r.url().endsWith("/api/tasks") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "开始测试", exact: true }).click();
    const task = await (await submitted).json();
    let done = task;
    await expect
      .poll(
        async () => {
          done = await (
            await page.request.get(`${origin}/api/tasks/${task.id}`)
          ).json();
          return [
            "FINISHED",
            "NEEDS_REVIEW",
            "CANCELLED",
            "INTERRUPTED",
          ].includes(done.status);
        },
        { timeout: 110000, intervals: [1000] },
      )
      .toBe(true);
    const report = done.summary.run_id
      ? await (
          await page.request.get(`${origin}/api/runs/${done.summary.run_id}`)
        ).json()
      : null;
    await writeFile(
      path.join(namespace, "live-receipt.json"),
      JSON.stringify(
        {
          evidence: "SELF_AUTHORED_LOOPBACK_TARGET_AND_REAL_MODEL",
          independent_human_reviewed: false,
          task_id: task.id,
          project_id: saved.id,
          status: done.status,
          outcome: done.summary.outcome,
          task_satisfied: done.summary.task_satisfied,
          model_source: done.summary.model_source,
          usage: done.summary.context_usage,
          model: report?.model_call,
          business_http_requests: requests,
          steps: report?.steps?.map(
            (s: {
              operation_id: string;
              outcome: string;
              checks: unknown;
            }) => ({
              operation_id: s.operation_id,
              outcome: s.outcome,
              checks: s.checks,
            }),
          ),
        },
        null,
        2,
      ),
    );
    expect(done.summary.task_satisfied).toBe(true);
    expect(done.summary.model_source).toBe("CONFIGURED_MODEL");
    expect(done.summary.context_usage.model_calls).toBeGreaterThan(0);
    expect(done.summary.context_usage.usage_known).toBe(true);
    expect(requests).toBe(2);
    await expect(
      page.getByText("本次需求已完成", { exact: true }),
    ).toBeVisible();
    const downloaded = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出 pytest 回归包", exact: true })
      .click();
    await (await downloaded).saveAs(path.join(namespace, "regression.zip"));
    const fixed = await page.request.post(origin + "/api/saved-tests/run", {
      headers: {
        "x-agenttest-csrf": (
          await (await page.request.get(origin + "/api/task-session")).json()
        ).csrf_token,
      },
      data: {
        task_id: task.id,
        run_id: done.summary.run_id,
        submission_id: randomUUID().replaceAll("-", ""),
      },
    });
    expect(fixed.status()).toBe(202);
    const fixedTask = await fixed.json();
    let repeated = fixedTask;
    await expect
      .poll(
        async () => {
          repeated = await (
            await page.request.get(`${origin}/api/tasks/${fixedTask.id}`)
          ).json();
          return ["FINISHED", "INTERRUPTED"].includes(repeated.status);
        },
        { timeout: 20000 },
      )
      .toBe(true);
    await writeFile(
      path.join(namespace, "fixed-receipt.json"),
      JSON.stringify(
        {
          summary: repeated.summary,
          business_requests_total: requests,
        },
        null,
        2,
      ),
    );
    expect(repeated.summary.fixed_regression_passed).toBe(true);
    expect(repeated.summary.context_usage.model_calls).toBe(0);
    expect(requests).toBe(4);
    await page.screenshot({
      path: path.join(namespace, "product-result-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(391);
    await page.screenshot({
      path: path.join(namespace, "product-result-mobile.png"),
      fullPage: true,
    });
  } finally {
    target.close();
    if (server && server.exitCode === null) {
      const stopped = new Promise<void>((resolve) =>
        server!.once("exit", () => resolve()),
      );
      server.kill();
      await stopped;
    }
  }
});
