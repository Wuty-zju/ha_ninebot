/** Mount the production panel with a synthetic Home Assistant connection. */
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { serveReplay } from './replay-server.mjs';

const currentA = ['1.2 km', '10 min', '90 Wh', '75.0 Wh/km', '7.2 km/h'];
const currentC = ['3.4 km', '10 min', '90 Wh', '26.5 Wh/km', '20.4 km/h'];

async function checkCleared(page) {
  assert.equal(await page.locator('#workspace').isVisible(), false);
  assert.equal(await page.locator('#rides button').count(), 0);
  assert.equal(await page.locator('.leaflet-container, .ninebot-route-pin').count(), 0);
  assert.equal(await page.locator('#timeline').inputValue(), '0');
  assert.equal(await page.locator('#sample').textContent(), '');
  assert.equal(await page.locator('#choices').textContent(), '');
  assert.equal(await page.locator('#active').textContent(), '');
  assert.deepEqual(await page.locator('td.current').allTextContents(), ['—', '—', '—', '—', '—']);
  assert.deepEqual(await page.locator('td.selected').allTextContents(), ['', '', '', '', '']);
}

// Runs in the existing synthetic browser fixture and exercises the panel's actual controls.
export async function checkArchivePaginationAndResume() {
  const { panel, requests, subscriptions } = fixture;
  const el = id => panel.shadowRoot.getElementById(id);
  const check = (condition, error) => { if (!condition) throw new Error(error); };
  const wait = async (condition, error) => {
    for (let remaining = 250; remaining > 0; remaining--) {
      if (condition()) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw new Error(error);
  };
  const reads = service => requests.map(item => item.message).filter(message => message.service === service);
  const lists = () => reads('get_recorded_trips');
  const details = () => reads('get_trip_detail');
  const browser = () => panel._scope.browser;
  const cleared = () => el('workspace').hidden && !el('viewport').querySelector('.leaflet-container') &&
    !el('sample').textContent && !el('rides').children.length && el('metrics').querySelector('.current').textContent === '—';
  const visibility = hidden => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: hidden });
    document.dispatchEvent(new Event('visibilitychange'));
  };
  const waitForPage = page => wait(() => el('page').textContent === String(page), `Could not open page ${page}`);
  await wait(() => el('account').options.length === 3, 'No discovered accounts');
  el('account').value = 'account-a';
  el('account').dispatchEvent(new Event('change'));
  await wait(() => subscriptions.length === 1, 'No initial scope');
  el('start-date').value = '2026-09-01';
  el('end-date').value = '2026-09-30';
  el('apply').click();
  await wait(() => subscriptions.length === 2, 'No dated scope');
  fixture.baseline();
  await waitForPage(1);
  await wait(() => el('sample').textContent, 'Initial route did not load');

  const ids = [];
  const record = () => {
    check(el('rides').children.length === 20, 'Page size changed');
    ids.push(...browser().page.rides.map(row => `${row.device_id}:${row.ride_id}`));
  };
  record();
  fixture.hold('synthetic-a', 'get_recorded_trips');
  el('next').click();
  await wait(() => lists().length === 3, 'No continuation arrived');
  el('next').click();
  check(lists().length === 3 && el('next').disabled, 'A second pagination action escaped the read gate');
  fixture.release('synthetic-a', 'get_recorded_trips');
  await waitForPage(2);
  record();
  el('next').click();
  await waitForPage(3);
  record();
  el('rides').children[1].click();
  await wait(() => !el('compare').disabled && el('sample').textContent, 'Selected route did not load');
  el('timeline').value = '2';
  el('timeline').dispatchEvent(new Event('input'));
  const sample = el('sample').textContent, active = browser(), listCount = lists().length, detailCount = details().length;
  visibility(true);
  window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
  check(cleared() && !subscriptions.at(-1).active, 'Background retained routes or the old permission feed');
  visibility(false);
  window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
  await wait(() => subscriptions.length === 3, 'Resume did not open a fresh permission feed');
  fixture.baseline(1);
  check(cleared() && lists().length === listCount && details().length === detailCount,
    'The retired feed restored private data');
  fixture.baseline();
  await waitForPage(3);
  await wait(() => el('sample').textContent === sample, 'Resume did not restore the selected saved point');
  check(el('account').value === 'account-a' && el('start-date').value === '2026-09-01' &&
    browser() === active && lists().length === listCount && details().length === detailCount + 1,
  'Resume reread saved pages or lost the current filters');

  for (let number = 4; number <= 7; number++) {
    el('next').click();
    await waitForPage(number);
    record();
  }
  const expected = [];
  for (let rank = 112; rank >= 0; rank--) {
    expected.push(`synthetic-a:ride-${999 - rank}`);
    if (rank <= 104 && rank % 4 === 0) expected.push(`synthetic-b:ride-${999 - rank}`);
  }
  check(ids.join() === expected.join() && lists().length === 8 && browser().pages.size === 5 &&
    el('next').disabled, 'Large fleet pagination omitted, repeated, reordered or reread rides');
  el('previous').click();
  el('next').click();
  check(el('page').textContent === '7' && lists().length === 8, 'Cached navigation reread an archive page');

  const { device_id: device } = browser().page.rides[0];
  fixture.hold(device);
  el('rides').firstElementChild.click();
  await wait(() => details().length === detailCount + 2, 'No pending saved route');
  visibility(true);
  fixture.lost.add(device);
  fixture.release(device);
  visibility(false);
  await wait(() => el('startup').textContent.includes('access was withdrawn'), 'Silent withdrawal went undetected');
  check(cleared() && subscriptions.length === 3 && detailCount + 2 === details().length && lists().length === 8 &&
    active.pages.size === 0, 'Silent withdrawal exposed a stale page or late geometry');
  return { rides: ids.length, localListReads: lists().length, cachedPages: 5 };
}

export async function checkArchiveReplay(browser, origin, output) {
  const page = await browser.newPage({ viewport: { width: 1220, height: 875 }, colorScheme: 'light' });
  const errors = [], external = [];
  try {
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) { external.push(url.href); return route.abort(); }
      return route.continue();
    });
    const account = page.getByLabel('Account', { exact: true });
    await page.goto(origin + '/archive-test', { timeout: 60000 });
    await account.selectOption('account-a');
    await page.waitForFunction(() => fixture.subscriptions.length === 1);
    assert.deepEqual(await page.evaluate(() => fixture.requests.map(({ message }) => message.type)),
      ['ninebot/history/vehicles', 'ninebot/history/subscribe']);
    await page.getByLabel('Start date', { exact: true }).fill('2026-08-01');
    await page.getByLabel('End date', { exact: true }).fill('2026-09-30');
    await page.getByRole('button', { name: 'Show rides' }).click();
    await page.waitForFunction(() => fixture.subscriptions.length === 2);
    await checkCleared(page);
    await page.evaluate(() => fixture.baseline());
    await page.locator('.leaflet-container').waitFor();
    assert.deepEqual(await page.locator('td.current').allTextContents(), currentA);
    const slider = page.getByRole('slider', { name: 'Saved points' });
    await slider.press('ArrowRight');
    assert.match(await page.locator('#sample').textContent(), /Coordinates 30.00010,/);
    assert.deepEqual(await page.locator('td.current').allTextContents(), currentA);
    if (output) await page.screenshot({ path: join(output, 'local-archive-panel.png'), fullPage: true });

    await page.evaluate(() => {
      window.oldScope = fixture.panel._scope;
      fixture.hold('synthetic-b');
    });
    assert.equal(await page.evaluate(() => oldScope.browser.pages.size), 1);
    await page.getByRole('button', { name: 'Compare another ride' }).click();
    await page.locator('#rides button').filter({ hasText: 'Synthetic B' }).click();
    await page.waitForFunction(() => fixture.requests.some(({ message }) =>
      message.service === 'get_trip_detail' && message.service_data.device_id === 'synthetic-b'));
    await account.selectOption('account-b');
    await checkCleared(page);
    const obsolete = await page.evaluate(() => ({
      pages: oldScope.browser.pages.size, streams: oldScope.browser.streams.length,
      page: oldScope.browser.page, active: fixture.subscriptions[1].active,
    }));
    assert.deepEqual(obsolete, { pages: 0, streams: 0, page: null, active: false });
    await page.waitForFunction(() => fixture.subscriptions.length === 3);
    await page.evaluate(() => fixture.baseline());
    await page.locator('.leaflet-container').waitFor();
    assert.match(await page.locator('#sample').textContent(), /Coordinates 35.00000,/);
    assert.deepEqual(await page.locator('td.current').allTextContents(), currentC);
    await page.evaluate(() => {
      fixture.release('synthetic-b');
      fixture.baseline(1);
    });
    assert.equal(await page.locator('#rides button').filter({ hasText: 'Synthetic B' }).count(), 0);
    assert.match(await page.locator('#sample').textContent(), /Coordinates 35.00000,/);
    assert.deepEqual(await page.locator('td.current').allTextContents(), currentC);

    await page.evaluate(() => { fixture.hold('synthetic-c'); });
    await page.getByRole('button', { name: 'Compare another ride' }).click();
    await page.locator('#rides button').last().click();
    await page.waitForFunction(() => fixture.requests.some(({ message }) =>
      message.service === 'get_trip_detail' && message.service_data.ride_id === 'synthetic-older'));
    await page.evaluate(() => {
      fixture.lost.add('synthetic-c');
      fixture.close('denied');
    });
    await checkCleared(page);
    await page.getByText('Ride or location access was withdrawn. Choose an account again.').waitFor();
    assert.equal(await account.locator('option[value="account-b"]').count(), 0);
    await page.evaluate(() => fixture.release('synthetic-c'));
    await checkCleared(page);

    await account.selectOption('account-a');
    await page.waitForFunction(() => fixture.subscriptions.length === 4);
    await page.evaluate(() => fixture.baseline());
    await page.locator('.leaflet-container').waitFor();
    await page.evaluate(() => fixture.replaceUser());
    await checkCleared(page);
    await page.waitForFunction(() => fixture.subscriptions.length === 5);
    await page.evaluate(() => fixture.baseline());
    await page.locator('.leaflet-container').waitFor();
    await page.evaluate(() => fixture.disconnect());
    await checkCleared(page);
    assert.equal(await account.locator('option').count(), 1);
    assert.equal(await page.getByText('Disconnected from Home Assistant.').isVisible(), true);
    await page.setViewportSize({ width: 320, height: 720 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

    const log = await page.evaluate(() => fixture.requests);
    assert.ok(log.filter(({ message }) => message.type === 'ninebot/history/subscribe')
      .every(({ message, options }) => message.include_track === true && options.resubscribe === false));
    assert.ok(log.filter(({ message }) => message.type === 'call_service').every(({ message }) =>
      message.domain === 'ninebot' && message.return_response === true &&
      (message.service === 'get_recorded_trips' || (message.service === 'get_trip_detail' &&
        message.service_data.local_only === true && message.service_data.include_track === true))));
    assert.ok(log.filter(({ message }) => message.service === 'get_recorded_trips').every(({ message }) =>
      message.service_data.start_date === '2026-08-01' && message.service_data.end_date === '2026-09-30'));
    await page.goto(origin + '/archive-test?large', { timeout: 60000 });
    await page.evaluate(checkArchivePaginationAndResume);
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
  } catch (error) {
    if (errors.length) throw new Error(errors.join('\n'), { cause: error });
    throw error;
  } finally {
    await page.close();
  }
  console.log('Local archive panel passed: gated local reads, account and permission changes, bounded fleet pagination and background recovery.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const require = createRequire(import.meta.url);
  const { chromium } = require(join(process.env.BROWSER_TEST_DEPS, 'node_modules/playwright'));
  const output = process.env.BROWSER_TEST_OUTPUT;
  if (output) await mkdir(output, { recursive: true });
  const { server, origin } = await serveReplay();
  let browser;
  try {
    browser = await chromium.launch();
    await checkArchiveReplay(browser, origin, output);
  } finally {
    try { await browser?.close(); }
    finally { await new Promise(resolve => server.close(resolve)); }
  }
}
