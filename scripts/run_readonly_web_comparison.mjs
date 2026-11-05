// Run a pre-frozen two-arm schedule through the same normal web submission path.
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
import {setTimeout as delay} from "node:timers/promises";
import {LoginSchedule} from "./login_acceptance.mjs";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const {chromium} = require("playwright");
const out = path.resolve(process.argv[2]);
const raw = await readFile(path.join(out, "freeze.json"));
const freeze = JSON.parse(raw);
const sha256 = createHash("sha256").update(raw).digest("hex");
assert.equal(sha256, (await readFile(path.join(out, "freeze.sha256"), "utf8")).trim());
assert.match(freeze.target, /^http:\/\/127\.0\.0\.1:\d+$/);
const save = (file, data) => writeFile(path.join(out, file), JSON.stringify(data, null, 2) + "\n",
  {flag: "wx"});
const logins = new LoginSchedule(freeze.login_limits);
const projected = freeze.positions.reduce((result, item) => {
  for (const [account, count] of Object.entries(item.expected_logins)) {
    result[account] = (result[account] || 0) + count;
  }
  return result;
}, {});
const peak = Math.max(0, ...Object.values(projected));
const sourceTotal = Object.values(projected).reduce((sum, count) => sum + count, 0);
const projectedConcentration = peak >= logins.account.maxRequests ||
  sourceTotal >= logins.source.maxRequests;
for (const item of freeze.positions) {
  logins.earliest(0, item.expected_logins);
  const {document, generic} = item.config;
  assert.equal(generic.allow_mutations, false);
  assert.equal(generic.base_url, freeze.target);
  assert.ok(generic.roles.every(role => !role.login_operation || role.login_operation === "loginUser"));
  const methods = new Map(Object.entries(JSON.parse(document).paths).flatMap(([url, operations]) =>
    Object.entries(operations).map(([method, operation]) => [operation.operationId, {method, url}])));
  for (const operation of generic.operations.filter(item => item.enabled)) {
    const {method, url} = methods.get(operation.operation_id);
    assert.ok(method === "get" || operation.operation_id === "loginUser" &&
      method === "post" && url === "/api/auth/login", "Only GET and managed login are allowed");
  }
}
console.log(JSON.stringify({projected, sourceTotal, projectedConcentration}));

await save("schedule.claim.json", {sha256, projected, projectedConcentration,
  startedAt: new Date().toISOString()});
const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || "msedge"});
const records = [];
let stop;
try {
  for (const item of freeze.positions) {
    if (stop) {
      records.push({id: item.id, status: "NOT_EXECUTED", reason: stop});
      continue;
    }
    const now = Date.now();
    const waitMs = logins.earliest(now, item.expected_logins) - now;
    if (waitMs) {
      console.log(JSON.stringify({id: item.id, waitMs, reason: "LOGIN_WINDOW"}));
      await delay(waitMs + 50);
    }
    const slot = item.id;
    await mkdir(path.join(out, slot));
    const origin = `http://127.0.0.1:${freeze.ports[item.arm]}`;
    const context = await browser.newContext({viewport: {width: 1440, height: 1080}});
    const page = await context.newPage();
    const start = Date.now();
    let taskId;
    try {
      const health = await context.request.get(`${freeze.target}/api/health`, {timeout: 15_000});
      assert.equal(health.status(), 200);
      assert.equal((await health.json()).status, "UP");
      await page.goto(`${origin}/projects`);
      const session = await (await page.request.get(`${origin}/api/task-session`)).json();
      const saved = await page.request.post(`${origin}/api/project-intake`, {
        headers: {Origin: origin, "x-agenttest-csrf": session.csrf_token}, data: item.config,
      });
      const project = await saved.json();
      await save(`${slot}/project.json`, project);
      assert.equal(saved.status(), 201, "Intake rejected before any model or business request");
      assert.equal(project.ready, true);
      await page.goto(`${origin}/tasks?project=${project.id}`);
      await page.getByLabel("测试重点（可选）", {exact: true}).fill(item.request);
      await save(`${slot}/claim.json`, {id: slot, sha256, request: item.request,
        projectId: project.id, startedAt: new Date().toISOString()});
      const waiting = page.waitForResponse(response => response.url() === `${origin}/api/tasks` &&
        response.request().method() === "POST", {timeout: 60_000});
      await page.getByRole("button", {name: "开始一轮测试", exact: true}).click();
      const submitted = await waiting;
      const accepted = await submitted.json();
      taskId = accepted.id;
      await save(`${slot}/submitted.json`, accepted);
      assert.equal(submitted.status(), 202);
      let terminal;
      while (Date.now() - start < 700_000) {
        const current = await (await context.request.get(`${origin}/api/tasks/${taskId}`)).json();
        if (["FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "FAILED", "CANCELLED"].includes(current.status)) {
          terminal = current;
          break;
        }
        await delay(1000);
      }
      assert.ok(terminal, "Terminal evidence unavailable");
      const finishedAt = Date.now();
      await save(`${slot}/terminal.json`, terminal);
      const summary = terminal.summary;
      const requests = [];
      for (const {run_id: runId} of summary.cases || []) {
        if (!runId) continue;
        const result = await context.request.get(`${origin}/api/runs/${runId}`);
        assert.equal(result.status(), 200);
        const report = await result.json();
        await save(`${slot}/run-${runId}.json`, report);
        for (const step of [...report.steps, ...(report.investigations || []),
                            ...(report.lifecycle_cleanup || [])]) {
          for (const attempt of step.attempts) {
            const role = step.request_evidence?.role ?? step.role;
            const login = step.operation_id === "loginUser";
            const status = attempt.status_code;
            if (login) logins.observe(finishedAt, role, status, attempt.retry_after_seconds);
            requests.push({runId, operation: step.operation_id, method: step.method,
              path: step.path_template, role, values: step.request_evidence?.values, status,
              elapsedMs: attempt.elapsed_ms, responseSha256: attempt.response_sha256,
              projection: attempt.response_projection, retryAfterSeconds: attempt.retry_after_seconds,
              reason: login && status === 429 ? "LOGIN_RATE_LIMITED" : step.reason,
              classification: login && status === 429 ? "TEST_INFRA_ERROR" : step.outcome,
              validation: step.input_validation,
              checks: step.checks.map(({selector, passed}) => ({selector, passed}))});
          }
        }
      }
      const usage = summary.context_usage;
      const record = {id: slot, status: terminal.status, taskId, seconds: (finishedAt - start) / 1000,
        waitMs, usage, requests, reason: summary.reason, pending: summary.pending_negative_values,
        questions: summary.questions, design: summary.design_records?.map(({group_id, status,
          reason, model_call}) => ({group_id, status, reason, model_call}))};
      records.push(record);
      await save(`${slot}/evidence.json`, record);
      console.log(JSON.stringify({id: slot, status: terminal.status, usage, requests: requests.length}));
      if (requests.some(r => r.method !== "GET" && r.operation !== "loginUser")) {
        stop = "UNEXPECTED_TARGET_METHOD";
      } else if (usage?.model_calls && usage.usage_known !== true) {
        stop = "UNKNOWN_USAGE_GLOBAL_STOP";
      } else if (["INTERRUPTED", "CANCELLED"].includes(terminal.status)) {
        stop = "INTERRUPTED_DO_NOT_REPLAY";
      }
    } catch (error) {
      stop = "MISSING_EVIDENCE_DO_NOT_REPLAY";
      records.push({id: slot, taskId, status: stop, reason: String(error)});
      console.error(slot, String(error));
    } finally {
      await context.close();
    }
  }
} finally {
  await browser.close();
  await save("schedule-terminal.json", {sha256, records, stop});
}
if (stop) process.exitCode = 1;
