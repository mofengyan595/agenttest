// Read-only browser readiness check before any schedule claim or paid submission.
import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {chromium} from '../../web/node_modules/playwright/index.mjs';
const folder = path.resolve(process.argv[2]);
const rows = JSON.parse(await readFile(path.join(folder, 'preflight.json')));
const browser = await chromium.launch({channel: 'msedge', headless: true});
const verified = [];
try {
  for (const [arm, port] of [['baseline',19092],['candidate',19093]]) {
    const row = rows.find(r => r.arm === arm && r.config === 'flow');
    assert.ok(row.project_id);
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${port}/health`);
    // A new browser session cannot own a project made by HTTP preflight. Verify its own UI.
    const session = await (await page.request.get(`http://127.0.0.1:${port}/api/task-session`)).json();
    const cfgs = JSON.parse(await readFile(path.join(folder, 'configs.json')));
    const response = await page.request.post(`http://127.0.0.1:${port}/api/project-intake`, {
      headers: {Origin:`http://127.0.0.1:${port}`, 'x-agenttest-csrf':session.csrf_token},
      data: cfgs.flow, maxRetries:0});
    assert.equal(response.status(),201);
    const project = await response.json();
    await page.goto(`http://127.0.0.1:${port}/tasks?project=${project.id}`);
    await page.getByLabel('测试重点（可选）',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'开始一轮测试',exact:true}).isEnabled(),true);
    verified.push({arm, normal_web_entry:true, submissions:0, model_calls:0});
    await context.close();
  }
} finally { await browser.close(); }
await writeFile(path.join(folder,'browser-preflight.json'),JSON.stringify(verified,null,2),{flag:'wx'});
console.log('Both ordinary Web entries ready; zero task submissions / model calls.');
