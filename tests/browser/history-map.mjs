/** Isolated synthetic browser smoke: no existing user browser or HA instance. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, sep, join } from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(join(process.env.BROWSER_TEST_DEPS, 'node_modules/playwright'));
const root = fileURLToPath(new URL('../../', import.meta.url));
const assets = resolve(root, 'custom_components/ninebot/frontend');
const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://localhost');
    let path;
    if (url.pathname === '/') path = resolve(root, 'tests/frontend/map-fixture.html');
    else if (url.pathname.startsWith('/ninebot-static/')) {
      path = resolve(assets, url.pathname.slice('/ninebot-static/'.length));
      if (!path.startsWith(assets + sep)) throw new Error('Not an asset');
    } else throw new Error('Not a fixture');
    const bytes = await readFile(path);
    const type = path.endsWith('.js') ? 'application/javascript' : path.endsWith('.css') ? 'text/css' :
      path.endsWith('.png') ? 'image/png' : 'text/html';
    response.writeHead(200, { 'Content-Type': type }); response.end(bytes);
  } catch { response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1080, height: 900 } });
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  // Even optional tiles cannot leave the isolated test. All points are fake.
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin) {
      external.push(route.request().url()); return route.abort();
    }
    return route.continue();
  });
  await page.goto(origin);
  await page.waitForFunction(() => window.fixtureCard?.browser.page?.rides.length === 2);
  for (const name of ['Synthetic A', 'Synthetic B']) {
    await page.locator('ninebot-trip-card summary').filter({ hasText: name }).click();
    await page.getByRole('button', { name: '显示已保存轨迹', exact: true, disabled: false }).click();
    await page.waitForFunction(() => window.fixtureCard.tracks.busy === false);
  }
  await page.waitForFunction(() => window.fixtureCard.routeMap.map?.getZoom() > 1);
  assert.equal(await page.locator('ninebot-trip-card .leaflet-container canvas').count(), 1);
  assert.equal(await page.evaluate(() => fixtureCard.tracks.tracks.size), 2);
  const calls = await page.evaluate(() => fixtureRequests.filter(call => call.service === 'get_trip_detail'));
  assert.deepEqual(calls.map(call => call.service_data.device_id), ['synthetic-a', 'synthetic-b']);
  assert.ok(calls.every(call => call.service_data.local_only === true && call.service_data.include_track === true));
  assert.equal(external.length, 0);
  assert.deepEqual(errors, []);
  const callsBeforePlayback = await page.evaluate(() => fixtureRequests.length);
  const slider = page.getByRole('slider', { name: '源点序号 · Synthetic A', exact: true });
  await slider.focus(); await slider.press('Home');
  for (let i = 0; i < 10; i++) await slider.press('ArrowRight');
  await page.waitForFunction(() => fixtureCard.routeMap.focus !== null);
  assert.ok(Math.abs(await page.evaluate(() => fixtureCard.routeMap.focus.getLatLng().lat) - 30.001) < 1e-8);
  await page.getByRole('button', { name: '按点播放', exact: true }).first().click();
  await page.waitForFunction(() => fixtureCard.playback !== null && Number(fixtureCard.shadowRoot.querySelector('input[type=range]').value) > 10);
  await page.getByRole('button', { name: '暂停播放', exact: true }).click();
  assert.equal(await page.evaluate(() => fixtureRequests.length), callsBeforePlayback);
  const output = process.env.BROWSER_TEST_OUTPUT;
  if (output) {
    await mkdir(output, { recursive: true });
    await page.screenshot({ path: join(output, 'synthetic-multi-vehicle-map.png'), fullPage: true });
  }
  await page.getByRole('button', { name: '开启地图底图', exact: true }).click();
  await page.waitForFunction(() => window.fixtureCard.routeMap.tiles !== null);
  // Requests may be aborted without responses. Observe the test router instead.
  for (let i = 0; external.length === 0 && i < 20; i++) await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(external.length > 0); assert.ok(external.every(url => url.startsWith('https://tile.openstreetmap.org/')));
  await page.getByRole('button', { name: '关闭地图底图', exact: true }).click();
  await page.waitForFunction(() => window.fixtureCard.routeMap.tiles === null);
  await page.evaluate(() => {
    fixtureCard.hass = { ...fixtureHass, user: { id: 'another-synthetic-reader' } };
  });
  await page.waitForFunction(() => fixtureCard.tracks.tracks.size === 0 && fixtureCard.routeMap.map === null && fixtureCard.playback === null);
  assert.deepEqual(errors, []);
  console.log('Synthetic browser smoke passed: two scoped routes, local assets, tile opt-in, user-change privacy cleanup.');
} finally {
  await browser.close(); await new Promise(resolve => server.close(resolve));
}
