/** Run against the standalone entry point, with the same archive as the card smoke test. */
import assert from 'node:assert/strict';
import { join } from 'node:path';

const currentA = ['1.2 km', '10 min', '90 Wh', '75.0 Wh/km', '7.2 km/h'];
const currentB = ['2.4 km', '15 min', '120 Wh', '50.0 Wh/km', '9.6 km/h'];
const both = ['3.6 km', '25 min', '210 Wh', '58.3 Wh/km', '8.6 km/h'];

async function watch(page, origin) {
  const errors = [], external = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => requests.push(request.url()));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) {
      external.push(url.href);
      return route.abort();
    }
    let instrumentation;
    if (url.pathname === '/replay/synthetic-archive.mjs') instrumentation = `
      const detail = syntheticDetail;
      window.detailReads = [];
      syntheticDetail = (ride, request, options) => {
        window.detailReads.push(request);
        return detail(ride, request, options);
      };
    `;
    if (url.pathname === '/ninebot-static/route-map.js') instrumentation = `
      const highlight = RouteMap.prototype.highlight;
      RouteMap.prototype.highlight = function (point, ...args) {
        window.previewMap = this;
        return highlight.call(this, point, ...args);
      };
    `;
    if (!instrumentation) return route.continue();
    const response = await route.fetch();
    return route.fulfill({ response, body: (await response.text()) + instrumentation });
  });
  return { errors, external, requests };
}

function controls(page) {
  return {
    ride: name => page.locator('#rides button').filter({ hasText: name }),
    compare: page.getByRole('button', { name: 'Compare both', exact: true }),
    timeline: page.getByRole('slider', { name: 'Saved points' }),
    current: () => page.locator('td.current').allTextContents(),
    selected: () => page.locator('td.selected').allTextContents(),
    choose: page.getByRole('dialog', { name: 'Choose a ride at this spot' }),
  };
}

export async function checkStandaloneReplay(browser, origin, output) {
  const desktop = await browser.newPage({ viewport: { width: 1180, height: 880 }, colorScheme: 'light' });
  try {
    const { external, requests, errors } = await watch(desktop, origin);
    const ui = controls(desktop);
    const url = origin + '/replay/?lang=en';
    await desktop.goto(url);
    await desktop.waitForFunction(() => previewMap?.renderer?.focus);
    assert.equal(await desktop.locator('ninebot-trip-card, ha-map').count(), 0);
    assert.equal(await desktop.locator('.leaflet-container').count(), 1);
    const reads = await desktop.evaluate(() => detailReads);
    assert.deepEqual(reads.map(({ device_id, ride_id, query_month }) => [device_id, ride_id, query_month]), [
      ['synthetic-a', 'synthetic-ride', '202609'], ['synthetic-b', 'synthetic-ride', '202608'],
    ]);
    assert.ok(reads.every(read => read.local_only && read.include_track && read.point_offset === 0 && read.max_points === 512));
    const requestCount = requests.length;
    await desktop.evaluate(() => { window.initialRenderer = previewMap.renderer; });
    assert.deepEqual(await ui.current(), currentA);
    await ui.compare.click();
    assert.equal(await ui.compare.getAttribute('aria-pressed'), 'true');
    assert.deepEqual(await ui.selected(), both);
    await ui.ride('Synthetic B').press('Enter');
    assert.equal(await ui.ride('Synthetic B').getAttribute('aria-pressed'), 'true');
    assert.deepEqual(await ui.current(), currentB);
    const paths = await desktop.evaluate(() => {
      window.initialLayers = previewMap.renderer.routes.getLayers();
      return initialLayers.filter(layer => layer.getLatLngs).map(layer => layer.getLatLngs().length);
    });
    assert.deepEqual(paths, [15, 43, 15, 43]);
    await ui.timeline.press('Home');
    for (let count = 0; count < 15; count++) await ui.timeline.press('ArrowRight');
    assert.match(await ui.timeline.getAttribute('aria-valuetext'), /^Point 16 of 58 · Source point sequence 17 · .+17$/);
    assert.match(await desktop.locator('#sample').innerText(), /Source point sequence 17 · Coordinates 30.00370,/);
    assert.equal(await desktop.evaluate(() => {
      const { lat, lng } = previewMap.renderer.focus.getLatLng();
      return previewMap.renderer === initialRenderer &&
        previewMap.renderer.routes.getLayers().every((layer, index) => layer === initialLayers[index]) &&
        Math.abs(lat - 30.0037) < 1e-8 && Math.abs(lng - (120.002 + Math.sin(17 / 8) * .001)) < 1e-8;
    }), true);
    assert.deepEqual(await ui.current(), currentB);
    assert.deepEqual(await ui.selected(), both);
    await desktop.getByRole('button', { name: 'Play points', exact: true }).click();
    await desktop.waitForFunction(() => Number(document.getElementById('timeline').value) > 15);
    await desktop.getByRole('button', { name: 'Pause points', exact: true }).click();
    await ui.timeline.press('End');
    await desktop.getByRole('button', { name: 'Play points', exact: true }).click();
    assert.equal(await ui.timeline.inputValue(), '0');
    await desktop.getByRole('button', { name: 'Pause points', exact: true }).click();
    await ui.timeline.press('ArrowRight');
    await ui.compare.press('Enter');
    assert.equal(await ui.timeline.inputValue(), '1');
    await ui.compare.press('Enter');
    assert.match(await ui.timeline.getAttribute('aria-valuetext'), /^Point 2 of 58 · Source point sequence 1 · .+1$/);

    await desktop.locator('.ninebot-route-pin').last().click();
    await ui.choose.getByRole('button', { name: /Synthetic A/ }).click();
    assert.equal(await ui.ride('Synthetic A').getAttribute('aria-pressed'), 'true');
    assert.equal(await ui.ride('Synthetic A').evaluate(node => node === document.activeElement), true);
    assert.equal(await ui.choose.count(), 0);
    assert.equal(await desktop.locator('.leaflet-container').count(), 1);
    assert.deepEqual(await ui.selected(), both);
    assert.deepEqual(await desktop.evaluate(() => detailReads), reads);
    assert.equal(requests.length, requestCount);
    for (let count = 0; count < 43; count++) await ui.timeline.press('ArrowRight');
    await ui.ride('Synthetic A').focus();
    if (output) await desktop.screenshot({ path: join(output, 'standalone-replay-desktop-light.png'), fullPage: true });

    for (const [scenario, expectedCurrent, expectedSelected] of [
      ['incomplete', ['2.4 km', '15 min', '—', '—', '—'],
        ['3.6 km', '25 min', '—1/2 rides usable', '—1/2 rides usable', '—1/2 rides usable']],
      ['zero', ['0 km', '0 sec', '0 Wh', '—', '—'],
        ['1.2 km', '10 min', '90 Wh', '—1/2 rides usable', '—1/2 rides usable']],
      ['endpoints', currentB, both],
    ]) {
      await desktop.goto(url + '&scenario=' + scenario);
      await ui.compare.click();
      await ui.ride('Synthetic B').click();
      assert.deepEqual(await ui.current(), expectedCurrent);
      assert.deepEqual(await ui.selected(), expectedSelected);
      if (scenario === 'endpoints') {
        assert.equal(await ui.timeline.count(), 0);
        assert.equal(await desktop.locator('#sample').innerText(), '');
        assert.match(await desktop.locator('#quality').innerText(), /Start and end only/);
        assert.equal(await desktop.evaluate(() => previewMap.point), null);
        await ui.ride('Synthetic A').click();
        assert.equal(await ui.timeline.inputValue(), '0');
      }
    }
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
  } finally {
    await desktop.close();
  }

  const mobile = await browser.newPage({ viewport: { width: 320, height: 720 }, colorScheme: 'dark',
    isMobile: true, hasTouch: true });
  try {
    const { external, requests, errors } = await watch(mobile, origin);
    const ui = controls(mobile);
    await mobile.goto(origin + '/replay/?lang=en');
    await mobile.waitForFunction(() => previewMap?.renderer?.focus);
    const requestCount = requests.length;
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await mobile.evaluate(() => {
      window.gestures = [];
      for (const type of ['touchstart', 'touchend', 'input']) {
        document.addEventListener(type, event => gestures.push([event.type, event.isTrusted, event.target.id]), true);
      }
    });
    await ui.compare.tap();
    await ui.ride('Synthetic B').tap();
    assert.deepEqual(await ui.current(), currentB);
    await mobile.locator('.ninebot-route-pin').last().tap();
    await ui.choose.getByRole('button', { name: /Synthetic A/ }).tap();
    assert.deepEqual(await ui.current(), currentA);
    await mobile.getByRole('button', { name: 'Play points', exact: true }).tap();
    await mobile.waitForFunction(() => Number(document.getElementById('timeline').value) > 0);
    await ui.timeline.scrollIntoViewIfNeeded();
    const { x, y, width, height } = await ui.timeline.boundingBox();
    const fraction = Number(await ui.timeline.inputValue()) / Number(await ui.timeline.getAttribute('max'));
    const start = x + 8 + (width - 16) * fraction;
    const end = x + width * .72;
    const cdp = await mobile.context().newCDPSession(mobile);
    const touch = (type, xpos) => cdp.send('Input.dispatchTouchEvent', {
      type, touchPoints: type === 'touchEnd' ? [] : [{ x: xpos, y: y + height / 2 }],
    });
    await touch('touchStart', start);
    for (let step = 1; step <= 8; step++) await touch('touchMove', start + (end - start) * step / 8);
    await touch('touchEnd');
    const state = await mobile.evaluate(() => {
      const index = Number(document.getElementById('timeline').value);
      const cursor = previewMap.renderer.focus.getLatLng();
      return { index, cursor: [cursor.lat, cursor.lng],
        expectedCursor: [previewMap.point.latitude, previewMap.point.longitude],
        source: previewMap.point.sequence, gestures, reads: detailReads.length };
    });
    assert.ok(state.gestures.some(([type, trusted, id]) => type === 'touchend' && trusted && id === 'compare'));
    assert.ok(state.gestures.some(([type, trusted, id]) => type === 'input' && trusted && id === 'timeline'));
    assert.ok(state.index > 16 && state.index < 57, JSON.stringify(state));
    assert.equal(state.source, state.index + 2);
    assert.deepEqual(state.cursor, state.expectedCursor);
    assert.match(await ui.timeline.getAttribute('aria-valuetext'), new RegExp(
      `^Point ${state.index + 1} of 58 · Source point sequence ${state.source} · .+${state.source}$`));
    assert.equal(await mobile.getByRole('button', { name: 'Play points', exact: true }).count(), 1);
    assert.deepEqual(await ui.current(), currentA);
    assert.deepEqual(await ui.selected(), both);
    assert.equal(state.reads, 2);
    assert.equal(requests.length, requestCount);
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    if (output) await mobile.screenshot({ path: join(output, 'standalone-replay-mobile-dark.png'), fullPage: true });
  } finally {
    await mobile.close();
  }
  console.log('Standalone replay passed: same-ID rides across months, trusted touch, saved-point seek and two in-memory detail reads.');
}
