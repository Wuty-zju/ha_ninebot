/** Isolated synthetic browser smoke: no existing user browser or HA instance. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { serveReplay } from './replay-server.mjs';
import { checkStandaloneReplay } from './replay-example.mjs';
const require = createRequire(import.meta.url);
const { chromium } = require(join(process.env.BROWSER_TEST_DEPS, 'node_modules/playwright'));
const { server, origin } = await serveReplay();
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
  const statistics = page.getByRole('table', { name: '已保存行程统计' });
  const summaryValues = selector => statistics.locator(selector).allTextContents();
  assert.deepEqual(await summaryValues('.metric-label'), ['里程', '时长', '能耗', '每公里能耗', '平均速度']);
  assert.deepEqual(await summaryValues('td.selected-value'),
    ['2.4 km', '20分钟', '180 Wh', '75.0 Wh/km', '7.2 km/h']);
  const routePin = page.getByRole('button', { name: /1 · Synthetic A/ }).and(page.locator('.ninebot-route-pin'));
  await routePin.press('Enter');
  assert.equal(await page.evaluate(() => fixtureCard.activeTrack.label), 'Synthetic A');
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
  assert.equal(await statistics.isVisible(), true);
  if (output) await page.screenshot({ path: join(output, 'replay-mobile-light.png'), fullPage: true });
  await page.setViewportSize({ width: 320, height: 720 });
  await page.emulateMedia({ forcedColors: 'active' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const phoneTable = await statistics.boundingBox();
  assert.ok(phoneTable.x >= 0 && phoneTable.x + phoneTable.width <= 320);
  await page.emulateMedia({ forcedColors: 'none' });
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
    const { routes, replay } = fixtureCard.selection.snapshot(fixtureCard.tracks.tracks);
    const empty = !fixtureCard.browser.page && !fixtureCard.tracks.tracks.size &&
      !fixtureCard.routeMap.renderer && !fixtureCard.busy && !routes.length && !replay.point;
    for (const callback of fixtureFeeds) callback({ schema_version: 1, type: 'baseline',
      vehicles: ['synthetic-a', 'synthetic-b'].map(device_id =>
        ({ device_id, selection_version: 'a'.repeat(32) + ':1' })) });
    return empty;
  });
  assert.equal(emptyAfterLatePage, true);
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.routeMap.renderer);

  // Date filters browse the library independently of the live authorized comparison.
  const retained = await page.evaluate(async () => {
    const key = fixtureCard.selection.primary, track = fixtureCard.activeTrack;
    const before = fixtureRequests.filter(call => call.service === 'get_trip_detail').length;
    fixtureCard.browser.dates('2026-08-01', '2026-08-31');
    fixtureCard.historyUpdate({ type: 'baseline', vehicles: ['synthetic-a', 'synthetic-b'].map(device_id =>
      ({ device_id, selection_version: 'a'.repeat(32) + ':1' })) });
    const editing = fixtureCard.browser.awaitApply && !fixtureCard.browser.busy;
    await fixtureCard.refreshLocal();
    return editing && fixtureCard.selection.primary === key && fixtureCard.activeTrack === track &&
      fixtureRequests.filter(call => call.service === 'get_trip_detail').length === before;
  });
  assert.equal(retained, true);
  await page.locator('.ride .overlay').first().click();
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.activeRide.query_month === '202608');
  assert.deepEqual(await page.evaluate(() =>
    [...fixtureCard.selection.selected.values()].map(ride => ride.query_month)), ['202609', '202608']);
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

  // Two vehicles share the upstream ride ID across buffered pages and archive months.
  await page.goto(origin + '/?paged&english&overlap');
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.routeMap.renderer && fixtureFeeds.size === 1);
  await page.getByRole('button', { name: 'Next page', exact: true }).click();
  await page.getByRole('button', { name: /Compare · Synthetic B/ }).click();
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.activeTrack?.label === 'Synthetic B');
  assert.deepEqual(await page.evaluate(() => fixtureCard.selection.snapshot(fixtureCard.tracks.tracks)
    .routes.map(({ ride }) => [ride.device_id, ride.ride_id, ride.query_month])), [
    ['synthetic-a', 'synthetic-ride', '202609'], ['synthetic-b', 'synthetic-ride', '202608'],
  ]);
  await page.getByRole('button', { name: 'Previous page', exact: true }).click();
  const crossMonth = page.getByRole('table', { name: 'Saved ride statistics' });
  const crossMonthValues = await crossMonth.locator('td').allTextContents();
  assert.deepEqual(crossMonthValues,
    ['2.4 km', '3.6 km', '15 min', '25 min', '120 Wh', '210 Wh', '50.0 Wh/km', '58.3 Wh/km', '9.6 km/h', '8.6 km/h']);
  const crossMonthSlider = page.getByRole('slider', { name: /2 · Synthetic B/ });
  for (let step = 0; step < 15; step++) await crossMonthSlider.press('ArrowRight');
  assert.equal(await crossMonthSlider.getAttribute('aria-valuetext'), 'Point 16 of 58 · Source point sequence 17');
  const crossMonthLatitude = await page.evaluate(() => fixtureCard.routeMap.renderer.focus.getLatLng().lat);
  assert.ok(Math.abs(crossMonthLatitude - 30.0037) < 1e-8);
  assert.deepEqual(await crossMonth.locator('td').allTextContents(), crossMonthValues);
  assert.deepEqual(await page.evaluate(() => fixtureRequests.filter(call => call.service).map(call => call.service)),
    ['get_recorded_trips', 'get_recorded_trips', 'get_trip_detail', 'get_trip_detail']);

  await page.getByRole('button', { name: 'Keep current ride', exact: true }).click();
  assert.equal(await crossMonthSlider.getAttribute('aria-valuetext'), 'Point 16 of 58 · Source point sequence 17');
  const afterRevocation = await page.evaluate(async () => {
    const { selection, tracks } = fixtureCard;
    const original = fixtureHass.callWS;
    let start, release;
    const started = new Promise(resolve => { start = resolve; });
    const held = new Promise(resolve => { release = resolve; });
    fixtureHass.callWS = async message => {
      const response = await original(message);
      start();
      await held;
      return response;
    };
    const projection = () => selection.snapshot(tracks.tracks);
    const readsBefore = fixtureRequests.length;
    try {
      fixtureCard.activeTrack.stale = true;
      const late = fixtureCard.readRoute(fixtureCard.activeRide, true);
      await started;
      const sourceSequence = projection().replay.point.sequence;
      for (const callback of fixtureFeeds) callback({ type: 'closed', reason: 'denied' });
      const cleared = projection();
      release();
      await late;
      return { sourceSequence, cleared, settled: projection(), reads: fixtureRequests.length - readsBefore,
        cachedTracks: tracks.tracks.size, page: fixtureCard.browser.page };
    } finally {
      release();
      fixtureHass.callWS = original;
    }
  });
  assert.equal(afterRevocation.sourceSequence, 17);
  assert.equal(afterRevocation.reads, 1);
  assert.equal(afterRevocation.cachedTracks, 0);
  assert.equal(afterRevocation.page, null);
  assert.deepEqual(afterRevocation.settled, afterRevocation.cleared);
  assert.equal(afterRevocation.settled.primaryKey, null);
  assert.deepEqual(afterRevocation.settled.routes, []);
  assert.deepEqual(afterRevocation.settled.replay, { index: 0, count: 0, point: null });
  assert.equal(afterRevocation.settled.statistics.current.distance.value, null);
  assert.equal(await crossMonth.count(), 0);
  assert.equal(await page.locator('.leaflet-container').count(), 0);

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
  assert.equal(await native.evaluate(map => map.scaleRuler), true);
  const historyReads = await page.evaluate(() => fixtureRequests.length);
  const follow = page.getByRole('button', { name: '跟随回放', exact: true });
  await follow.click();
  assert.equal(await follow.getAttribute('aria-pressed'), 'true');
  await page.getByRole('slider').press('ArrowRight');
  assert.deepEqual(await native.evaluate(map => [map.center, map.zoom]), [[30.0002, 120 + Math.sin(2 / 8) * .001], 14]);
  await page.getByRole('button', { name: '显示全部轨迹', exact: true }).click();
  assert.equal(await follow.getAttribute('aria-pressed'), 'false');
  assert.equal(await page.evaluate(() => fixtureRequests.length), historyReads);
  await page.locator('.ride .overlay').last().click();
  await page.waitForFunction(() => !fixtureCard.busy && fixtureCard.activeTrack?.label === 'Synthetic B');
  const beforeFocus = await page.evaluate(() => fixtureRequests.length);
  const pin = await native.evaluate(map => {
    const marker = map.editableLocations.find(item => item.activatable);
    map.dispatchEvent(new CustomEvent('editable-location-clicked', { detail: { id: marker.id } }));
    return marker.title;
  });
  assert.ok(pin.includes('Synthetic A'));
  assert.equal(await page.evaluate(() => fixtureCard.activeTrack?.label), 'Synthetic A');
  assert.equal(await native.evaluate(map => map === savedNativeMap), true);
  assert.equal(await page.evaluate(() => fixtureRequests.length), beforeFocus);
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
  // Real markers and the HA component contract must agree when two vehicles share a route.
  for (const nativeMode of [false, true]) {
    await page.goto(origin + '/?overlap&english' + (nativeMode ? '&native' : ''));
    await page.waitForFunction(() => fixtureCard.routeMap.renderer && !fixtureCard.busy);
    await page.locator('.overlay').last().click();
    await page.waitForFunction(() => fixtureCard.activeTrack?.label === 'Synthetic B' && !fixtureCard.busy);
    const beforePicking = await page.evaluate(() => fixtureRequests.length);
    const showPicker = async () => {
      if (nativeMode) {
        await native.evaluate(map => map.dispatchEvent(new CustomEvent('editable-location-clicked',
          { detail: { id: 'route-2' } })));
      } else await page.locator('.ninebot-route-pin').last().click();
    };
    if (nativeMode) {
      await showPicker();
    } else {
      // Replay the iPhone sequence where both touch events arrive and no click follows.
      await page.locator('.ninebot-route-pin').last().locator('span').first().evaluate(pin => {
        const touch = new Touch({ target: pin, identifier: 1, clientX: 80, clientY: 60 });
        for (const type of ['touchstart', 'touchend']) {
          pin.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true,
            touches: type === 'touchstart' ? [touch] : [], changedTouches: [touch] }));
        }
      });
    }
    const picker = page.getByRole('dialog', { name: 'Choose a ride at this spot' });
    if (!nativeMode) {
      const stillOpen = await picker.getByRole('button', { name: /1 · Synthetic A/ }).evaluate(option => {
        function send(type, y) {
          const finger = new Touch({ target: option, identifier: 1, clientX: 60, clientY: y });
          option.dispatchEvent(new TouchEvent(type, {
            bubbles: true, cancelable: true, changedTouches: [finger],
            touches: type === 'touchend' || type === 'touchcancel' ? [] : [finger],
          }));
        }
        send('touchstart', 80); send('touchmove', 43); send('touchend', 43);
        send('touchstart', 80); send('touchcancel', 80); send('touchend', 80);
        return option.closest('dialog').open && fixtureCard.activeTrack.label === 'Synthetic B';
      });
      assert.ok(stillOpen, 'Scrolling or cancelling leaves the choice open');
    }
    await picker.getByRole('button', { name: /1 · Synthetic A/ }).click();
    await page.waitForFunction(() => fixtureCard.shadowRoot.activeElement?.className === 'focus-track');
    const comparison = page.getByRole('table', { name: 'Saved ride statistics' });
    const currentValues = comparison.locator('.current-value');
    const selectedValues = comparison.locator('td.selected-value');
    const currentSummary = ['1.2 km', '10 min', '90 Wh', '75.0 Wh/km', '7.2 km/h'];
    const selectedSummary = ['3.6 km', '25 min', '210 Wh', '58.3 Wh/km', '8.6 km/h'];
    assert.deepEqual(await currentValues.allTextContents(), currentSummary);
    assert.deepEqual(await selectedValues.allTextContents(), selectedSummary);
    const nativeSlider = page.getByRole('slider', { name: /1 · Synthetic A/ });
    await nativeSlider.press('Home');
    for (let step = 0; step < 15; step++) await nativeSlider.press('ArrowRight');
    assert.equal(await nativeSlider.getAttribute('aria-valuetext'), 'Point 16 of 58 · Source point sequence 17');
    assert.deepEqual(await currentValues.allTextContents(), currentSummary);
    assert.deepEqual(await selectedValues.allTextContents(), selectedSummary);
    const remove = page.getByRole('button', { name: /Remove this route · 2 · Synthetic B/ })
      .and(page.locator('.remove-track'));
    await remove.press('Enter');
    assert.equal(await page.locator('.focus-track').count(), 1);
    assert.equal(await page.evaluate(() => fixtureCard.shadowRoot.activeElement?.className), 'focus-track');
    assert.equal(await page.evaluate(() => fixtureCard.index), 15);
    assert.equal(await comparison.getByRole('columnheader', { name: /All selected rides/ }).count(), 0);
    // Previously read rides can rejoin the comparison from the library.
    await page.getByRole('button', { name: /Compare · Synthetic B/ }).press('Enter');
    await page.locator('.focus-track').first().press('Enter');
    await page.getByRole('button', { name: 'Play points', exact: true }).click();
    await page.waitForFunction(() => fixtureCard.index > 0);
    const activeTimer = await page.evaluate(() => fixtureCard.playback);
    await page.getByRole('button', { name: 'Keep current ride', exact: true }).press('Enter');
    assert.equal(await page.evaluate(() => fixtureCard.playback), activeTimer);
    assert.equal(await page.evaluate(() => fixtureCard.shadowRoot.activeElement?.className), 'focus-track');
    assert.deepEqual(await currentValues.allTextContents(), currentSummary);
    assert.equal(await page.locator('.focus-track').count(), 1);
    assert.equal(await page.locator(nativeMode ? 'ha-map' : '.leaflet-container').count(), 1);
    assert.equal(await page.evaluate(() => fixtureRequests.length), beforePicking);
    await page.getByRole('button', { name: /Compare · Synthetic B/ }).press('Enter');
    await showPicker();
    await page.evaluate(() => fixtureHass.connection.fire('disconnected'));
    assert.equal(await picker.count(), 0);
    assert.equal(await page.locator('.pin-choices button').count(), 0);
    assert.equal(await comparison.count(), 0);
  }
  await page.goto(origin + '/?overlap&english&incomplete');
  await page.waitForFunction(() => fixtureCard.routeMap.renderer && !fixtureCard.busy);
  await page.getByRole('button', { name: /Compare · Synthetic B/ }).click();
  await page.waitForFunction(() => fixtureCard.activeTrack?.label === 'Synthetic B' && !fixtureCard.busy);
  const incomplete = page.getByRole('table', { name: 'Saved ride statistics' });
  const incompleteCurrent = await incomplete.locator('.current-value').allTextContents();
  assert.deepEqual(incompleteCurrent, ['2.4 km', '15 min', '—', '—', '—']);
  assert.deepEqual(await incomplete.locator('td.selected-value').allTextContents(),
    ['3.6 km', '25 min', '—1/2 rides usable', '—1/2 rides usable', '—1/2 rides usable']);
  assert.equal(await incomplete.getByRole('rowheader', { name: 'Average speed' }).count(), 1);
  await page.setViewportSize({ width: 320, height: 720 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (output) await page.screenshot({ path: join(output, 'replay-mobile-partial.png'), fullPage: true });
  assert.equal(external.length, 0);
  assert.deepEqual(errors, []);
  console.log('Synthetic replay passed: one map, local reads, sample gaps, stable viewport, endpoint-only tracks and scope cleanup.');
  await checkStandaloneReplay(browser, origin, output);
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
