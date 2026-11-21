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
  const page = await browser.newPage({ viewport: { width: 1080, height: 900 }, colorScheme: 'dark' });
  const errors = [], external = [];
  page.on('pageerror', error => {
    errors.push(error.message);
    console.error('Synthetic replay browser error:', error.stack);
  });
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    external.push(route.request().url());
    return route.abort();
  });
  await page.goto(origin);
  await page.waitForFunction(() => fixtureCard.browser.page?.rides.length === 2 &&
    !fixtureCard.busy && fixtureCard.routeMap.renderer?.map.getZoom() > 1);
  assert.equal(await page.locator('details.filters').getAttribute('open'), null);
  await page.getByRole('button', { name: /加入比较 · Synthetic B/ }).click();
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.tracks.tracks.size === 2);
  assert.equal(await page.evaluate(() => fixtureCard.selection.selected.size), 2);
  const calls = await page.evaluate(() => fixtureRequests.filter(call => call.service === 'get_trip_detail'));
  assert.deepEqual(calls.map(call => call.service_data.device_id), ['synthetic-a', 'synthetic-b']);
  assert.ok(calls.every(call => call.service_data.local_only === true && call.service_data.include_track === true));
  assert.equal(await page.locator('.leaflet-container').count(), 1);

  // The map and its backing renderer remain mounted while focus changes.
  await page.evaluate(() => {
    const renderer = fixtureCard.routeMap.renderer;
    renderer.map.panBy([80, 20], { animate: false });
    window.savedView = [renderer.map.getCenter().lat, renderer.map.getCenter().lng, renderer.map.getZoom()];
    window.savedRenderer = renderer;
    window.savedLayers = renderer.routes.getLayers();
  });
  await page.locator('.compare-list button').filter({ hasText: 'Synthetic A' }).click();
  assert.deepEqual(await page.evaluate(() => {
    const map = fixtureCard.routeMap.renderer.map;
    return [map.getCenter().lat, map.getCenter().lng, map.getZoom()];
  }), await page.evaluate(() => savedView));
  assert.equal(await page.evaluate(() => {
    const renderer = fixtureCard.routeMap.renderer;
    return renderer === savedRenderer && renderer.routes.getLayers().every((layer, index) => layer === savedLayers[index]);
  }), true);

  const requests = await page.evaluate(() => fixtureRequests.length);
  const slider = page.getByRole('slider');
  await slider.focus();
  await slider.press('Home');
  for (let index = 0; index < 15; index++) await slider.press('ArrowRight');
  assert.equal(await page.evaluate(() => fixtureCard.index), 15);
  assert.ok((await slider.getAttribute('aria-valuetext')).endsWith('17'), 'Sequence skips the missing source rows');
  const actual = await page.evaluate(() => fixtureCard.routeMap.renderer.focus.getLatLng().lat);
  assert.ok(Math.abs(actual - 30.0017) < 1e-8);
  await page.getByRole('button', { name: '按点播放', exact: true }).click();
  await page.waitForFunction(() => fixtureCard.playback !== null && fixtureCard.index > 15);
  await page.getByRole('button', { name: '暂停播放', exact: true }).click();
  await page.getByRole('button', { name: '重新播放', exact: true }).click();
  assert.equal(await page.evaluate(() => fixtureCard.index), 0);
  assert.equal(await page.evaluate(() => fixtureRequests.length), requests);

  const output = process.env.BROWSER_TEST_OUTPUT;
  if (output) {
    await mkdir(output, { recursive: true });
    await page.screenshot({ path: join(output, 'replay-desktop-dark.png'), fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const mapBox = await page.locator('.map-frame').boundingBox(), listBox = await page.locator('.library').boundingBox();
  assert.ok(listBox.y >= mapBox.y + mapBox.height);
  assert.ok((await page.getByRole('button', { name: '按点播放', exact: true }).boundingBox()).height >= 44);
  assert.deepEqual(await page.locator('.metrics dt').allTextContents(), ['里程', '时长', '能耗']);
  if (output) await page.screenshot({ path: join(output, 'replay-mobile-light.png'), fullPage: true });
  await page.setViewportSize({ width: 1080, height: 900 });

  await page.locator('.ride .choose').last().click();
  assert.equal(await page.evaluate(() => fixtureCard.routeMap.renderer === savedRenderer), true);
  assert.equal(await page.evaluate(() => fixtureCard.selection.selected.size), 2);
  await page.locator('.ride .choose').last().press('ArrowUp');
  assert.equal(await page.evaluate(() =>
    fixtureCard.shadowRoot.activeElement === fixtureCard.shadowRoot.querySelector('.ride .choose')), true);
  assert.equal(await page.evaluate(() => fixtureRequests.length), requests);

  await page.evaluate(async () => {
    const { NinebotTripCard } = await import('/ninebot-static/ninebot-trip-card.js');
    window.fixtureEditor = NinebotTripCard.getConfigElement();
    fixtureEditor.hass = fixtureHass;
    fixtureEditor.setConfig(fixtureConfig);
    fixtureEditor.addEventListener('config-changed', event => { window.editedConfig = event.detail.config; });
    document.body.append(fixtureEditor);
  });
  await page.locator('ninebot-trip-card-editor').getByLabel('标题', { exact: true }).fill('Synthetic replay');
  await page.locator('ninebot-trip-card-editor').getByLabel('标题', { exact: true }).press('Tab');
  await page.waitForFunction(() => editedConfig?.title === 'Synthetic replay');
  assert.equal(await page.evaluate(() => editedConfig.vehicles.length), 2);
  assert.equal(await page.evaluate(() => editedConfig.statistics_entities[0]), 'ninebot:synthetic_day_distance_km');
  await page.evaluate(() => fixtureEditor.remove());

  await page.getByRole('button', { name: '按点播放', exact: true }).click();
  const before = await page.evaluate(() => ({
    requests: fixtureRequests.length, key: fixtureCard.selection.primary, playback: fixtureCard.playback,
  }));
  await page.evaluate(() => {
    for (const callback of fixtureFeeds) callback({ schema_version: 1, type: 'changed',
      vehicles: ['synthetic-a', 'synthetic-b'].map(device_id => ({ device_id, selection_version: 'a'.repeat(32) + ':1' })) });
  });
  const latest = page.getByRole('button', { name: '本地记录已更新，读取最新内容' });
  await latest.waitFor();
  assert.equal(await page.evaluate(() => fixtureRequests.length), before.requests);
  assert.equal(await page.evaluate(() => fixtureCard.playback), before.playback);
  await latest.click();
  await page.waitForFunction(() => !fixtureCard.busy && !fixtureCard.pendingUpdates);
  assert.equal(await page.evaluate(() => fixtureCard.selection.primary), before.key);
  assert.equal(await page.evaluate(() => fixtureCard.routeMap.renderer === savedRenderer), true);
  assert.equal(await page.evaluate(() => fixtureCard.playback), null);

  await page.evaluate(() => fixtureHass.connection.fire('disconnected'));
  await page.waitForFunction(() => !fixtureCard.routeMap.renderer && fixtureCard.tracks.tracks.size === 0);
  assert.equal(await page.locator('.leaflet-container').count(), 0);
  await page.evaluate(() => fixtureHass.connection.fire('ready'));
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.routeMap.renderer);

  const emptyAfterLatePage = await page.evaluate(async () => {
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const original = fixtureHass.callWS;
    fixtureHass.callWS = async message => {
      if (message.service === 'get_recorded_trips') await held;
      return original(message);
    };
    const late = fixtureCard.browser.read();
    fixtureCard.historyUpdate({ type: 'retrying', reason: 'unloaded' });
    release();
    await late;
    fixtureHass.callWS = original;
    const empty = !fixtureCard.browser.page && !fixtureCard.tracks.tracks.size &&
      !fixtureCard.routeMap.renderer && !fixtureCard.busy;
    for (const callback of fixtureFeeds) callback({ schema_version: 1, type: 'baseline',
      vehicles: ['synthetic-a', 'synthetic-b'].map(device_id =>
        ({ device_id, selection_version: 'a'.repeat(32) + ':1' })) });
    return empty;
  });
  assert.equal(emptyAfterLatePage, true);
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.routeMap.renderer);

  await page.evaluate(async () => {
    fixtureSaved = false;
    fixtureCard.browser.reset();
    await fixtureCard.browser.read();
  });
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.selection.selected.size === 1);
  assert.equal(await page.getByRole('slider').count(), 0);
  assert.equal(await page.getByRole('button', { name: '获取云端详情', exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => fixtureRequests.some(call =>
    call.service === 'get_trip_detail' && !call.service_data.local_only)), false);

  // Native capability detection falls through to the one dashboard map.
  await page.goto(`${origin}/?native=1`);
  await page.waitForFunction(() => fixtureCard.routeMap.renderer?.map.tagName === 'HA-MAP' &&
    !fixtureCard.busy && fixtureCard.routeMap.renderer.map.paths.length);
  const native = page.locator('ha-map');
  assert.equal(await native.count(), 1);
  assert.equal(await page.locator('.leaflet-container').count(), 0);
  const paths = await native.evaluate(map => map.paths.map(path => path.points.map(point => point.point[0])));
  assert.equal(paths.length, 2);
  assert.equal(paths[0].length, 15);
  assert.equal(paths[1].length, 43);
  await page.evaluate(() => {
    const map = fixtureCard.routeMap.renderer.map;
    window.savedNativeMap = map;
    window.savedPaths = map.paths;
  });
  await page.getByRole('slider').press('ArrowRight');
  assert.equal(await native.evaluate(map => map.paths === savedPaths), true);
  assert.equal(await native.evaluate(map => map.editableLocations[0].location[0]), 30.0001);
  await page.locator('.ride .choose').last().click();
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.activeTrack?.label === 'Synthetic B');
  assert.equal(await native.evaluate(map => map === savedNativeMap), true);
  await page.evaluate(() => {
    window.fixtureDeny = true;
    fixtureCard.hass = { ...fixtureHass, user: { id: 'another-synthetic-reader' } };
  });
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.browser.error === 'denied');
  assert.equal(await native.count(), 0);
  assert.equal(await page.evaluate(() => savedNativeMap.paths.length + savedNativeMap.editableLocations.length), 0);
  assert.equal(await page.evaluate(() => fixtureCard.tracks.tracks.size), 0);
  await page.evaluate(() => fixtureCard.remove());
  assert.equal(await page.evaluate(() => fixtureFeeds.size), 0);
  assert.equal(await page.evaluate(() => [...fixtureConnectionListeners.values()].some(set => set.size)), false);

  for (const nativeMode of [true, false]) {
    await page.goto(`${origin}/?endpoints=1${nativeMode ? '&native=1' : ''}`);
    await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.routeMap.renderer &&
      fixtureCard.activeTrack?.kind === 'server_simplified');
    assert.equal(await page.getByRole('slider').count(), 0);
    assert.match(await page.evaluate(() => fixtureCard.view.ui.quality.textContent), /仅显示起点和终点.*180 天/);
    if (nativeMode) {
      assert.deepEqual(await native.evaluate(map => map.paths.map(path => ({
        name: path.name, points: path.points.length, time: path.points[0].timestamp.toISOString(),
      }))), [
        { name: '起点', points: 1, time: '2026-09-03T08:00:00.000Z' },
        { name: '终点', points: 1, time: '2026-09-03T08:10:00.000Z' },
      ]);
    } else {
      assert.deepEqual(await page.locator('.leaflet-tooltip').allTextContents(), ['起点', '终点']);
      assert.equal(await page.evaluate(() => fixtureCard.routeMap.renderer.routes.getLayers()
        .every(layer => !layer.getLatLngs)), true);
    }
    assert.equal(await page.evaluate(() => fixtureRequests.filter(call =>
      call.service === 'get_trip_detail' && call.service_data.local_only).length), 1);
  }
  assert.equal(external.length, 0);
  assert.deepEqual(errors, []);
  console.log('Synthetic replay passed: one map, local reads, sample gaps, stable viewport, endpoint-only tracks and scope cleanup.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
