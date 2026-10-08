import assert from 'node:assert/strict';
import { test } from 'node:test';
import { TripBrowser, safeRide, digits, businessDates, samplePolyline, NinebotTripCard }
  from '../../custom_components/ninebot/frontend/ninebot-trip-card.js';
import { LABELS } from '../../custom_components/ninebot/frontend/labels.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const row = (id = 'synthetic-ride') => ({ ride_id: id, detail_id: id, query_month: '202609',
  start_time: '2026-09-03T08:00:00+00:00', end_time: '2026-09-03T08:10:00+00:00',
  distance_m: 1200, duration_s: 600, max_speed_m_s: 30 / 3.6, average_speed_m_s: 2,
  energy_wh: 100, precision: { mileages: 2, speed: 1 } });
const page = (id = 'synthetic-ride') => ({ source_mode: 'ride_archive', revision: 1,
  rides: [row(id)], next_cursor: 'a'.repeat(32), range: {},
  coverage: { missing_months: ['202608'], partial_months: ['202609'], unknown_months: [] } });
function setup(handler, config = { entity: 'calendar.synthetic_vehicle_rides', limit: 1 }) {
  const calls = [], connection = {};
  const hass = { user: { id: 'synthetic-reader' }, connection, connected: true,
    language: 'zh-Hans', config: { time_zone: 'Asia/Shanghai' }, callWS: async request => {
      calls.push(request);
      if (request.type === 'config/entity_registry/get') return { device_id: 'synthetic-device' };
      return { response: await handler(request) };
    } };
  const browser = new TripBrowser(); browser.configure(config); browser.setHass(hass); browser.connect();
  return { browser, hass, calls };
}

test('open and repeated HA telemetry updates read only one local page', async () => {
  const { browser, hass, calls } = setup(() => page()); await tick();
  assert.equal(calls.length, 2); assert.equal(calls[1].service, 'get_recorded_trips');
  for (let i = 0; i < 100; i++) browser.setHass({ ...hass, states: { unused: i } });
  await tick(); assert.equal(calls.length, 2); assert.equal(browser.page.rides.length, 1);
  assert.ok(calls[1].return_response); assert.equal(calls[1].service_data.device_id, 'synthetic-device');
  assert.ok(!('refresh' in calls[1].service_data)); assert.ok(!('include_track' in calls[1].service_data));
  await browser.read(browser.page.next_cursor);
  assert.equal(browser.pageNumber, 2); assert.equal(browser.page.rides.length, 1);
  assert.equal(calls[2].service_data.cursor, 'a'.repeat(32));
});

test('detail is explicit, scoped, bounded and never requests or retains GPS', async () => {
  const { browser, calls } = setup(request => request.service === 'get_recorded_trips' ? page() :
    { query_month: '202609', received_at: '2026-10-08T10:00:00+00:00',
      ride: { ...row(), track: [{ latitude: 30, longitude: 120 }], secret: 'not-retained',
        speed_samples: [{ sequence: 1, speed_raw: 78.9 }] } });
  await tick(); assert.equal(calls.length, 2);
  await browser.detail(row()); assert.equal(calls.length, 2); // Not a current page object.
  await browser.detail(browser.page.rides[0]);
  const request = calls.at(-1);
  assert.equal(request.service, 'get_trip_detail'); assert.equal(request.service_data.query_month, '202609');
  assert.equal(request.service_data.include_track, false); assert.equal(request.service_data.max_points, 500);
  const result = browser.details.get('synthetic-ride');
  assert.ok(!('track' in result)); assert.ok(!('secret' in result));
  assert.deepEqual(result.speed_samples, [{ sequence: 1, speed_raw: 78.9 }]);
  assert.equal(result.received_at, '2026-10-08T10:00:00+00:00');
});

test('no queued duplicate detail; only five current-page details survive', async () => {
  let resolve;
  const { browser, calls } = setup(request => request.service === 'get_recorded_trips' ?
    { ...page(), rides: Array.from({ length: 8 }, (_, i) => row(`ride-${i}`)) } :
    new Promise(done => { resolve = () => done({ query_month: '202609', ride: row(request.service_data.ride_id) }); }),
    { device_id: 'synthetic-device', limit: 8 });
  await tick();
  for (const ride of browser.page.rides) {
    const pending = browser.detail(ride); await browser.detail(ride); resolve(); await pending;
  }
  assert.equal(calls.filter(c => c.service === 'get_trip_detail').length, 8);
  assert.equal(browser.details.size, 5); assert.ok(!browser.details.has('ride-0'));
  const pending = browser.detail(browser.page.rides[0]); browser.disconnect(); resolve(); await pending;
  assert.equal(browser.details.size, 0); assert.equal(browser.page, null);
});

test('late responses cannot cross config, user, connection or date changes', async () => {
  for (const change of [
    (browser, hass) => browser.configure({ device_id: 'new-device' }),
    (browser, hass) => browser.setHass({ ...hass, user: { id: 'other-reader' } }),
    (browser, hass) => browser.setHass({ ...hass, connection: {} }),
    (browser, hass) => browser.dates('2026-08-01', '2026-08-02'),
    browser => browser.disconnect(),
  ]) {
    const pending = [];
    const { browser, hass } = setup(() => new Promise(resolve => pending.push(resolve)));
    await tick(); change(browser, hass); pending[0](page('old-private-ride')); await tick();
    assert.ok(!browser.page); assert.equal(browser.details.size, 0);
    browser.disconnect();
  }
});

test('permissions, invalid responses and cursor revisions fail without replay loops', async () => {
  for (const error of [{ code: 'unauthorized' }, { translation_key: 'history_cursor' }, undefined]) {
    const { browser, calls } = setup(() => Promise.reject(error)); await tick();
    assert.equal(browser.busy, false); assert.ok(browser.error); assert.equal(calls.length, 2);
    for (let i = 0; i < 5; i++) browser.setHass(browser.hass);
    await tick(); assert.equal(calls.length, 2);
  }
  const { browser, calls } = setup(() => ({ ...page(), rides: [row(), row('extra')] }));
  await tick(); assert.equal(browser.error, 'read_error'); assert.equal(browser.page, null);
  assert.equal(calls.length, 2);
});

test('detail mismatch rejected and archive cursor rejection preserves current page', async () => {
  const { browser } = setup(request => {
    if (request.service === 'get_trip_detail') return { query_month: '202609', ride: row('foreign') };
    if (request.service_data.cursor) return Promise.reject({ translation_key: 'history_cursor' });
    return page();
  });
  await tick(); await browser.detail(browser.page.rides[0]);
  assert.equal(browser.error, 'detail_error'); assert.equal(browser.details.size, 0);
  await browser.read(browser.page.next_cursor);
  assert.equal(browser.error, 'cursor_error'); assert.equal(browser.page.rides[0].ride_id, 'synthetic-ride');
  assert.equal(browser.page.next_cursor, null);
});

test('disconnection clears data and reconnect reads locally once; devices isolate', async () => {
  const first = setup(() => page('first'), { device_id: 'first-device' });
  const second = setup(() => page('second'), { device_id: 'second-device' }); await tick();
  first.browser.setHass({ ...first.hass, connected: false }); assert.equal(first.browser.page, null);
  first.browser.setHass(first.hass); await tick();
  assert.equal(first.calls.length, 2); assert.equal(second.calls.length, 1);
  assert.equal(second.browser.page.rides[0].ride_id, 'second');
  assert.ok(first.calls.every(c => c.service_data.device_id === 'first-device'));
});

test('raw/sample bounds and units stay separate; source precision survives', () => {
  const value = safeRide({ ...row(), trail: 'private', password: 'private',
    speed_samples: Array.from({ length: 600 }, (_, sequence) => ({ sequence, speed_raw: 78.9 })) });
  assert.equal(value.speed_samples.length, 500); assert.equal(value.precision.mileages, 2);
  assert.ok(!('trail' in value)); assert.ok(!('password' in value));
  assert.equal(digits(99), 6); assert.equal(digits(-1), 0); assert.equal(digits(null, 2), 2);
  assert.equal(samplePolyline([]), null);
  assert.ok(!samplePolyline([{ sequence: 0, speed_raw: 5 }, { sequence: 1, speed_raw: 5 }]).includes('NaN'));
  assert.equal(samplePolyline([{ sequence: 0, speed_raw: -1e308 }, { sequence: 1, speed_raw: 1e308 }]), null);
  assert.deepEqual(businessDates(new Date('2026-09-30T16:00:00Z')), ['2026-10-01', '2026-10-01']);
});

test('all 21 locale labels populated, date/time and metric precision valid', () => {
  assert.equal(Object.keys(LABELS).length, 21);
  for (const labels of Object.values(LABELS)) {
    assert.deepEqual(Object.keys(labels).sort(), Object.keys(LABELS.en).sort());
    assert.ok(Object.values(labels).every(value => typeof value === 'string' && value));
  }
  const format = NinebotTripCard.prototype;
  const fake = { browser: { hass: { language: 'en', config: { time_zone: 'Asia/Shanghai' } } } };
  assert.equal(format.timestamp.call(fake, 'invalid'), '—');
  assert.equal(format.timestamp.call(fake, null), '—');
  assert.equal(format.number.call(fake, null, 2, 'km'), '—');
  assert.equal(format.number.call(fake, 1.2, 2, 'km'), '1.20 km');
  assert.equal(format.number.call(fake, 0, 0, 'Wh'), '0 Wh');
});

test('configuration does not silently choose an account or ignore bad page bounds', () => {
  const browser = new TripBrowser();
  for (const config of [{}, { entity: 'sensor.foo' }, { entity: 'calendar.foo', device_id: 'x' },
    { device_id: 'x', limit: 101 }, { device_id: 'x', limit: true }, { device_id: 'x', start_date: 'bad' }]) {
    assert.throws(() => browser.configure(config));
  }
});


test('editing dates does not reread on ordinary HA updates until explicitly applied', async () => {
  const { browser, hass, calls } = setup(() => page()); await tick();
  browser.dates('2026-08-01', '2026-08-03');
  for (let i = 0; i < 10; i++) browser.setHass(hass);
  await tick(); assert.equal(calls.length, 2); assert.equal(browser.page, null);
  await browser.read(); assert.equal(calls.at(-1).service_data.start_date, '2026-08-01');
});
