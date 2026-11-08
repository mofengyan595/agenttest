// Batch-only ordinary Web UI submission. Frozen answers never enter the page or product.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {mkdir, readFile, readdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {chromium} from '../../web/node_modules/playwright/index.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repository = path.resolve(here, '../..');
const folder = path.resolve(process.argv[2]);
const python = path.join(repository, '.venv/Scripts/python.exe');
const raw = await readFile(path.join(folder, 'freeze.json'));
const hash = data => createHash('sha256').update(data).digest('hex');
const digest = hash(raw);
assert.equal(digest, (await readFile(path.join(folder, 'freeze.sha256'), 'utf8')).trim());
const freeze = JSON.parse(raw);
const sources = JSON.parse(await readFile(path.join(folder, 'source-manifest.json')));
const save = (name, data) => writeFile(path.join(folder, name),
  JSON.stringify(data, null, 2) + '\n', {flag: 'wx'});
const json = async name => JSON.parse(await readFile(path.join(folder, name)));
const terminalStates = new Set(['FINISHED', 'NEEDS_REVIEW', 'FAILED', 'INTERRUPTED', 'CANCELLED']);
const records = [];
let stop = null;
async function verify() {
  assert.equal(execFileSync('git', ['-C', repository, 'rev-parse', 'HEAD'],
    {encoding: 'utf8'}).trim(), freeze.root_head, 'ROOT_HEAD_DRIFT');
  for (const [name, sha] of Object.entries(freeze.root_files))
    assert.equal(hash(await readFile(path.join(repository, name))), sha, 'ROOT_SOURCE_DRIFT:' + name);
  for (const [name, sha] of Object.entries(freeze.evaluator_sha256))
    assert.equal(hash(await readFile(path.join(here, name))), sha, 'EVALUATOR_DRIFT:' + name);
  for (const arm of ['baseline', 'candidate']) {
    for (const [name, sha] of Object.entries(sources[arm]))
      assert.equal(hash(await readFile(path.join(folder, 'source-' + arm, name))), sha,
        'ARM_SOURCE_DRIFT:' + arm + ':' + name);
  }
  const state = await (await fetch('http://127.0.0.1:' + freeze.ports.target + '/__state')).json();
  assert.equal(state.state_sha256, freeze.target_state_sha256, 'TARGET_STATE_DRIFT');
}
async function active(value) {
  // The fixture reads this harness-owned file. No control API exists on the business target.
  await writeFile(path.join(folder, 'active.json'), JSON.stringify(value));
}
async function audits(id) {
  const text = await readFile(path.join(folder, 'target-audit.jsonl'), 'utf8').catch(e => {
    if (e.code === 'ENOENT') return ''; throw e;
  });
  return text.trim().split('\n').filter(Boolean).map(JSON.parse).filter(r => r.position === id);
}
async function position(turn, arm, task) {
  const id = `${task.id}_r${turn}_${arm}`;
  await mkdir(path.join(folder, id));
  const origin = 'http://127.0.0.1:' + freeze.ports[arm];
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  const start = Date.now();
  let taskId, stage = 'PREFLIGHT', session, submittedAt;
  const observationFolder = path.join(folder, arm + '-observations');
  const before = new Set(await readdir(observationFolder).catch(() => []));
  try {
    await verify();
    await active({id, config: task.config, arm});
    await page.goto(origin + '/health');
    session = await (await page.request.get(origin + '/api/task-session', {maxRetries: 0})).json();
    const intake = await page.request.post(origin + '/api/project-intake', {maxRetries: 0,
      headers: {Origin: origin, 'x-agenttest-csrf': session.csrf_token},
      data: freeze.configs[task.config]});
    const project = await intake.json();
    await save(id + '/intake.json', {status: intake.status(), project});
    assert.equal(intake.status(), 201, 'INTAKE_INFRASTRUCTURE_ERROR');
    assert.equal(project.ready, true, 'INTAKE_NOT_READY');
    stage = 'WEB_SUBMIT';
    await page.goto(origin + '/tasks?project=' + project.id);
    await page.getByLabel('测试重点（可选）', {exact: true}).fill(task.request);
    await save(id + '/claim.json', {id, digest, request: task.request, projectId: project.id});
    const pending = page.waitForResponse(r => r.url() === origin + '/api/tasks' &&
      r.request().method() === 'POST', {timeout: 45000});
    await page.getByRole('button', {name: '开始一轮测试', exact: true}).click();
    const response = await pending;
    const accepted = await response.json();
    assert.equal(response.status(), 202, 'SUBMISSION_INFRASTRUCTURE_ERROR');
    taskId = accepted.id;
    submittedAt = Date.now();
    await save(id + '/submission.json', {taskId, status: response.status()});
    stage = 'WAIT_TERMINAL';
    let terminal;
    while (Date.now() - submittedAt < freeze.limits.max_seconds * 1000) {
      const result = await page.request.get(origin + '/api/tasks/' + taskId, {maxRetries: 0});
      assert.equal(result.status(), 200);
      terminal = await result.json();
      if (terminalStates.has(terminal.status)) break;
      await delay(700);
    }
    if (!terminalStates.has(terminal?.status)) {
      stop = 'OPERATOR_180_SECOND_TIMEOUT';
      await page.request.post(origin + '/api/tasks/' + taskId + '/cancel', {maxRetries: 0,
        headers: {Origin: origin, 'x-agenttest-csrf': session.csrf_token}});
      await delay(500);
      terminal = await (await page.request.get(origin + '/api/tasks/' + taskId,
        {maxRetries: 0})).json();
    }
    await save(id + '/terminal.json', terminal);
    assert.equal(terminal.summary.budget.max_model_calls,4,'NATIVE_MODEL_CAP_MISMATCH');
    assert.equal(terminal.summary.budget.max_seconds,180,'NATIVE_TIME_CAP_MISMATCH');
    const terminalAt=Date.now();
    const observations = [];
    for (const name of await readdir(observationFolder).catch(() => [])) {
      if (!name.endsWith('.json') || before.has(name)) continue;
      observations.push(JSON.parse(await readFile(path.join(observationFolder, name))));
    }
    await save(id + '/observations.json', observations);
    const reports = [];
    for (const row of terminal.summary.cases || []) {
      if (!row.run_id) continue;
      const result = await page.request.get(origin + '/api/runs/' + row.run_id, {maxRetries: 0});
      assert.equal(result.status(), 200);
      const report = await result.json();
      await save(id + '/run-' + row.run_id + '.json', report);
      reports.push(report);
    }
    await save(id + '/audit.json', await audits(id));
    const scored = JSON.parse(execFileSync(python, [path.join(here, 'collect.py'),
      'position', folder, id], {encoding: 'utf8', env: {...process.env,
        PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1'}}));
    await save(id + '/score.json', scored);
    const usage = terminal.summary.context_usage || {};
    if (usage.model_calls > 0 && usage.usage_known !== true) stop ||= 'MODEL_USAGE_UNKNOWN';
    if (scored.infrastructure || !scored.evidence_integrity) stop ||= 'INFRASTRUCTURE_ERROR';
    if (scored.configured_scope_violations) stop ||= 'CONFIGURED_AUTHORITY_VIOLATION';
    if (['INTERRUPTED', 'CANCELLED', 'FAILED'].includes(terminal.status)) stop ||= 'TASK_INTERRUPTED';
    if ((terminal.summary.timings?.total_ms || 0) >= 180000) stop ||= 'TASK_TIME_LIMIT';
    const modelRecords = (terminal.summary.design_records || []).filter(r => r.model_call);
    if (modelRecords.some(r => r.model_call.calls > 0 && r.model_call.total_tokens == null))
      stop ||= 'MODEL_USAGE_UNKNOWN';
    if (modelRecords.some(r => ['MODEL_TIMEOUT', 'MODEL_CONNECTION_ERROR', 'MODEL_API_ERROR',
        'MODEL_SERVICE_ERROR', 'MODEL_USAGE_UNKNOWN', 'MODEL_TOKEN_BUDGET',
        'MODEL_TOTAL_TOKEN_BUDGET'].includes(r.model_call.reason || r.reason)))
      stop ||= 'MODEL_INFRASTRUCTURE_ERROR';
    if (modelRecords.some(r => r.model_call.model_name && r.model_call.model_name !== 'ecnu-max'))
      stop ||= 'MODEL_CONFIGURATION_DRIFT';
    assert.ok((usage.model_calls || 0) <= 4, 'MODEL_CALL_LIMIT');
    assert.ok((usage.known_tokens || 0) <= 500000, 'MODEL_TOKEN_LIMIT');
    assert.ok(scored.target_requests <= 8, 'TARGET_REQUEST_LIMIT');
    await verify();
    return {id, arm, task: task.id, round: turn, taskId, status: terminal.status,
      elapsed_ms: Date.now() - start, task_elapsed_ms: terminalAt - submittedAt,
      model_calls: usage.model_calls || 0, usage_known: usage.usage_known,
      known_tokens: usage.known_tokens || 0, ...scored};
  } catch (error) {
    const message = String(error?.message || '').slice(0, 200);
    stop ||= /DRIFT/.test(message) ? 'SOURCE_OR_TARGET_DRIFT' : 'EVIDENCE_OR_UI_INFRASTRUCTURE_ERROR';
    await save(id + '/harness-error.json', {stage, error_class: error.name, message});
    return {id, arm, task: task.id, round: turn, taskId, status: stop, stage,
      elapsed_ms: Date.now() - start};
  } finally {
    await active({id: '_idle'});
    await context.close();
  }
}
await verify();
await save('schedule-claim.json', {digest, started: new Date().toISOString(), positions: 48,
  retries: 0, source: '2026-11-07 operator authorization'});
let browser;
try {
  browser = await chromium.launch({channel: 'msedge', headless: true});
  for (let turn = 1; turn <= 2; turn++) {
    const tasks = turn === 1 ? freeze.tasks : [...freeze.tasks].reverse();
    const arms = turn === 1 ? ['baseline', 'candidate'] : ['candidate', 'baseline'];
    for (const task of tasks) for (const arm of arms) {
      const row = stop ? {id: `${task.id}_r${turn}_${arm}`, arm, task: task.id, round: turn,
        status: 'NOT_EXECUTED', reason: stop} : await position(turn, arm, task);
      records.push(row);
      await save(row.id + '.json', row);
      // Machine journal only; the operator gets a final aggregate, not 48 manual interactions.
      console.log(JSON.stringify({id: row.id, status: row.status, calls: row.model_calls,
        target_requests: row.target_requests, stop}));
    }
  }
} catch (error) {
  stop ||= 'BROWSER_OR_SCHEDULER_INFRASTRUCTURE_ERROR';
  await save('scheduler-error.json', {error_class:error.name, message:String(error.message).slice(0,200)});
  for (let turn=1;turn<=2;turn++) for (const task of freeze.tasks)
    for (const arm of ['baseline','candidate']) {
      const id=`${task.id}_r${turn}_${arm}`;
      if (records.some(r=>r.id===id)) continue;
      const row={id,arm,task:task.id,round:turn,status:'NOT_EXECUTED',reason:stop};
      records.push(row); await save(id+'.json',row);
    }
} finally {
  if (browser) await browser.close();
  await save('schedule-terminal.json', {digest, records, stop, finished: new Date().toISOString()});
}
if (stop) process.exitCode = 1;
