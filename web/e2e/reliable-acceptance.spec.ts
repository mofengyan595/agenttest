import { expect, test } from "@playwright/test";
import { spawn, execFile, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer } from "node:net";
import { mkdir, readFile, writeFile, readdir, unlink } from "node:fs/promises";
import { promisify } from "node:util";
import path from "node:path";
import type { GoalTask } from "../src/api/tasks";

type FrozenTask = {id: string; project: string; request: string;
  initial_body?: Record<string, unknown>; update_body?: Record<string, unknown>;
  required_operations: string[]; read_count?: number; approved_reader_status?: number;
  negative_missing_field?: string; negative_status?: number; query?: Record<string, unknown>;
  injected_status?: number; minimum_investigation_requests?: number;
  maximum_investigation_requests?: number; write_lifecycle_required: boolean; fixed_runs: number};
type Case = NonNullable<GoalTask["summary"]["cases"]>[number];
const terminal = new Set(["FINISHED", "NEEDS_REVIEW", "CANCELLED", "INTERRUPTED"]);
const runFile = promisify(execFile);

// External oracle only. This module is never imported by a product planner or server.
function checkOriginal(task: GoalTask, frozen: FrozenTask): Case | undefined {
  expect(task.status).toBe("FINISHED");
  expect(task.summary.mode).toBe("MODEL_DESIGNED");
  expect(task.summary.context_usage?.usage_known).toBe(true);
  expect(task.summary.context_usage?.model_calls).toBeGreaterThan(0);
  const cases = task.summary.cases || [];
  const evidence = cases.flatMap((c) => c.evidence || []);
  const operations = new Set(evidence.map((e) => e.operation_id));
  for (const operation of frozen.required_operations) expect(operations.has(operation)).toBe(true);
  expect([...operations].every((op) => frozen.required_operations.includes(op))).toBe(true);
  if (frozen.project === "directory") {
    const read = evidence.find((e) => e.operation_id === "readSection12");
    expect(read?.request?.values?.query).toEqual(frozen.query);
    const failed = cases.find((c) => c.outcome === "ASSERTION_FAILED" &&
      c.evidence?.some((e) => e.operation_id === "readAvailability"));
    expect(failed).toBeTruthy();
    expect(failed?.evidence?.some((e) => e.checks.some((c) => !c.passed &&
      c.kind === "http_status" && c.actual === frozen.injected_status))).toBe(true);
    const probes = failed?.investigations || [];
    expect(probes.reduce((n, p) => n + p.requests, 0)).toBeGreaterThanOrEqual(
      frozen.minimum_investigation_requests!);
    expect(probes.reduce((n, p) => n + p.requests, 0)).toBeLessThanOrEqual(
      frozen.maximum_investigation_requests!);
    expect(probes.every((p) => ["GET", "HEAD", "OPTIONS"].includes(p.method))).toBe(true);
    expect(failed?.failure_explanation?.cause_confirmed).toBe(false);
    expect(failed?.failure_explanation?.next_action).toBeTruthy();
    return undefined;
  }
  expect(task.summary.complete).toBe(true);
  expect(task.summary.checks_passed).toBe(true);
  const source = cases.find((c) => c.outcome === "PASS" && c.resource_lifecycle?.length &&
    c.run_id && task.summary.exports?.[c.run_id]?.available);
  expect(source, JSON.stringify(task.summary)).toBeTruthy();
  expect(source?.resource_lifecycle?.every((r) => r.status === "CLEANED" &&
    r.owner_run_id === source.run_id && r.resource_id !== null)).toBe(true);
  const creation = source?.evidence?.find((e) => e.method === "POST" &&
    e.operation_id === (frozen.project === "parcels" ? "createParcel" : "createReservation"));
  expect(creation?.request?.values?.body).toEqual(frozen.initial_body);
  if (frozen.project === "parcels") {
    expect(source?.evidence?.filter((e) => e.operation_id === "readParcel")).toHaveLength(
      frozen.read_count!);
    expect(source?.evidence?.find((e) => e.operation_id === "changeParcel")?.request?.values?.body)
      .toEqual(frozen.update_body);
    expect(source?.evidence?.map((e) => e.operation_id)).toEqual([
      "createParcel", "readParcel", "changeParcel", "readParcel", "deleteParcel"]);
  } else {
    expect(evidence.some((e) => e.operation_id === "readReservation" && e.request?.role === "reader"
      && e.checks.some((c) => c.kind === "http_status" && c.passed &&
        c.actual === frozen.approved_reader_status))).toBe(true);
    expect(cases.some((c) => (c.approved_negative_steps || 0) > 0 && c.evidence?.some((e) =>
      e.operation_id === "createReservation" &&
      !(frozen.negative_missing_field! in (e.request?.values?.body as object || {})) &&
      e.checks.some((k) => k.kind === "http_status" && k.passed && k.actual === frozen.negative_status))))
      .toBe(true);
  }
  return source;
}

async function freeGroup(): Promise<number> {
  for (let attempt = 0; attempt < 20; attempt++) {
    const initial = createServer();
    await new Promise<void>((resolve) => initial.listen(0, "127.0.0.1", resolve));
    const address = initial.address();
    if (!address || typeof address === "string") throw new Error("Loopback port unavailable");
    const port = address.port;
    await new Promise<void>((resolve) => initial.close(() => resolve()));
    if (port > 65532) continue;
    const sockets = [];
    try {
      for (let offset = 0; offset < 4; offset++) {
        const socket = createServer();
        sockets.push(socket);
        await new Promise<void>((resolve, reject) => {
          socket.once("error", reject); socket.listen(port + offset, "127.0.0.1", resolve);
        });
      }
      return port;
    } catch { /* No process is stopped; choose another unused group. */ }
    finally { await Promise.all(sockets.filter((s) => s.listening).map((s) =>
      new Promise<void>((resolve) => s.close(() => resolve())))); }
  }
  throw new Error("Four isolated loopback ports unavailable");
}

test("frozen reliable colleague first attempt", async ({page}) => {
  test.skip(process.env.AGENTTEST_RELIABLE_REAL !== "1", "Explicit real-model acceptance only");
  test.setTimeout(900_000);
  page.setDefaultTimeout(30_000);
  const repository = path.resolve("..");
  const manifest = JSON.parse(await readFile(process.env.AGENTTEST_RELIABLE_MANIFEST!, "utf8"));
  const frozen: FrozenTask = manifest.tasks.find((t: FrozenTask) =>
    t.id === process.env.AGENTTEST_RELIABLE_TASK);
  if (!frozen) throw new Error("Frozen task missing");
  const evidence = process.env.AGENTTEST_RELIABLE_EVIDENCE!;
  const relative = path.relative(path.join(repository, "runs/reliable-colleague"), evidence);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Evidence outside root");
  await mkdir(evidence, {recursive: true});
  const python = path.join(repository, process.platform === "win32" ?
    ".venv/Scripts/python.exe" : ".venv/bin/python");
  const port = await freeGroup(), origin = `http://127.0.0.1:${port}`;
  const target = `http://127.0.0.1:${port + (frozen.project === "parcels" ? 1 :
    frozen.project === "reservations" ? 2 : 3)}`;
  const data = path.join(evidence, "lab"), stop = path.join(evidence, "stop.request");
  const credentialDirectory = path.join(repository, ".venv/tmp/reliable-" + randomUUID());
  const sessionFile = path.join(credentialDirectory, ".env");
  const started = Date.now();
  let phase = "startup", sentinel: string | null = null, harnessRequests = 0;
  let passed = false, error: string | null = null, child: ChildProcess | null = null;
  let cleanupConfirmed = false;
  const fixed: GoalTask[] = [];
  let original: GoalTask | null = null;
  const save = async (name: string, value: unknown) => writeFile(path.join(evidence, name),
    JSON.stringify(value, null, 2) + "\n", {flag: "wx"});
  const wait = async (id: string): Promise<GoalTask> => {
    let row: GoalTask;
    await expect.poll(async () => {
      row = await (await page.request.get(`${origin}/api/tasks/${id}`)).json();
      return terminal.has(row.status);
    }, {timeout: 650_000, intervals: [1000]}).toBe(true);
    return row!;
  };
  try {
    child = spawn(python, ["-X", "utf8", "scripts/start_colleague_lab.py", "--port", String(port),
      "--data", data, "--stop-file", stop], {cwd: repository, windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"], env: {...process.env, PYTHONUTF8: "1"}});
    let serverLog = "";
    child.stdout?.on("data", (v) => { serverLog += v.toString(); });
    child.stderr?.on("data", (v) => { serverLog += v.toString(); });
    child.once("exit", () => { void writeFile(path.join(evidence, "server.log"), serverLog); });
    await expect.poll(async () => {
      try { return (await page.request.get(origin + "/health", {timeout: 1000})).ok(); }
      catch { return false; }
    }, {timeout: 90_000}).toBe(true);
    await expect.poll(async () => {
      try { return (await page.request.get(target + "/health", {timeout: 1000})).ok(); }
      catch { return false; }
    }, {timeout: 90_000}).toBe(true);
    await page.goto(origin);
    const receipt = JSON.parse(await readFile(path.join(data, "lab-projects.json"), "utf8"));
    const projectId = receipt.projects[frozen.project];
    if (frozen.project === "parcels") {
      harnessRequests++;
      const response = await page.request.post(target + "/parcels", {data: {
        name: "foreign_reference_611", quantity: 1,
        meta: {labels: ["blue"], options: {tracked: true}}}});
      expect(response.status()).toBe(201);
      sentinel = (await response.json()).id;
      expect(sentinel).toMatch(/^parcel_[a-f0-9]{12}$/);
      await save("other-task-resource.json", {id: sentinel, owner: "EXTERNAL_ACCEPTANCE_TASK"});
    }
    await page.locator(`a[href="/tasks?project=${projectId}"]`).first().click();
    await page.getByLabel("测试重点（可选）", {exact: true}).fill(frozen.request);
    phase = "submit";
    const submittedResponse = page.waitForResponse((r) => r.url().endsWith("/api/tasks") &&
      r.request().method() === "POST");
    await page.getByRole("button", {name: "开始一轮测试", exact: true}).click();
    const response = await submittedResponse;
    const submitted = await response.json();
    await save("submitted.json", submitted);
    expect(response.status()).toBe(202);
    phase = "original";
    original = await wait(submitted.id);
    await save("first-attempt.json", original);
    phase = "oracle";
    const source = checkOriginal(original, frozen);
    await page.reload();
    await expect(page.getByRole("region", {name: "本轮测试结果"})).toBeVisible();
    if (frozen.project === "directory")
      await expect(page.getByLabel("调查结论")).toContainText("内部原因尚未证实");
    else await expect(page.getByLabel("本轮数据清理").first()).toContainText("已清理 1 项");
    await page.screenshot({path: path.join(evidence, "desktop-result.png"), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(evidence, "mobile-result.png"), fullPage: true});
    await page.screenshot({path: path.join(evidence, "mobile-result-viewport.png")});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.setViewportSize({width: 1440, height: 1080});
    if (source && frozen.fixed_runs) {
      phase = "save";
      const card = page.locator(".scenario-card").filter({has:
        page.locator(`a[href="/runs/${source.run_id}"]`)});
      const saved = page.waitForResponse((r) => r.url().endsWith(`/api/tasks/${original!.id}/regression`));
      await card.getByRole("button", {name: "保存测试", exact: true}).click();
      expect((await saved).status()).toBe(200);
      phase = "fixed-browser";
      await page.goto(origin + "/saved");
      const repeated = page.waitForResponse((r) => r.url().endsWith("/api/saved-tests/run") &&
        r.request().method() === "POST");
      await page.getByRole("button", {name: "再次运行固定测试", exact: true}).first().click();
      const first = await wait((await (await repeated).json()).id);
      fixed.push(first); await save("fixed-browser.json", first);
      expect(first.summary.fixed_regression_passed).toBe(true);
      await expect(page.getByLabel("本轮数据清理")).toContainText("已清理 1 项");
      // A protected .env transfers this browser's local ownership to the CLI, never to a model.
      const cookie = (await page.context().cookies(origin + "/api/task-session"))
        .find((c) => c.name === "agenttest_task_session");
      expect(cookie).toBeTruthy();
      await mkdir(credentialDirectory, {recursive: true});
      await writeFile(sessionFile, `AGENTTEST_WORKSPACE_ORIGIN=${origin}\n` +
        `AGENTTEST_WORKSPACE_SESSION=${cookie!.value}\n`, {flag: "wx", mode: 0o600});
      phase = "fixed-cli";
      const output = path.join(evidence, "fixed-cli.json");
      const cli = await runFile(python, ["-m", "agenttest", "workspace", "--server", origin,
        "--session-file", sessionFile, "regression", "--task-id", original.id, "--run-id", source.run_id!,
        "--output", output], {cwd: repository, windowsHide: true, timeout: 90_000,
          env: {...process.env, PYTHONUTF8: "1"}});
      await writeFile(path.join(evidence, "fixed-cli.log"), cli.stdout + cli.stderr);
      const artifact = JSON.parse(await readFile(output, "utf8"));
      expect(artifact.task.ok).toBe(true);
      fixed.push(artifact.task.data);
      const ids = new Set(source.resource_lifecycle!.map((r) => r.resource_id));
      for (const row of fixed) {
        expect(row.summary.context_usage?.model_calls).toBe(0);
        expect(row.summary.fixed_regression_passed).toBe(true);
        for (const resource of row.summary.resource_lifecycle || []) {
          expect(resource.status).toBe("CLEANED");
          expect(ids.has(resource.resource_id)).toBe(false);
          ids.add(resource.resource_id);
        }
      }
      expect(ids.size).toBe(3);
      const health = await (await page.request.get(target + "/health")).json();
      expect(health.record_count).toBe(sentinel ? 1 : 0);
      if (sentinel) {
        harnessRequests++;
        expect((await page.request.get(target + "/parcels/" + sentinel)).status()).toBe(200);
      }
      phase = "configuration-change";
      await page.goto(origin);
      await page.locator(".project-cards > section").filter({has:
        page.locator(`a[href="/tasks?project=${projectId}"]`)}).getByRole("button", {name: "补充信息"}).click();
      await page.getByLabel("业务说明（可选）", {exact: true}).fill("本次检查后更新配置版本，原证据保留。");
      await page.getByLabel("我已核对所选接口、输入和检查规则，确认用于此隔离测试环境", {exact: true}).check();
      const updated = page.waitForResponse((r) => r.url().endsWith(`/api/project-intake/${projectId}`)
        && r.request().method() === "POST");
      await page.getByRole("button", {name: "保存并检查接入", exact: true}).click();
      expect((await updated).status()).toBe(200);
      await page.goto(origin + "/saved");
      await expect(page.getByRole("button", {name: "再次运行固定测试", exact: true}).first()).toBeDisabled();
      const session = await (await page.request.get(origin + "/api/task-session")).json();
      const blocked = await page.request.post(origin + "/api/saved-tests/run", {
        headers: {"x-agenttest-csrf": session.csrf_token}, data: {task_id: original.id,
          run_id: source.run_id, submission_id: randomUUID().replaceAll("-", "")}});
      expect(blocked.status()).toBe(409);
      await save("configuration-change.json", {blocked_status: blocked.status(), target_requests: 0,
        model_calls: 0, original_resource_ids: [...ids]});
    }
    passed = true;
  } catch (cause) {
    error = cause instanceof Error ? cause.message.slice(0, 8000) : "ACCEPTANCE_STOPPED";
    await page.screenshot({path: path.join(evidence, "first-failure.png"), fullPage: true}).catch(() => {});
    throw cause;
  } finally {
    if (sentinel) {
      harnessRequests++;
      await page.request.delete(target + "/parcels/" + sentinel).catch(() => {});
    }
    if (child) {
      await writeFile(stop, "Stop only this launcher's owned children.\n");
      cleanupConfirmed = await new Promise<boolean>((resolve) => {
        const timer = setTimeout(() => resolve(false), 30_000);
        if (child!.exitCode !== null) { clearTimeout(timer); resolve(true); }
        else child!.once("exit", () => { clearTimeout(timer); resolve(true); });
      });
      if (!cleanupConfirmed) child.kill();
    }
    await unlink(sessionFile).catch(() => {});
    let observedRequests: number | null = null;
    try {
      const instances = await readdir(path.join(data, "target-events"));
      const events = (await readFile(path.join(data, "target-events", instances[0],
        frozen.project + ".jsonl"), "utf8")).split("\n").filter(Boolean).map((line) => JSON.parse(line));
      observedRequests = events.filter((e) => e.path !== "/health").length;
    } catch { /* A startup failure remains explicit, never counted as zero HTTP. */ }
    await save("position-result.json", {passed, task: frozen.id, phase, error,
      seconds: (Date.now() - started) / 1000, cleanup_confirmed: cleanupConfirmed,
      original_task_id: original?.id, fixed_tasks: fixed.map((r) => r.id),
      observed_target_requests: observedRequests, external_checker_requests: harnessRequests,
      task_target_requests: observedRequests === null ? null : observedRequests - harnessRequests});
  }
});
