/** Milestone-only browser acceptance against the pytest-owned native HA server. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
const lines = createInterface({input:process.stdin})[Symbol.asyncIterator]();
const config = JSON.parse((await lines.next()).value);
const origin = new URL(config.url).origin;
assert.equal(new URL(config.url).hostname, '127.0.0.1');
const require = createRequire(import.meta.url);
const { chromium } = require(join(process.env.BROWSER_TEST_DEPS, 'node_modules/playwright'));
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:1100,height:960}});
  const errors = [], external = [], operations = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin) {
      external.push(route.request().url()); return route.abort();
    }
    return route.continue();
  });
  await page.goto(config.url);
  await page.evaluate(async ({token,user}) => {
    await customElements.whenDefined('ninebot-assistant-panel');
    const socket = new WebSocket(`${location.origin.replace('http','ws')}/api/websocket`);
    let sequence = 0;
    const pending = new Map();
    window.nativeOperations = [];
    const authenticated = new Promise((resolve,reject) => {
      socket.onerror = () => reject(new Error('Native websocket failed'));
      socket.onmessage = ({data}) => {
        const message = JSON.parse(data);
        if (message.type === 'auth_required') socket.send(JSON.stringify({type:'auth',access_token:token}));
        else if (message.type === 'auth_ok') resolve();
        else if (message.type === 'auth_invalid') reject(new Error('Native authentication rejected'));
        else if (message.type === 'result') {
          const handler = pending.get(message.id); pending.delete(message.id);
          if (message.success) handler?.resolve(message.result);
          else handler?.reject(message.error);
        }
      };
    });
    await authenticated;
    const hass = {language:'en',user:{id:user},connection:socket,callWS:message => new Promise((resolve,reject) => {
      const id = ++sequence;
      // Store operation names only: never retain credentials or response payloads.
      window.nativeOperations.push(message.operation || 'vehicles');
      pending.set(id,{resolve,reject}); socket.send(JSON.stringify({id,...message}));
    })};
    const panel = document.createElement('ninebot-assistant-panel'); panel.hass = hass;
    document.body.append(panel); window.nativePanel = panel; window.nativeSocket = socket;
  }, {token:config.token,user:config.user});
  await page.getByRole('button',{name:'Review',exact:true}).click();
  await page.getByRole('region',{name:'Review the scope',exact:true}).waitFor();
  assert.equal(await page.evaluate(() => nativeOperations.includes('approve')), false);
  assert.equal(await page.locator('ninebot-assistant-panel').evaluate(el => el.shadowRoot.querySelectorAll('input').length), 0);
  await page.getByRole('button',{name:'Approve and synchronize',exact:true}).click();
  await page.getByRole('heading',{name:'Queued',exact:true}).waitFor();
  process.stdout.write('approved\n');
  assert.equal((await lines.next()).value, 'continue');
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await page.getByRole('heading',{name:'Completed',exact:true}).waitFor();
  operations.push(...await page.evaluate(() => nativeOperations));
  assert.deepEqual(operations,['vehicles','list','prepare','approve','list']);
  const credentialInDom = await page.locator('ninebot-assistant-panel').evaluate((el,token) => el.shadowRoot.innerHTML.includes(token), config.token);
  assert.equal(credentialInDom,false);
  assert.deepEqual(errors,[]); assert.deepEqual(external,[]);
  const output = process.env.BROWSER_TEST_OUTPUT;
  await mkdir(output,{recursive:true});
  await page.screenshot({path:join(output,'native-completed.png'),fullPage:true});
  await writeFile(join(output,'native-results.json'),JSON.stringify({nativeHAWebSocket:true,explicitHumanReview:true,completed:true,operations,credentialInDom,externalRequests:external.length,pageErrors:errors},null,2));
  await page.evaluate(() => {nativePanel.remove(); nativeSocket.close();});
  process.stdout.write('completed\n');
} finally { await browser.close(); }
