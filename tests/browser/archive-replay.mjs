/** Mount the production panel with a synthetic Home Assistant connection. */
import assert from 'node:assert/strict';
import { join } from 'node:path';

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
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
  } catch (error) {
    if (errors.length) throw new Error(errors.join('\n'), { cause: error });
    throw error;
  } finally {
    await page.close();
  }
  console.log('Local archive panel passed: gated local reads, same-ID account replacement, access withdrawal, late results and disconnect cleanup.');
}
