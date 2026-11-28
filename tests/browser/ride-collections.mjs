/** Download through the synthetic standalone UI with fresh, isolated browser storage. */
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { serveReplay } from './replay-server.mjs';

const PNG = Buffer.from('89504e470d0a1a0a', 'hex');
const gpxValid = `<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk>
  <trkseg><trkpt lat="30.002" lon="120.001"/><trkpt lat="30.003" lon="120.002"/></trkseg>
  <trkseg><trkpt lat="30.004" lon="120.005"/><trkpt lat="30.005" lon="120.006"/></trkseg>
  </trk></gpx>`;

async function pngDownload(page, height, mobile) {
  const image = page.locator('#postcard-image');
  await page.waitForFunction(() => document.getElementById('postcard-image').naturalWidth > 0);
  assert.equal(await image.evaluate(node => node.naturalHeight), height);
  const link = page.getByRole('link', { name: 'Download PNG' });
  const [download] = await Promise.all([page.waitForEvent('download'), mobile ? link.tap() : link.click()]);
  assert.equal(download.suggestedFilename(), 'ninebot-postcard-2026-08.png');
  const stream = await download.createReadStream();
  const bytes = Buffer.concat(await Array.fromAsync(stream));
  assert.deepEqual(bytes.subarray(0, 8), PNG);
  assert.equal(bytes.readUint32BE(16), 960);
  assert.equal(bytes.readUint32BE(20), height);
  for (let at = 8; at + 12 <= bytes.length; at += bytes.readUInt32BE(at) + 12) {
    const type = bytes.toString('ascii', at + 4, at + 8);
    assert.ok(['IHDR', 'PLTE', 'IDAT', 'IEND'].includes(type), `Unexpected PNG chunk ${type}`);
  }
}

export async function checkRideCollections(browser, origin, mobile = false, output) {
  const page = await browser.newPage({ acceptDownloads: true, hasTouch: mobile, isMobile: mobile,
    viewport: mobile ? { width: 320, height: 720 } : { width: 1180, height: 880 } });
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin !== origin) external.push(url.href);
  });
  await page.addInitScript(() => {
    window.postcardCanvas = { strings: [], lines: 0 };
    const painter = CanvasRenderingContext2D.prototype;
    const fillText = painter.fillText, lineTo = painter.lineTo;
    painter.fillText = function (text, ...position) {
      if (this.canvas.width === 960) window.postcardCanvas.strings.push(text);
      return fillText.call(this, text, ...position);
    };
    painter.lineTo = function (...position) {
      if (this.canvas.width === 960) window.postcardCanvas.lines++;
      return lineTo.call(this, ...position);
    };
  });
  const url = origin + '/replay/?lang=en&scenario=endpoints';
  const checks = page.locator('#rides input[type=checkbox]');
  const openControls = async () => {
    if (!await page.locator('#collection-controls').evaluate(node => node.open)) {
      await page.locator('#collection-summary').click();
    }
  };
  const expectCount = value => page.waitForFunction(expected =>
    document.getElementById('collection-total').textContent === `${expected} selected`, value);
  const mapChoice = page.locator('#postcard-map');
  try {
    await page.goto(url);
    await checks.last().waitFor();
    if (mobile) await checks.last().tap();
    else await checks.last().check();
    await expectCount(1);
    await page.getByRole('button', { name: 'Next' }).click();
    if (mobile) await checks.first().tap();
    else await checks.first().check();
    await expectCount(2);
    await openControls();
    await page.getByRole('button', { name: 'Save selection to collection' }).click();
    assert.equal(await page.locator('#collection-status').textContent(), 'Selection saved.');
    const storage = await page.evaluate(() => Object.entries(localStorage)
      .filter(([key]) => key.startsWith('ninebot.ride-collections.v1:')));
    assert.equal(storage.length, 2);
    assert.deepEqual(storage.map(([, value]) => JSON.parse(value)), Array(2).fill([
      ['synthetic-b', 'synthetic-ride', '202608'], ['synthetic-a', 'synthetic-august-list', '202608'],
    ]));
    assert.equal(await mapChoice.isChecked(), false);
    await page.getByRole('button', { name: 'Create postcard' }).click();
    await pngDownload(page, 610, mobile);
    if (output) await page.screenshot({ path: join(output, `postcard-${mobile ? 'phone' : 'desktop'}.png`) });
    assert.deepEqual(await page.evaluate(() => window.postcardCanvas), {
      strings: ['August 2026', 'Monthly ride postcard', 'Distance', '3.1 km', 'Duration', '20 min', 'Energy used', '158 Wh'], lines: 0,
    });
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await page.waitForFunction(() => !document.getElementById('postcard-image').hasAttribute('src'));
    await mapChoice.check();
    await page.getByRole('button', { name: 'Create postcard' }).click();
    await pngDownload(page, 984, mobile);
    assert.equal(await page.evaluate(() => window.postcardCanvas.strings.at(-1)), 'Saved route sketches');
    assert.equal(await page.evaluate(() => window.postcardCanvas.lines), 0, 'Saved endpoints must remain separate dots');
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await page.reload();
    await expectCount(2);
    await openControls();
    assert.equal(await mapChoice.isChecked(), false);
    await page.getByRole('button', { name: 'Clear selection' }).click();
    await page.locator('#collection-shelf').selectOption('commute');
    await page.getByRole('button', { name: 'Open collection' }).click();
    assert.equal(await page.locator('#collection-status').textContent(), 'This collection has no rides yet.');
    await checks.first().check();
    await page.getByRole('button', { name: 'Save selection to collection' }).click();
    await page.locator('#collection-shelf').selectOption('weekend');
    await page.getByRole('button', { name: 'Open collection' }).click();
    await expectCount(2);

    await page.getByLabel('Demo account').selectOption('b');
    await page.locator('#rides button').getByText('Synthetic C').waitFor();
    await expectCount(0);
    await openControls();
    await page.getByRole('button', { name: 'Open collection' }).click();
    assert.equal(await page.locator('#collection-status').textContent(), 'This collection has no rides yet.');
    assert.equal(await page.locator('#collection-list').textContent(), '');
    await page.getByLabel('Demo account').selectOption('a');
    await expectCount(2);
    await page.goto(origin + '/replay/?lang=en&scenario=unavailable');
    await expectCount(1);
    await openControls();
    assert.equal(await page.locator('#collection-status').textContent(), 'Some saved rides are unavailable.');

    const current = () => page.locator('td.current').allTextContents();
    const stats = await current();
    await page.locator('#gpx-file').setInputFiles({ name: 'demo.gpx', mimeType: 'application/gpx+xml', buffer: Buffer.from(gpxValid) });
    await page.waitForFunction(() => document.getElementById('gpx-status').textContent === '4 local GPX points overlaid.');
    assert.deepEqual(await current(), stats);
    await page.locator('#gpx-file').setInputFiles({ name: 'invalid.gpx', mimeType: 'application/gpx+xml',
      buffer: Buffer.from('<!DOCTYPE gpx SYSTEM "https://example.invalid/secret"><gpx/>') });
    await page.waitForFunction(() => document.getElementById('gpx-status').textContent === 'Could not read a GPX track.');
    await page.getByRole('button', { name: 'Remove GPX' }).click();
    assert.equal(await page.locator('#gpx-status').textContent(), '');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
  } finally {
    await page.close();
  }
  console.log(`Ride collections passed (${mobile ? 'phone' : 'desktop'}): scoped favorites, two-page draft, PNG, GPX and unavailable rides.`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const require = createRequire(import.meta.url);
  const playwright = require(join(process.env.BROWSER_TEST_DEPS, 'node_modules/playwright'));
  const output = process.env.BROWSER_TEST_OUTPUT;
  if (output) await mkdir(output, { recursive: true });
  const { server, origin } = await serveReplay();
  let browser;
  try {
    browser = await playwright[process.env.BROWSER_TEST_ENGINE ?? 'chromium'].launch();
    await checkRideCollections(browser, origin, false, output);
    await checkRideCollections(browser, origin, true, output);
  } finally {
    try { await browser?.close(); }
    finally { await new Promise(resolve => server.close(resolve)); }
  }
}
