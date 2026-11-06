// Evaluation-side only. Expected results never enter the browser or model.
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import path from "node:path";
import {setTimeout as delay} from "node:timers/promises";
import {chromium} from "../web/node_modules/playwright/index.mjs";

const root = path.resolve(process.argv[2]);
const raw = await readFile(path.join(root, "freeze.json"));
const digest = createHash("sha256").update(raw).digest("hex");
assert.equal(digest, (await readFile(path.join(root, "freeze.sha256"), "utf8")).trim());
const freeze = JSON.parse(raw);
const arms = ["baseline", "candidate"];
const terminalStates = new Set(["FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "FAILED", "CANCELLED"]);
const evidence = {maxRetries: 2, timeout: 30_000};
function save(file, data) {
  return writeFile(path.join(root, file), JSON.stringify(data, null, 2) + "\n", {flag: "wx"});
}

for (const cfg of Object.values(freeze.configs)) {
  assert.match(cfg.generic.base_url, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.equal(cfg.generic.allow_mutations, false);
  assert.ok(cfg.generic.roles.every(role => !role.login_operation));
  const allowed = new Set(Object.values(JSON.parse(cfg.document).paths).map(item => {
    assert.deepEqual(Object.keys(item), ["get"]);
    return item.get.operationId;
  }));
  assert.ok(cfg.generic.operations.every(op => allowed.has(op.operation_id)));
}
await save("schedule-claim.json", {digest, started: new Date().toISOString()});
const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || "msedge"});
let stop;
const records = [];

async function position(round, arm, item) {
  const id = item.id + "_r" + round + "_" + arm;
  await mkdir(path.join(root, id));
  const origin = "http://127.0.0.1:" + freeze.ports[arm];
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  const started = Date.now();
  let stage = "PROJECT_INTAKE", taskId;
  try {
    await page.goto(origin + "/");
    const session = await (await page.request.get(origin + "/api/task-session", evidence)).json();
    const response = await page.request.post(origin + "/api/project-intake", {
      headers: {Origin: origin, "x-agenttest-csrf": session.csrf_token},
      data: freeze.configs[item.config],
    });
    const project = await response.json();
    await save(id + "/intake.json", {status: response.status(), project});
    if (response.status() !== 201 || !project.ready)
      return {id, status: "CONFIGURATION_ERROR", elapsed_ms: Date.now() - started};
    stage = "BROWSER_SUBMIT";
    await page.goto(origin + "/tasks?project=" + project.id);
    await page.getByLabel("测试重点（可选）", {exact: true}).fill(item.request);
    await save(id + "/claim.json", {id, digest, projectId: project.id,
      request: item.request, started: new Date().toISOString()});
    const wait = page.waitForResponse(response => response.url() === origin + "/api/tasks" &&
      response.request().method() === "POST", {timeout: 45_000});
    await page.getByRole("button", {name: "开始一轮测试", exact: true}).click();
    const submitted = await wait;
    const accepted = await submitted.json();
    assert.equal(submitted.status(), 202);
    taskId = accepted.id;
    await save(id + "/submission.json", {taskId, status: submitted.status()});
    stage = "TERMINAL_EVIDENCE";
    let terminal;
    while (Date.now() - started < 660_000) {
      const result = await page.request.get(origin + "/api/tasks/" + taskId, evidence);
      assert.equal(result.status(), 200);
      terminal = await result.json();
      if (terminalStates.has(terminal.status)) break;
      await delay(1200);
    }
    assert.ok(terminalStates.has(terminal?.status));
    await save(id + "/terminal.json", terminal);
    const usage = terminal.summary.context_usage;
    let calls = 0;
    for (const row of terminal.summary.cases || []) {
      if (!row.run_id) continue;
      const reportResponse = await page.request.get(origin + "/api/runs/" + row.run_id, evidence);
      assert.equal(reportResponse.status(), 200);
      const report = await reportResponse.json();
      await save(id + "/run-" + row.run_id + ".json", report);
      for (const step of [...report.steps, ...(report.investigations || []),
                           ...(report.lifecycle_cleanup || [])]) {
        calls += step.attempts.length;
        if (step.attempts.length && step.method !== "GET") stop = "UNAUTHORIZED_METHOD";
      }
    }
    if (usage?.model_calls && usage.usage_known !== true) stop = "MODEL_USAGE_UNKNOWN";
    if (["INTERRUPTED", "CANCELLED"].includes(terminal.status)) stop ||= "TASK_INTERRUPTED";
    return {id, taskId, status: terminal.status, elapsed_ms: Date.now() - started,
      model_calls: usage?.model_calls ?? 0, usage_known: usage?.usage_known,
      requests: calls, complete: terminal.summary.complete ?? false};
  } catch {
    stop ||= "EVIDENCE_UNAVAILABLE_DO_NOT_REPLAY";
    return {id, taskId, status: stop, stage, elapsed_ms: Date.now() - started};
  } finally {
    await context.close();
  }
}

try {
  for (let round = 1; round <= freeze.rounds; round++) {
    const items = round % 2 ? freeze.tasks : [...freeze.tasks.slice(12), ...freeze.tasks.slice(0, 12)];
    const orderedArms = round % 2 ? arms : [...arms].reverse();
    for (const item of items) {
      const batch = stop ? orderedArms.map(arm => ({id: item.id + "_r" + round + "_" + arm,
        status: "NOT_EXECUTED", reason: stop})) :
        await Promise.all(orderedArms.map(arm => position(round, arm, item)));
      for (const entry of batch) {
        records.push(entry);
        await save(entry.id + ".json", entry);
        console.log(JSON.stringify(entry));
      }
    }
  }
} finally {
  await browser.close();
  await save("schedule-terminal.json", {digest, records, stop});
}
if (stop) process.exitCode = 1;
