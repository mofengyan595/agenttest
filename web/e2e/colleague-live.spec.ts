import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { createServer } from "node:net";
import path from "node:path";
import type { GoalTask } from "../src/api/tasks";
import { checkColleagueAcceptance } from "./colleague-oracle";
import { checkColleagueV2Acceptance } from "./colleague-v2-oracle";
import { checkColleagueV3Acceptance } from "./colleague-v3-oracle";
import { checkColleagueV4Acceptance } from "./colleague-v4-oracle";
import { checkColleagueV5Acceptance } from "./colleague-v5-oracle";
import { checkColleagueV6Acceptance } from "./colleague-v6-oracle";
import { checkColleagueV7Acceptance } from "./colleague-v7-oracle";
import { checkColleagueV8Acceptance } from "./colleague-v8-oracle";
import { checkColleagueV9Acceptance } from "./colleague-v9-oracle";
import { checkColleagueV10Acceptance } from "./colleague-v10-oracle";
import { checkColleagueV11Acceptance } from "./colleague-v11-oracle";
import { checkColleagueV12Acceptance } from "./colleague-v12-oracle";
import { checkColleagueV13Acceptance } from "./colleague-v13-oracle";
import { checkColleagueV14Acceptance } from "./colleague-v14-oracle";
import { checkColleagueV15Acceptance } from "./colleague-v15-oracle";
import { checkColleagueV16Acceptance } from "./colleague-v16-oracle";

test("development: ordinary web intake, configured model design and disposable HTTP CRUD", async ({
  page,
}) => {
  test.skip(
    process.env.AGENTTEST_COLLEAGUE_DEV !== "1",
    "Explicit new-product development acceptance",
  );
  test.setTimeout(900_000);
  page.setDefaultTimeout(20_000);
  const repository = path.resolve("..");
  const kind = process.env.AGENTTEST_COLLEAGUE_PROJECT || "parcels";
  const acceptance = process.env.AGENTTEST_COLLEAGUE_ACCEPTANCE
    ? JSON.parse(await readFile(process.env.AGENTTEST_COLLEAGUE_MANIFEST ||
      path.join(repository, "docs/testing-colleague-acceptance-v1.json"), "utf8"))
    : null;
  const acceptanceTask = acceptance?.tasks.find((t: {project: string}) => t.project === kind);
  const cancelAfterWrite = process.env.AGENTTEST_COLLEAGUE_CANCEL === "1";
  if (cancelAfterWrite && kind !== "parcels") throw new Error("Cancellation requires parcel fixture");
  if (!["parcels", "reservations", "directory"].includes(kind))
    throw new Error("Unknown disposable project");
  const root = path.join(repository, "runs/testing-colleague");
  const workspace = path.resolve(process.env.AGENTTEST_COLLEAGUE_WORKSPACE ||
    path.join(root, "development-workspace"));
  const workspaceRelative = path.relative(root, workspace);
  if (workspaceRelative.startsWith("..") || path.isAbsolute(workspaceRelative))
    throw new Error("Disposable workspace must stay under the evidence root");
  const evidence = path.join(
    root,
    acceptance ? `${acceptance.evidence_folder || "acceptance-v1"}/round-${process.env.AGENTTEST_COLLEAGUE_ROUND}` : "development-browser",
    randomUUID().replaceAll("-", ""),
  );
  await mkdir(evidence, { recursive: true });
  await writeFile(path.join(evidence, "position.json"), JSON.stringify({
    project: kind, round: process.env.AGENTTEST_COLLEAGUE_ROUND || null,
    acceptance_version: acceptance?.version || null,
    ...(acceptance?.performance_replay_only ? {
      classification: acceptance.classification,
      schedule_label: acceptance.run_label,
      performance_replay_only: true,
    } : {}),
  }, null, 2));
  const python = path.join(
    repository,
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python",
  );
  const freePort = async () => {
    const socket = createServer();
    await new Promise<void>((resolve) =>
      socket.listen(0, "127.0.0.1", resolve),
    );
    const address = socket.address();
    if (!address || typeof address === "string")
      throw new Error("Local port unavailable");
    const selected = address.port;
    await new Promise<void>((resolve, reject) =>
      socket.close((error) => (error ? reject(error) : resolve())),
    );
    return String(selected);
  };
  const port = await freePort(),
    targetPort = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const instance = randomUUID();
  const children: ChildProcess[] = [];
  const startupTimeout = 90_000;
  let workspaceChild: ChildProcess | null = null;
  let task: GoalTask | null = null;
  const start = (args: string[]) => {
    const child = spawn(python, ["-X", "utf8", ...args], {
      cwd: repository,
      windowsHide: true,
      stdio: ["ignore", "ignore", "ignore"],
      env: {
        ...process.env,
        AGENTTEST_PROJECTS_FILE: "",
        AGENTTEST_SYNTHETIC_TARGETS: "0",
        PYTHONUTF8: "1",
        AGENTTEST_LAB_INSTANCE_ID: instance,
        AGENTTEST_TARGET_LAB_OWNER_USERNAME: "owner_" + instance,
        AGENTTEST_TARGET_LAB_OWNER_PASSWORD: "owner_secret_" + instance,
        AGENTTEST_TARGET_LAB_OWNER_BEARER: "owner_token_" + instance,
        AGENTTEST_TARGET_LAB_READER_USERNAME: "reader_" + instance,
        AGENTTEST_TARGET_LAB_READER_PASSWORD: "reader_secret_" + instance,
        AGENTTEST_TARGET_LAB_READER_BEARER: "reader_token_" + instance,
      },
    });
    children.push(child);
    return child;
  };
  try {
    start([
      "scripts/serve_colleague_targets.py",
      "--project",
      kind,
      "--port",
      targetPort,
      "--events",
      path.join(evidence, "target-events.jsonl"),
      "--fault-http-status",
      process.env.AGENTTEST_COLLEAGUE_FAULT_STATUS || "503",
      "--read-delay",
      cancelAfterWrite ? "4" : "0",
    ]);
    workspaceChild = start([
      "scripts/serve_web_workspace.py",
      "--port",
      port,
      "--data",
      workspace,
    ]);
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
        { timeout: startupTimeout },
      )
      .toBe(true);
    await expect
      .poll(
        async () => {
          try {
            return (
              (
                await (
                  await page.request.get(
                    `http://127.0.0.1:${targetPort}/health`,
                    { timeout: 1000 },
                  )
                ).json()
              ).instance_id === instance
            );
          } catch {
            return false;
          }
        },
        { timeout: startupTimeout },
      )
      .toBe(true);
    await page.goto(origin);
    await expect(page.getByText(/已读取真实模型配置/)).toBeVisible({timeout: 65000});
    await page
      .getByLabel("项目名称", { exact: true })
      .fill((acceptance ? "验收项目 " : "开发测试项目 ") + kind);
    await page
      .getByLabel("接口文档（OpenAPI 3.0 / 3.1，JSON 或 YAML）", {
        exact: true,
      })
      .setInputFiles(
        path.join(root, `development-documents/${kind}.openapi.json`),
      );
    await page
      .getByRole("button", { name: "读取接口并配置", exact: true })
      .click();
    await page
      .getByLabel("隔离测试环境地址", { exact: true })
      .fill(`http://127.0.0.1:${targetPort}`);
    await page
      .getByLabel("我确认这是已授权的隔离测试环境", { exact: true })
      .check();
    if (kind === "directory")
      await page
        .getByRole("spinbutton", {
          name: "本次任务最多发送的请求数",
          exact: true,
        })
        .fill("64");
    await page
      .getByLabel("允许所选接口在此隔离环境中创建、修改或删除测试数据", {
        exact: true,
      })
      .check();
    await page
      .getByLabel("允许受控异常参数测试，按文档中的 400 / 422 检查拒绝结果", {
        exact: true,
      })
      .check();
    if (process.env.AGENTTEST_COLLEAGUE_FIXED_NEGATIVES_ONLY !== "1")
      await page.getByLabel("允许在同一字段、同类文档约束内变化异常值", {exact: true}).check();
    if (kind === "reservations") {
      await page
        .getByText("配置测试身份（需要登录或 Token 时填写）", { exact: true })
        .click();
      await page
        .getByRole("button", { name: "添加测试身份", exact: true })
        .click();
      await page
        .getByRole("group", { name: "测试身份 tester1", exact: true })
        .getByLabel("身份别名", { exact: true })
        .fill("owner");
      const owner = page.getByRole("group", {
        name: "测试身份 owner",
        exact: true,
      });
      await owner
        .getByRole("combobox", { name: "鉴权方式", exact: true })
        .selectOption("login");
      await owner
        .getByLabel("账号环境变量名", { exact: true })
        .fill("AGENTTEST_TARGET_LAB_OWNER_USERNAME");
      await owner
        .getByLabel("密码环境变量名", { exact: true })
        .fill("AGENTTEST_TARGET_LAB_OWNER_PASSWORD");
      await owner
        .getByRole("combobox", { name: "登录接口", exact: true })
        .selectOption("openSession");
      await page
        .getByRole("button", { name: "添加测试身份", exact: true })
        .click();
      await page
        .getByRole("group", { name: "测试身份 tester2", exact: true })
        .getByLabel("身份别名", { exact: true })
        .fill("reader");
      await page
        .getByRole("group", { name: "测试身份 reader", exact: true })
        .getByLabel("Token 环境变量名", { exact: true })
        .fill("AGENTTEST_TARGET_LAB_READER_BEARER");
      await page
        .getByRole("combobox", {
          name: "登录和受限接口默认使用的身份",
          exact: true,
        })
        .selectOption("owner");
    }
    await page
      .getByLabel("我已核对所选接口、输入和检查规则，确认用于此隔离测试环境", {
        exact: true,
      })
      .check();
    const saving = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/project-intake") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "保存并检查接入", exact: true })
      .click();
    const savedResponse = await saving;
    const project = await savedResponse.json();
    await writeFile(
      path.join(evidence, "intake.json"),
      JSON.stringify(project, null, 2),
    );
    expect(savedResponse.status()).toBe(201);
    expect(project.ready).toBe(true);
    expect(project.inventory.total).toBe(
      kind === "parcels" ? 6 : kind === "reservations" ? 5 : 48,
    );
    await page.reload();
    await page
      .locator(`a[href="/tasks?project=${project.id}"]`)
      .first()
      .click();
    const focus =
      acceptanceTask?.request ?? process.env.AGENTTEST_COLLEAGUE_FOCUS ??
      (kind === "parcels"
        ? "请创建一个带标签的包裹，读取后更新名称，再删除本轮新建的包裹；另外检查数量越界时是否按文档拒绝。"
        : kind === "reservations"
          ? "以 owner 登录，新建标题为开发预约的周二预约并读取，看看 reader 能不能读取 owner 的新预约；他人读取应该返回什么还没确认，请具体问我。最后清理本轮新预约。不要检查服务可用状态。"
          : "");
    await page.getByLabel("测试重点（可选）", { exact: true }).fill(focus);
    await writeFile(path.join(evidence, "development-request.txt"), focus);
    const submitting = page.waitForResponse(
      (r) => r.url().endsWith("/api/tasks") && r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "开始一轮测试", exact: true })
      .click({ clickCount: cancelAfterWrite ? 2 : 1 });
    const submittedResponse = await submitting;
    const submitted = await submittedResponse.json();
    await writeFile(
      path.join(evidence, "submitted.json"),
      JSON.stringify(submitted, null, 2),
    );
    expect(submittedResponse.status()).toBe(202);
    if (cancelAfterWrite) {
      const eventFile = path.join(evidence, "target-events.jsonl");
      await expect.poll(async () => {
        const events = (await readFile(eventFile, "utf8")).trim().split("\n")
          .map((line) => JSON.parse(line)) as {method: string; path: string}[];
        return events.some((e) => e.method === "GET" && e.path.startsWith("/parcels/"));
      }, {timeout: 240_000, intervals: [100]}).toBe(true);
      const stopping = page.waitForResponse((r) => r.url().endsWith(`/api/tasks/${submitted.id}/cancel`));
      await page.getByRole("button", {name: "取消后续动作", exact: true}).click();
      expect((await stopping).status()).toBe(200);
      task = await (await page.request.get(origin + "/api/tasks/" + submitted.id)).json();
      await writeFile(path.join(evidence, "cancelled.json"), JSON.stringify(task, null, 2));
      expect(task!.status).toBe("CANCELLED");
      const unsentCleanup = task!.summary.untested_operations?.find((o) => o.operation_id === "deleteParcel");
      expect(unsentCleanup?.reason).toMatch(/取消|停止/);
      expect(task!.summary.cases?.some((c) => c.evidence?.some((e) =>
        e.method === "POST" && e.checks.some((check) => check.actual === 201)))).toBe(true);
      const eventsBefore = await readFile(eventFile, "utf8");
      expect(eventsBefore.split("\n").filter((line) => line.includes('"method": "POST"'))).toHaveLength(1);
      expect(eventsBefore).not.toContain('"method": "DELETE"');
      const exited = new Promise<void>((resolve) => workspaceChild!.once("exit", () => resolve()));
      workspaceChild!.kill();
      await exited;
      workspaceChild = start(["scripts/serve_web_workspace.py", "--port", port,
        "--data", workspace]);
      await expect.poll(async () => {
        try { return (await page.request.get(origin + "/health", {timeout: 1000})).ok(); }
        catch { return false; }
      }, {timeout: startupTimeout}).toBe(true);
      await page.reload();
      await expect(page.getByRole("region", {name: "本轮测试结果"}))
        .toContainText("本轮停止原因：已停止后续动作");
      const fresh = await (await page.request.get(origin + "/api/task-session")).json();
      const duplicate = await page.request.post(origin + "/api/tasks", {
        headers: {"x-agenttest-csrf": fresh.csrf_token},
        data: submittedResponse.request().postDataJSON(),
      });
      expect(duplicate.status()).toBe(202);
      expect((await duplicate.json()).id).toBe(submitted.id);
      expect(await readFile(eventFile, "utf8")).toBe(eventsBefore);
      const health = await (await page.request.get(`http://127.0.0.1:${targetPort}/health`)).json();
      expect(health.record_count).toBe(1);
      await writeFile(path.join(evidence, "cancel-restart-duplicate.json"), JSON.stringify({
        classification: "DEVELOPMENT_ONLY", task_id: submitted.id,
        status: task!.status, context_usage: task!.summary.context_usage,
        duplicate_task_id: submitted.id, events_unchanged: true, remaining_records: 1,
        cleanup: "Cancelled task did not replay cleanup; disposable target is discarded at exit.",
      }, null, 2));
      return;
    }
    await expect
      .poll(
        async () => {
          task = (await (
            await page.request.get(origin + "/api/tasks/" + submitted.id)
          ).json()) as GoalTask;
          return [
            "FINISHED",
            "NEEDS_REVIEW",
            "INTERRUPTED",
            "CANCELLED",
          ].includes(task.status);
        },
        { timeout: 650_000, intervals: [1000] },
      )
      .toBe(true);
    await writeFile(
      path.join(evidence, "first-attempt.json"),
      JSON.stringify(task, null, 2),
    );
    let result = task as unknown as GoalTask;
    const inputChoice = acceptanceTask?.input_choice || process.env.AGENTTEST_COLLEAGUE_INPUT_CHOICE;
    if (inputChoice) {
      const inputQuestion = result.summary.questions?.find((q) => q.input_choices?.includes(inputChoice));
      expect(inputQuestion, JSON.stringify(result.summary)).toBeTruthy();
      expect(result.summary.cases?.flatMap((c) => c.evidence || [])
        .filter((e) => e.path === "/reservations" && e.method === "POST") || []).toHaveLength(0);
      await page.reload();
      await page.getByRole("button", {name: `选择“${inputChoice}”并补充`, exact: true}).click();
      await expect(page.getByLabel("测试重点（可选）", {exact: true})).toHaveValue(
        new RegExp(inputChoice));
      const supplement = page.waitForResponse((r) => r.url().endsWith("/api/tasks") &&
        r.request().method() === "POST");
      await page.getByRole("button", {name: "提交补充需求", exact: true}).click();
      const next = await (await supplement).json();
      await writeFile(path.join(evidence, "input-supplement-submitted.json"), JSON.stringify(next, null, 2));
      await expect.poll(async () => {
        result = await (await page.request.get(origin + "/api/tasks/" + next.id)).json();
        return ["FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "CANCELLED"].includes(result.status);
      }, {timeout: 650_000, intervals: [1000]}).toBe(true);
      await writeFile(path.join(evidence, "input-supplement-first-attempt.json"), JSON.stringify(result, null, 2));
      expect(result.summary.questions?.filter((q) => q.input_choices?.length) || []).toHaveLength(0);
      const created = result.summary.cases?.flatMap((c) => c.evidence || [])
        .find((e) => e.path === "/reservations" && e.method === "POST");
      expect(created?.request?.values?.body).toMatchObject({title: inputChoice});
      const expectedBody = acceptanceTask?.initial_body || JSON.parse(
        process.env.AGENTTEST_COLLEAGUE_EXPECT_INITIAL_BODY || "null");
      if (expectedBody) expect(created?.request?.values?.body).toMatchObject(expectedBody);
    }
    if (acceptance?.version === "testing-colleague-final-v16")
      checkColleagueV16Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v15")
      checkColleagueV15Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v14")
      checkColleagueV14Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v13")
      checkColleagueV13Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v12")
      checkColleagueV12Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v11")
      checkColleagueV11Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v10")
      checkColleagueV10Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v9")
      checkColleagueV9Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v8")
      checkColleagueV8Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v7")
      checkColleagueV7Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v6")
      checkColleagueV6Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v5")
      checkColleagueV5Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v4")
      checkColleagueV4Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v3")
      checkColleagueV3Acceptance(result, kind, "initial");
    else if (acceptance?.version === "testing-colleague-final-v2")
      checkColleagueV2Acceptance(result, kind, "initial");
    else if (acceptance) checkColleagueAcceptance(result, kind, "initial");
    expect(result.summary.mode).toBe("MODEL_DESIGNED");
    expect(result.summary.context_usage?.usage_known).toBe(true);
    expect(result.summary.context_usage?.model_calls).toBeGreaterThan(0);
    expect(result.summary.context_usage?.requests).toBeGreaterThan(0);
    if (process.env.AGENTTEST_COLLEAGUE_EXPECT_QUESTIONS)
      expect(result.summary.questions || []).toHaveLength(
        Number(process.env.AGENTTEST_COLLEAGUE_EXPECT_QUESTIONS));
    type LiteralCheck = {operation_id: string; slot: "query" | "body"; name: string; value: unknown};
    const literalChecks: LiteralCheck[] = acceptanceTask?.literal_checks || JSON.parse(
      process.env.AGENTTEST_COLLEAGUE_EXPECT_LITERAL_CHECKS || "[]");
    const matchesLiteral = (item: NonNullable<NonNullable<GoalTask["summary"]["cases"]>[number]["evidence"]>[number], check: LiteralCheck) => {
      const values = (item.request?.values?.[check.slot] || {}) as Record<string, unknown>;
      return item.operation_id === check.operation_id &&
        JSON.stringify(values[check.name]) === JSON.stringify(check.value) &&
        item.checks.some((c) => c.kind === "http_status" && c.passed && c.actual === 422);
    };
    const literalObservations = literalChecks.map((check) => ({...check,
      matched: result.summary.cases?.some((c) => c.evidence?.some((e) => matchesLiteral(e, check))) || false}));
    if (literalChecks.length) {
      await writeFile(path.join(evidence, "exact-literal-observations.json"),
        JSON.stringify(literalObservations, null, 2));
      expect(literalObservations.every((item) => item.matched)).toBe(true);
    }
    if (process.env.AGENTTEST_COLLEAGUE_EXPECT_HELD_VALUES) {
      const held = Number(process.env.AGENTTEST_COLLEAGUE_EXPECT_HELD_VALUES);
      expect(result.summary.pending_negative_values).toHaveLength(held);
      expect(result.summary.coverage?.requested_check_denominator).toBe(held);
      expect(result.summary.coverage?.executed_requested_checks).toBe(0);
      expect(result.summary.cases?.every((c) => !c.approved_negative_steps)).toBe(true);
    }
    if (process.env.AGENTTEST_COLLEAGUE_EXPECT_PARTIAL_CHECKS) {
      const counts = JSON.parse(process.env.AGENTTEST_COLLEAGUE_EXPECT_PARTIAL_CHECKS);
      expect(result.status).toBe("FINISHED");
      expect(result.summary.complete).toBe(false);
      expect(result.summary.pending_negative_values).toHaveLength(counts.pending);
      expect(result.summary.coverage?.requested_check_denominator).toBe(counts.requested);
      expect(result.summary.coverage?.executed_requested_checks).toBe(counts.executed);
      const heldValues = result.summary.pending_negative_values?.map((p) => p.requested_value) || [];
      const actual = result.summary.cases?.flatMap((c) => c.evidence || []) || [];
      expect(actual.some((e) => e.method === "GET" && e.checks.some(
        (c) => c.kind === "http_status" && c.passed && c.actual === 200))).toBe(true);
      expect(actual.every((e) => !Object.values(e.request?.values?.query || {}).some(
        (v) => heldValues.includes(v)))).toBe(true);
    }
    if (process.env.AGENTTEST_COLLEAGUE_EXPECT_SOURCE_CONFLICT) {
      const held = process.env.AGENTTEST_COLLEAGUE_EXPECT_SOURCE_CONFLICT;
      expect(result.summary.source_blocked_operations).toContain(held);
      const actual = result.summary.cases?.flatMap((item) => item.evidence || []) || [];
      expect(actual.some((item) => item.operation_id === held)).toBe(false);
      expect(actual.every((item) => ["GET", "HEAD", "OPTIONS"].includes(item.method))).toBe(true);
    }
    if (process.env.AGENTTEST_COLLEAGUE_EXPECT_CHECKS) {
      const count = Number(process.env.AGENTTEST_COLLEAGUE_EXPECT_CHECKS);
      expect(result.summary.coverage?.requested_check_denominator).toBe(count);
      expect(result.summary.coverage?.executed_requested_checks).toBe(count);
    }
    if (process.env.AGENTTEST_COLLEAGUE_EXPECT_READY_COVERAGE) {
      const expected = Number(process.env.AGENTTEST_COLLEAGUE_EXPECT_READY_COVERAGE);
      const normal = new Set(result.summary.cases?.flatMap((c) => c.evidence || [])
        .filter((e) => e.checks.some((k) => k.kind === "http_status" &&
          typeof k.actual === "number" && Array.isArray(k.expected) &&
          k.expected.some((status) => typeof status === "number" && status >= 200 && status < 300)))
        .map((e) => e.operation_id));
      expect(normal.size).toBe(expected);
      expect(result.summary.design_records?.every((r) => r.status === "PROPOSED")).toBe(true);
      expect(result.summary.cases?.filter((c) => c.kinds?.includes("negative"))
        .every((c) => (c.approved_negative_steps || 0) > 0)).toBe(true);
      await writeFile(path.join(evidence, "normal-ready-coverage.json"), JSON.stringify({
        classification: "DEVELOPMENT_REAL_MODEL_LOOPBACK_HTTP", expected_ready: expected,
        actual_normal_operations: [...normal].sort(),
        normal_means_requested_not_passed: true,
        usage: result.summary.context_usage, business_correctness_is_separate: true,
      }, null, 2));
    }
    if (kind === "reservations") {
      const question = result.summary.questions?.find(
        (q) => q.kind === "permission",
      );
      expect(question, JSON.stringify(result.summary)).toBeTruthy();
      expect(
        result.summary.cases?.some((c) =>
          c.evidence?.some((e) => e.method === "POST" && e.path === "/session"),
        ),
      ).toBe(true);
      await page.reload();
      await page
        .getByRole("button", { name: "确认应返回 403", exact: true })
        .click();
      await expect(page.getByText(/规则已保存/)).toBeVisible();
      await expect(page.getByText(/已保存预期：返回 403/)).toBeVisible();
      await page
        .getByLabel("测试重点（可选）", { exact: true })
        .fill(
          acceptanceTask?.followup ?? process.env.AGENTTEST_COLLEAGUE_FOLLOWUP ?? "按已确认的规则，以 owner 登录并新建周一预约，再由 reader 读取同一个新预约，核验拒绝；最后由 owner 删除本轮预约。不检查服务状态。",
        );
      const following = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/tasks") && r.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "提交补充需求", exact: true })
        .click();
      const next = await (await following).json();
      await writeFile(
        path.join(evidence, "followup-submitted.json"),
        JSON.stringify(next, null, 2),
      );
      await expect
        .poll(
          async () => {
            task = (await (
              await page.request.get(origin + "/api/tasks/" + next.id)
            ).json()) as GoalTask;
            return [
              "FINISHED",
              "NEEDS_REVIEW",
              "INTERRUPTED",
              "CANCELLED",
            ].includes(task.status);
          },
          { timeout: 300_000, intervals: [1000] },
        )
        .toBe(true);
      result = task as unknown as GoalTask;
      await writeFile(
        path.join(evidence, "followup-first-attempt.json"),
        JSON.stringify(result, null, 2),
      );
      expect(result.summary.context_usage?.usage_known).toBe(true);
      expect(result.summary.questions || []).toHaveLength(0);
      const access = result.summary.cases
        ?.flatMap((c) => c.evidence || [])
        .find((e) => e.method === "GET" && e.request?.role === "reader");
      expect(access, JSON.stringify(result.summary)).toBeTruthy();
      expect(access?.checks.some((c) => c.actual === 403 && c.passed)).toBe(
        true,
      );
      if (acceptance?.version === "testing-colleague-final-v16")
        checkColleagueV16Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v15")
        checkColleagueV15Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v14")
        checkColleagueV14Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v13")
        checkColleagueV13Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v12")
        checkColleagueV12Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v11")
        checkColleagueV11Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v10")
        checkColleagueV10Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v9")
        checkColleagueV9Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v8")
        checkColleagueV8Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v7")
        checkColleagueV7Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v6")
        checkColleagueV6Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v5")
        checkColleagueV5Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v4")
        checkColleagueV4Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v3")
        checkColleagueV3Acceptance(result, kind, "followup");
      else if (acceptance?.version === "testing-colleague-final-v2")
        checkColleagueV2Acceptance(result, kind, "followup");
      else if (acceptance) checkColleagueAcceptance(result, kind, "followup");
    }
    if (acceptance && kind !== "directory") {
      const health = await (await page.request.get(`http://127.0.0.1:${targetPort}/health`)).json();
      await writeFile(path.join(evidence, "disposable-final-state.json"), JSON.stringify({
        remaining_records: health.record_count, classification: "SELF_BUILT_TARGET_OBSERVATION",
      }, null, 2));
      expect(health.record_count).toBe(0);
    }
    expect(new URL(page.url()).searchParams.get("task")).toBe(result.id);
    await page.reload();
    await expect(
      page.getByRole("region", { name: "本轮测试结果" }),
    ).toBeVisible({timeout: 60000});
    await page.screenshot({
      path: path.join(evidence, "desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(evidence, "mobile.png"),
      fullPage: true,
    });
    await page.screenshot({ path: path.join(evidence, "mobile-viewport.png") });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    if (kind === "directory") {
      expect(project.inventory.counts.READY).toBe(45);
      expect(project.inventory.counts.NEEDS_INPUT).toBe(1);
      expect(project.inventory.counts.UNSUPPORTED).toBe(2);
      const groups = (
        result.summary as typeof result.summary & {
          groups: { primary: string[]; operations: string[] }[];
        }
      ).groups;
      expect(groups.flatMap((g) => g.primary)).toHaveLength(
        result.summary.focus?.mode === "selected"
          ? result.summary.focus.operation_ids.length
          : 45,
      );
      expect(groups.every((g) => g.operations.length <= 12)).toBe(true);
      expect(result.summary.context_usage?.model_calls).toBeGreaterThan(1);
      expect(
        result.summary.cases?.some((c) => c.outcome === "ASSERTION_FAILED"),
      ).toBe(true);
      const exportableCases = result.summary.cases?.filter(
        (c) => c.run_id && result.summary.exports?.[c.run_id]?.available) || [];
      const source = literalChecks.length ? exportableCases.find(
        (c) => c.evidence?.some((e) => matchesLiteral(e, literalChecks[0]))) : exportableCases[0];
      expect(source, JSON.stringify(result.summary)).toBeTruthy();
      await page.setViewportSize({ width: 1440, height: 1080 });
      const savingTest = page.waitForResponse(
        (r) =>
          r.url().includes(`/api/tasks/${result.id}/regression`) &&
          r.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "保存测试", exact: true })
        .nth(exportableCases.findIndex((c) => c.run_id === source?.run_id))
        .click();
      expect((await savingTest).status()).toBe(200);
      await writeFile(
        path.join(evidence, "saved-source.json"),
        JSON.stringify({ task_id: result.id, run_id: source?.run_id }, null, 2),
      );
      const exited = new Promise<void>((resolve) =>
        workspaceChild!.once("exit", () => resolve()),
      );
      workspaceChild!.kill();
      await exited;
      workspaceChild = start([
        "scripts/serve_web_workspace.py",
        "--port",
        port,
        "--data",
        workspace,
      ]);
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
          { timeout: startupTimeout },
        )
        .toBe(true);
      await page.goto(origin + "/saved");
      const asset = page
        .locator(".task-case")
        .filter({ hasText: project.name })
        .first();
      await expect(
        asset.getByRole("button", { name: "再次运行固定测试", exact: true }),
      ).toBeEnabled();
      const repeating = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/saved-tests/run") &&
          r.request().method() === "POST",
      );
      await asset
        .getByRole("button", { name: "再次运行固定测试", exact: true })
        .click();
      expect((await repeating).status()).toBe(202);
      // The UI consumes the POST response and navigates; CDP can discard that old body.
      // Observe the normal browser result instead of racing response.json() with navigation.
      await expect.poll(() => new URL(page.url()).searchParams.get("task"),
        {timeout: 30000}).toMatch(/^[a-f0-9]{32}$/);
      const rerunId = new URL(page.url()).searchParams.get("task");
      expect(rerunId).not.toBe(result.id);
      let repeated: GoalTask | null = null;
      await expect
        .poll(
          async () => {
            repeated = (await (
              await page.request.get(origin + "/api/tasks/" + rerunId)
            ).json()) as GoalTask;
            return [
              "FINISHED",
              "NEEDS_REVIEW",
              "INTERRUPTED",
              "CANCELLED",
            ].includes(repeated.status);
          },
          { timeout: 30000 },
        )
        .toBe(true);
      const fixed = repeated as unknown as GoalTask;
      await writeFile(
        path.join(evidence, "fixed-after-restart.json"),
        JSON.stringify(fixed, null, 2),
      );
      expect(fixed.summary.fixed_regression_passed).toBe(true);
      expect(fixed.summary.source_run_id).toBe(source?.run_id);
      expect(fixed.summary.context_usage?.model_calls).toBe(0);
      expect(fixed.summary.context_usage?.requests).toBeGreaterThan(0);
      if (literalChecks.length) {
        const events = (await readFile(path.join(evidence, "target-events.jsonl"), "utf8"))
          .split("\n").filter(Boolean).map((line) => JSON.parse(line));
        const last = events.filter((e) => e.path !== "/health").at(-1);
        expect(last?.query?.[literalChecks[0].name]).toBe(String(literalChecks[0].value));
        await writeFile(path.join(evidence, "exact-fixed-replay.json"), JSON.stringify({
          source_run_id: source?.run_id, model_calls: 0, last_actual_query: last.query,
          requested_value: literalChecks[0].value,
        }, null, 2));
      }
      await page.goto(origin);
      await page.locator(".project-cards > section").filter({hasText: project.id})
        .getByRole("button", {name: "补充信息", exact: true}).click();
      await page.getByLabel("业务说明（可选）", {exact: true}).fill("规则版本变更：本轮只核验文档中的状态。");
      await page.getByLabel("我已核对所选接口、输入和检查规则，确认用于此隔离测试环境", {exact: true}).check();
      const updating = page.waitForResponse((r) => r.url().endsWith(`/api/project-intake/${project.id}`)
        && r.request().method() === "POST");
      await page.getByRole("button", {name: "保存并检查接入", exact: true}).click();
      expect((await updating).status()).toBe(200);
      await page.goto(origin + "/saved");
      const stale = page.locator(".task-case").filter({hasText: project.name}).first();
      await expect(stale.getByRole("button", {name: "再次运行固定测试", exact: true})).toBeDisabled();
      await expect(stale).toContainText("项目配置或回归文件已变化");
      await writeFile(path.join(evidence, "configuration-change.json"), JSON.stringify({
        saved_run_id: source?.run_id, unavailable_after_change: true, model_calls: 0,
      }, null, 2));
    }
    const events = (
      await readFile(path.join(evidence, "target-events.jsonl"), "utf8")
    )
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line)) as { method: string; path: string }[];
    await writeFile(
      path.join(evidence, "development-observation.json"),
      JSON.stringify(
        {
          evidence,
          classification: acceptance?.classification || (acceptance ? "FROZEN_ACCEPTANCE_UNREVIEWED" : "DEVELOPMENT_ONLY"),
          acceptance_version: acceptance?.version ?? null,
          round: process.env.AGENTTEST_COLLEAGUE_ROUND ?? null,
          model_calls: result.summary.context_usage?.model_calls,
          total_tokens: result.summary.context_usage?.total_tokens,
          task_requests: result.summary.context_usage?.requests,
          target_events: events,
          complete: result.summary.complete,
          status: result.status,
        },
        null,
        2,
      ),
    );
    console.log(
      JSON.stringify({
        evidence,
        task: result.id,
        usage: result.summary.context_usage,
      }),
    );
    if (kind === "parcels" && process.env.AGENTTEST_COLLEAGUE_EXPECT_SOURCE_CONFLICT) {
      expect(events.every((event) => event.method === "GET" &&
        !event.path.startsWith("/parcels"))).toBe(true);
    } else if (kind === "parcels") {
      expect(
        events.some((e) => e.method === "POST" && e.path === "/parcels"),
      ).toBe(true);
      expect(
        events.some(
          (e) => e.method === "GET" && e.path.startsWith("/parcels/"),
        ),
      ).toBe(true);
      expect(events.some((e) => ["PATCH", "PUT"].includes(e.method))).toBe(
        true,
      );
      expect(events.some((e) => e.method === "DELETE")).toBe(true);
      expect(
        result.summary.cases?.some(
          (c) =>
            (c.approved_negative_steps || 0) > 0 &&
            c.negative_rejections_passed === c.approved_negative_steps &&
            c.outcome === "PASS",
        ),
      ).toBe(true);
    }
  } finally {
    await writeFile(path.join(evidence, "process-observation.json"), JSON.stringify(
      children.map((child) => ({pid: child.pid, exit_code: child.exitCode,
        signal: child.signalCode})), null, 2));
    if (task)
      await writeFile(
        path.join(evidence, "last-observed.json"),
        JSON.stringify(task, null, 2),
      );
    await page
      .screenshot({
        path: path.join(evidence, "last-page.png"),
        fullPage: true,
      })
      .catch(() => {});
    for (const child of children.reverse()) child.kill();
  }
});
