/** Follow an Assistant or Companion URL using a synthetic HA connection and the native map contract. */
import assert from 'node:assert/strict';

export async function checkLinkedRides(browser, origin) {
  const page = await browser.newPage();
  const link = '?view=' + 'a'.repeat(32) + '&native';
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    customElements.define('ha-map', class extends HTMLElement {
      paths = [];
      editableLocations = [];
      getView() { return { zoom: 14 }; }
      fitBounds(bounds) { this.bounds = bounds; }
    });
  });
  const baseline = async () => {
    await page.waitForFunction(() => window.fixture?.subscriptions.length);
    assert.equal(await page.locator('#workspace').isVisible(), false);
    await page.evaluate(() => fixture.baseline());
  };
  try {
    await page.goto(origin + '/archive-test' + link);
    await baseline();
    const map = page.locator('ha-map');
    await map.waitFor({ state: 'attached' });
    await map.evaluate(map => new Promise(resolve => {
      const both = () => map.editableLocations.some(x => x.id.startsWith('ninebot-peer-'))
        ? resolve() : requestAnimationFrame(both);
      both();
    }));
    assert.equal(await map.count(), 1);
    assert.equal(await map.evaluate(map => map.paths.length), 4);
    assert.equal(await page.locator('td.selected').nth(2).innerText(), '210 Wh');
    assert.equal(await page.locator('td.selected').nth(3).innerText(), '58.3 Wh/km');
    const timeline = page.locator('#timeline');
    await timeline.press('Home');
    for (let i = 0; i < 15; i++) await timeline.press('ArrowRight');
    await map.evaluate(map => map.dispatchEvent(new CustomEvent('editable-location-clicked', { detail: { id: 'route-1' } })));
    const choices = page.locator('#choices');
    assert.deepEqual(await choices.locator('strong').allTextContents(), ['Synthetic A', 'Synthetic B']);
    await choices.getByRole('button', { name: /Synthetic B/ }).click();
    assert.equal(await timeline.inputValue(), '15');
    assert.match(await timeline.getAttribute('aria-valuetext'), /sequence 17.*sequence 17/i);
    assert.equal(await page.locator('td.current').nth(2).innerText(), '120 Wh');
    assert.equal(await page.locator('td.current').nth(3).innerText(), '50.0 Wh/km');
    assert.equal(await map.count(), 1);
    const cloudReads = () => fixture.requests.filter(({message: m}) =>
      m.service && (m.service !== 'get_trip_detail' || !m.service_data.local_only)).length;
    assert.equal(await page.evaluate(cloudReads), 0);
    await page.evaluate(() => fixture.close('denied'));
    assert.equal(await page.locator('#workspace').isVisible(), false);
    assert.equal(await map.count(), 0);
    await page.goto(origin + '/archive-test' + link + '&denied');
    await page.waitForFunction(() => fixture.requests.some(r => r.message.type === 'ninebot/open_ride_view'));
    assert.equal(await page.locator('#workspace').isVisible(), false);
    assert.equal(await page.evaluate(() => fixture.requests.filter(r => r.message.service).length), 0);
    await page.goto(origin + '/archive-test' + link + '&endpoints&partial');
    await baseline();
    await map.waitFor({ state: 'attached' });
    await page.waitForFunction(() => fixture.requests.filter(r => r.message.validate_only).length === 2);
    assert.equal(await page.locator('td.selected').first().innerText(), '3.6 km');
    assert.match(await page.locator('td.selected').nth(2).innerText(), /1\s*\/\s*2/);
    assert.equal(await map.evaluate(map => map.paths.filter(path => path.points.length === 1).length), 2);
    assert.equal(await map.evaluate(map => map.editableLocations.some(x => x.id.startsWith('ninebot-peer-'))), false);
    await page.goto(origin + '/archive-test?native&receipt=r1.' + 'a'.repeat(32) + '.' + 'b'.repeat(40));
    await baseline();
    await map.waitFor({ state: 'attached' });
    assert.equal(await page.locator('td.current').nth(2).innerText(), '120 Wh');
    assert.equal(await page.locator('#rides .ride').count(), 1);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
}
