/** Browser proof for the packaged human review UI. Entirely synthetic and loopback-only. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, sep, join } from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(join(process.env.BROWSER_TEST_DEPS, 'node_modules/playwright'));
const assets = fileURLToPath(new URL('../../custom_components/ninebot/frontend/', import.meta.url));
const output = process.env.BROWSER_TEST_OUTPUT;
const server = createServer(async (request, response) => {
  const path = new URL(request.url, 'http://localhost').pathname;
  if (path === '/') {
    response.writeHead(200, { 'Content-Type': 'text/html' });
    response.end('<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Synthetic task review</title><body style="margin:0"><script type="module" src="/ninebot-static/assistant-panel.js"></script></body></html>');
    return;
  }
  try {
    const file = resolve(assets, path.slice('/ninebot-static/'.length));
    if (!path.startsWith('/ninebot-static/') || !file.startsWith(resolve(assets) + sep)) throw new Error('Not an asset');
    response.writeHead(200, { 'Content-Type': 'application/javascript' });
    response.end(await readFile(file));
  } catch { response.writeHead(404); response.end(); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
try {
  await mkdir(output, { recursive: true });
  const results = [];
  for (const [language, theme, width] of [['en', 'light', 1100], ['en', 'dark', 1100], ['zh-Hans', 'light', 430], ['zh-Hans', 'dark', 430]]) {
    const page = await browser.newPage({ viewport: { width, height: 960 } });
    const errors = [], external = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => {
      if (new URL(route.request().url()).origin !== origin) { external.push(route.request().url()); return route.abort(); }
      return route.continue();
    });
    await page.goto(origin);
    await page.evaluate(async ({ language, theme }) => {
      await customElements.whenDefined('ninebot-assistant-panel');
      const colors = theme === 'dark' ? { '--primary-background-color':'#111827', '--card-background-color':'#1f2937', '--primary-text-color':'#f3f4f6', '--secondary-text-color':'#d1d5db', '--divider-color':'#64748b' }
        : { '--primary-background-color':'#f8fafc', '--card-background-color':'#ffffff', '--primary-text-color':'#111827', '--secondary-text-color':'#475569', '--divider-color':'#94a3b8' };
      for (const [name, value] of Object.entries({ ...colors, '--primary-color':'#0284c7', '--error-color':theme === 'dark' ? '#fca5a5' : '#b91c1c' })) document.documentElement.style.setProperty(name, value);
      document.body.style.background = 'var(--primary-background-color)';
      window.reviewCalls = [];
      const task = { task_id:'a'.repeat(32), vehicle_ref:'vehicle-a', state:'awaiting_approval', plan_hash:'b'.repeat(64), start_date:'2026-11-05', end_date:'2026-11-06', time_zone:'Asia/Shanghai', selected_count:1, maximum_detail_attempts:2, saved_count:null, attempts_used:null, approval_expires_at:'2099-01-01T00:00:00Z' };
      const hass = { language, user:{id:'synthetic-user'}, connection:{}, callWS:async (message) => {
        window.reviewCalls.push(message.operation || 'vehicles');
        if (message.type.endsWith('/vehicles')) return {data:{vehicles:[{vehicle_ref:'vehicle-a',label:'<img src=x onerror=alert(1)> Synthetic A'}, {vehicle_ref:'vehicle-b',label:'Synthetic B'}]}};
        if (message.operation === 'list') return {tasks:[{...task}]};
        if (message.operation === 'prepare') return {task:{...task, ride_ids:['synthetic-record-a']},token:'synthetic-secret-confirmation'};
        task.state = message.operation === 'approve' ? 'queued' : 'cancelled';
        return {task:{...task}};
      } };
      const panel = document.createElement('ninebot-assistant-panel'); panel.hass = hass;
      document.body.append(panel); window.fixturePanel = panel;
    }, { language, theme });
    const zh = language.startsWith('zh');
    await page.locator('select option').nth(2).waitFor({state:'attached'});
    assert.equal(await page.locator('select').inputValue(), '');
    await page.locator('select').selectOption('vehicle-a');
    await page.getByRole('button', {name:zh?'审核':'Review', exact:true}).click();
    await page.getByRole('button', {name:zh?'批准并同步':'Approve and synchronize', exact:true}).waitFor();
    assert.equal(await page.locator('ninebot-assistant-panel img').count(), 0);
    assert.equal(await page.locator('ninebot-assistant-panel').evaluate((el) => el.shadowRoot.innerHTML.includes('synthetic-secret-confirmation')), false);
    assert.equal(await page.evaluate(() => reviewCalls.includes('approve')), false);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false);
    await page.screenshot({path:join(output, `${language}-${theme}.png`), fullPage:true});
    await page.getByRole('button', {name:zh?'批准并同步':'Approve and synchronize', exact:true}).click();
    await page.getByRole('heading', {name:zh?'已排队':'Queued', exact:true}).waitFor();
    await page.getByRole('button', {name:zh?'取消任务':'Cancel task', exact:true}).click();
    await page.getByRole('heading', {name:zh?'已取消':'Cancelled', exact:true}).waitFor();
    assert.deepEqual(await page.evaluate(() => reviewCalls), ['vehicles','list','prepare','approve','cancel']);
    await page.locator('ninebot-assistant-panel').evaluate((el) => el.remove());
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    results.push({language,theme,width,externalRequests:external.length,errors,scopeReviewed:true,explicitApproval:true,cancelled:true,tokenInDom:false,overflow});
    await page.close();
  }
  await writeFile(join(output, 'results.json'), JSON.stringify(results, null, 2));
} finally { await browser.close(); await new Promise((done) => server.close(done)); }
