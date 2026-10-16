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
  await page.waitForFunction(() => fixtureCard.browser.page?.rides.length === 2 &&
    !fixtureCard.tracks.busy && fixtureCard.tracks.tracks.size === 1 && fixtureCard.routeMap.map?.getZoom() > 1);
  assert.equal(await page.getByText('已枚举月份: 2', { exact: false }).count(), 1);
  await page.getByRole('checkbox', { name: /加入比较 · Synthetic B/ }).check();
  await page.waitForFunction(() => !fixtureCard.tracks.busy && fixtureCard.tracks.tracks.size === 2);
  assert.equal(await page.evaluate(() => fixtureCard.selection.selected.size), 2);
  const calls = await page.evaluate(() => fixtureRequests.filter(call => call.service === 'get_trip_detail'));
  assert.deepEqual(calls.map(call => call.service_data.device_id), ['synthetic-a', 'synthetic-b']);
  assert.ok(calls.every(call => call.service_data.local_only === true && call.service_data.include_track === true));
  assert.equal(external.length, 0); assert.deepEqual(errors, []);

  await page.evaluate(() => {
    const map = fixtureCard.routeMap;
    map.map.panBy([80, 20], { animate: false });
    window.savedView = [map.map.getCenter().lat, map.map.getCenter().lng, map.map.getZoom()];
    window.savedLayer = [...map.groups.values()][0].layer;
  });
  await page.locator('.chips button').filter({ hasText: 'Synthetic A' }).click();
  await page.waitForFunction(() => fixtureCard.selection.selected.get(fixtureCard.selection.primary)?.device_id === 'synthetic-a');
  const currentView = await page.evaluate(() => {
    const map = fixtureCard.routeMap;
    return [map.map.getCenter().lat, map.map.getCenter().lng, map.map.getZoom()];
  });
  assert.deepEqual(currentView, await page.evaluate(() => savedView));
  assert.equal(await page.evaluate(() => [...fixtureCard.routeMap.groups.values()][0].layer === savedLayer), true);

  const before = await page.evaluate(() => fixtureRequests.length);
  const slider = page.getByRole('slider', { name: '源点序号 · Synthetic A', exact: true });
  await slider.focus(); await slider.press('Home');
  for (let i = 0; i < 10; i++) await slider.press('ArrowRight');
  await page.waitForFunction(() => fixtureCard.selection.focus?.sequence === 10 && fixtureCard.routeMap.focus !== null);
  assert.ok(Math.abs(await page.evaluate(() => fixtureCard.routeMap.focus.getLatLng().lat) - 30.001) < 1e-8);
  const curve = page.locator('.primary svg').first(), box = await curve.boundingBox();
  await page.mouse.move(box.x + box.width * .5, box.y + box.height * .5);
  await page.waitForFunction(() => fixtureCard.selection.focus.sequence > 20 && fixtureCard.selection.focus.sequence < 40);
  await page.mouse.move(box.x + box.width * .2, box.y + box.height * .5); await page.mouse.down();
  await page.mouse.move(box.x + box.width * .7, box.y + box.height * .5); await page.mouse.up();
  await page.waitForFunction(() => fixtureCard.selection.brush?.start < fixtureCard.selection.brush?.end && fixtureCard.routeMap.brushLayer !== null);
  await page.evaluate(() => {
    const [key, item] = [...fixtureCard.routeMap.groups.entries()].find(([, item]) => item.track.label === 'Synthetic B');
    item.lines[0].fire('click', { latlng: fixtureCard.routeMap.library.latLng(item.track.points[8].latitude, item.track.points[8].longitude) });
  });
  await page.waitForFunction(() => fixtureCard.selection.selected.get(fixtureCard.selection.primary)?.device_id === 'synthetic-b' && fixtureCard.selection.focus?.sequence === 8);
  await page.getByRole('button', { name: '按点播放', exact: true }).click();
  await page.waitForFunction(() => fixtureCard.playback !== null && fixtureCard.selection.focus.sequence > 8);
  await page.getByRole('button', { name: '暂停播放', exact: true }).click();
  assert.equal(await page.evaluate(() => fixtureRequests.length), before);

  const output = process.env.BROWSER_TEST_OUTPUT;
  if (output) { await mkdir(output, { recursive: true }); await page.screenshot({ path: join(output, 'workbench-desktop.png'), fullPage: true }); }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const mainBox = await page.locator('.main').boundingBox(), listBox = await page.locator('.rides').boundingBox();
  assert.ok(listBox.y >= mainBox.y + mainBox.height - 2);
  const detailBox = await page.locator('.ride-detail').boundingBox();
  assert.ok(detailBox.y >= listBox.y + listBox.height - 2);
  if (output) await page.screenshot({ path: join(output, 'workbench-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1080, height: 900 });
  const localBeforeSwitch = await page.evaluate(() => fixtureRequests.length);
  await page.locator('.ride button').first().click();
  await page.waitForFunction(() => !fixtureCard.tracks.busy && fixtureCard.selection.selected.size === 1);
  await page.locator('.ride button').last().click();
  await page.waitForFunction(() => !fixtureCard.tracks.busy && fixtureCard.selection.selected.get(fixtureCard.selection.primary)?.device_id === 'synthetic-b');
  await page.locator('.ride button').last().press('ArrowUp');
  await page.waitForFunction(() => fixtureCard.selection.selected.get(fixtureCard.selection.primary)?.device_id === 'synthetic-a');
  assert.equal(await page.evaluate(() => fixtureRequests.length), localBeforeSwitch);
  await page.getByRole('button', { name: '开启地图底图', exact: true }).click();
  await page.waitForFunction(() => fixtureCard.routeMap.tiles !== null);
  for (let i = 0; external.length === 0 && i < 20; i++) await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(external.length > 0 && external.every(url => url.startsWith('https://tile.openstreetmap.org/')));
  await page.getByRole('button', { name: '关闭地图底图', exact: true }).click();

  await page.evaluate(async () => {
    const { NinebotTripCard } = await import('/ninebot-static/ninebot-trip-card.js');
    window.fixtureEditor = NinebotTripCard.getConfigElement(); fixtureEditor.hass = fixtureHass;
    fixtureEditor.setConfig(fixtureConfig);
    fixtureEditor.addEventListener('config-changed', event => { window.editedConfig = event.detail.config; });
    document.body.append(fixtureEditor);
  });
  await page.locator('ninebot-trip-card-editor').getByLabel('标题', { exact: true }).fill('Synthetic edited workbench');
  await page.locator('ninebot-trip-card-editor').getByLabel('标题', { exact: true }).press('Tab');
  await page.waitForFunction(() => editedConfig?.title === 'Synthetic edited workbench');
  assert.equal(await page.evaluate(() => editedConfig.vehicles.length), 2);
  assert.equal(await page.evaluate(() => editedConfig.statistics_entities[0]), 'ninebot:synthetic_day_distance_km');
  if (output) await page.screenshot({ path: join(output, 'workbench-editor.png'), fullPage: true });
  await page.evaluate(() => fixtureEditor.remove());

  await page.evaluate(async () => { fixtureSaved = false; fixtureCard.browser.reset(); await fixtureCard.browser.read(); });
  await page.waitForFunction(() => !fixtureCard.browser.busy && fixtureCard.selection.selected.size === 1);
  assert.equal(await page.getByRole('button', { name: '显示已保存轨迹', exact: true }).isDisabled(), true);
  const missingCalls = await page.evaluate(() => fixtureRequests.length);
  await page.waitForTimeout(100); assert.equal(await page.evaluate(() => fixtureRequests.length), missingCalls);
  await page.getByRole('button', { name: '获取云端详情', exact: true }).click();
  await page.waitForFunction(() => !fixtureCard.browser.busy && fixtureCard.browser.details.size === 1);
  assert.equal(await page.getByRole('button', { name: '显示已保存轨迹', exact: true }).isDisabled(), false);
  await page.getByRole('button', { name: '显示已保存轨迹', exact: true }).click();
  await page.waitForFunction(() => fixtureCard.routeMap.map !== null && !fixtureCard.tracks.busy);
  await page.evaluate(() => {
    window.fixtureDeny = true;
    fixtureCard.hass = { ...fixtureHass, user: { id: 'another-synthetic-reader' } };
  });
  await page.waitForFunction(() => fixtureCard.selection.selected.size === 0 && fixtureCard.tracks.tracks.size === 0 && fixtureCard.routeMap.map === null && fixtureCard.playback === null);
  assert.deepEqual(errors, []);
  console.log('Synthetic workbench passed: linked selection/map/curves/brush, stable layers/viewport, bounded local reads, desktop/mobile/editor, explicit cloud detail and user privacy cleanup.');
} finally {
  await browser.close(); await new Promise(resolve => server.close(resolve));
}
