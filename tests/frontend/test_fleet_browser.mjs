import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FleetBrowser, nativeCards, vehicleTargets }
  from '../../custom_components/ninebot/frontend/fleet-browser.js';
import { trackKey } from '../../custom_components/ninebot/frontend/local-tracks.js';
import { routePaths } from '../../custom_components/ninebot/frontend/route-map.js';
import { samplePaths } from '../../custom_components/ninebot/frontend/trip-browser.js';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const tick = () => new Promise(resolve => setImmediate(resolve));
const row = { ride_id: 'same-id', detail_id: 'detail-id', query_month: '202609',
  start_time: '2026-09-03T08:00:00Z', distance_m: 1000 };
const page = next => ({ source_mode: 'ride_archive', sort: 'desc', revision: 1, rides: [row], next_cursor: next,
  coverage: { missing_months: [], partial_months: ['202609'], unknown_months: [] } });
function setup(handler, config = { vehicles: [{ device_id: 'device-a', name: 'A' },
  { device_id: 'device-b', name: 'B' }], limit: 4 }) {
  const calls = [], cleared = [];
  const hass = { user: { id: 'reader' }, connection: {}, connected: true, callWS: async request => {
    calls.push(request); return { response: await handler(request) };
  } };
  const browser = new FleetBrowser(() => {}, () => cleared.push(true));
  browser.configure(config); browser.setHass(hass); browser.connect();
  return { browser, hass, calls, cleared };
}

test('global recent merge retains unconsumed vehicle rows across pages without equal-share loss', async () => {
  const histories = { 'device-a': [9, 8, 7, 5, 1], 'device-b': [10, 6, 2] };
  const { browser, calls } = setup(request => {
    const { device_id, cursor = '0', limit, sort } = request.service_data;
    assert.equal(sort, 'desc');
    const offset = Number(cursor), source = histories[device_id];
    const selected = source.slice(offset, offset + limit);
    return { ...page(null), rides: selected.map(day => ({ ...row, ride_id: `${device_id}-${day}`,
      start_time: `2026-09-${String(day).padStart(2, '0')}T08:00:00Z` })),
      next_cursor: offset + limit < source.length ? String(offset + limit) : null };
  });
  await tick();
  assert.deepEqual(browser.page.rides.map(ride => Number(ride.ride_id.split('-').at(-1))), [10, 9, 8, 7]);
  assert.equal(calls.length, 2); assert.ok(calls.every(call => call.service_data.limit === 4));
  assert.equal(browser.page.coverage.partial_months.length, 2);
  const stale = browser.page.next_cursor;
  await browser.read(stale);
  assert.deepEqual(browser.page.rides.map(ride => Number(ride.ride_id.split('-').at(-1))), [6, 5, 2, 1]);
  assert.equal(calls.length, 3); assert.equal(browser.page.next_cursor, null);
  const current = browser.page;
  await browser.read(stale); assert.equal(browser.page, current);
});

test('same upstream ride ID on different vehicles cannot collide in detail cache', async () => {
  const { browser, calls } = setup(request => request.service === 'get_recorded_trips' ? page(null) :
    { query_month: '202609', source_mode: 'ride_archive', ride: row });
  await tick();
  for (const ride of browser.page.rides) await browser.detail(ride);
  assert.equal(browser.details.size, 2);
  assert.ok(browser.details.has(trackKey('device-a', row)));
  assert.ok(browser.details.has(trackKey('device-b', row)));
  assert.deepEqual(calls.filter(c => c.service === 'get_trip_detail').map(c => c.service_data.device_id), ['device-a', 'device-b']);
});

test('selection and user/date/connection changes clear private data; stale results ignored', async () => {
  const pending = [];
  const { browser, hass, cleared } = setup(() => new Promise(resolve => pending.push(resolve)));
  await tick(); const baseline = cleared.length;
  browser.setHass({ ...hass, user: { id: 'other-reader' } });
  pending[0](page(null)); await tick();
  assert.equal(browser.page, null); assert.ok(cleared.length > baseline);
  browser.disconnect();
  const second = setup(() => page(null)); await tick();
  second.browser.select(1, false); await tick();
  assert.equal(second.browser.page.rides.length, 1);
  assert.equal(second.browser.page.rides[0].device_id, 'device-a');
  const count = second.calls.length;
  second.browser.dates('2026-08-01', '2026-08-31'); second.browser.setHass(second.hass);
  await tick(); assert.equal(second.calls.length, count); assert.equal(second.browser.page, null);
});

test('one denied vehicle does not expose a partially assembled fleet page', async () => {
  const { browser, cleared } = setup(request => {
    if (request.service_data.device_id === 'device-b') throw { code: 'unauthorized' };
    return page(null);
  });
  await tick(); assert.equal(browser.page, null); assert.equal(browser.error, 'denied');
  assert.ok(cleared.length >= 3); assert.equal(browser.details.size, 0);
});

test('single existing configuration remains local and calendar resolution is explicit', async () => {
  const calls = [];
  const browser = new FleetBrowser(); browser.configure({ entity: 'calendar.synthetic', limit: 1 });
  browser.setHass({ user: { id: 'reader' }, connection: {}, callWS: async request => {
    calls.push(request);
    return request.type === 'config/entity_registry/get' ? { device_id: 'device-a' } : { response: page(null) };
  } }); browser.connect(); await tick();
  assert.equal(calls.length, 2); assert.equal(browser.page.rides[0].device_id, 'device-a');
  assert.equal(calls.at(-1).service_data.limit, 1);
});

test('fleet targets/native card options are bounded and never include route data', () => {
  for (const config of [{ vehicles: [] }, { vehicles: ['calendar.a', 'calendar.a'] },
    { entity: 'calendar.a', vehicles: ['calendar.b'] }, { vehicles: Array(6).fill('calendar.a') },
    { vehicles: [{ device_id: 'a', entity: 'calendar.a' }] }]) assert.throws(() => vehicleTargets(config));
  assert.doesNotThrow(() => new FleetBrowser().configure({ vehicles: ['calendar.a', 'calendar.b'], limit: 1 }));
  const cards = nativeCards(vehicleTargets({ vehicles: ['calendar.a', 'calendar.b'] }), ['ninebot:synthetic_day_distance_km']);
  assert.equal(cards.cards[0].type, 'calendar'); assert.deepEqual(cards.cards[0].entities, ['calendar.a', 'calendar.b']);
  assert.equal(cards.cards[1].type, 'statistics-graph'); assert.deepEqual(cards.cards[1].stat_types, ['change']);
  const monthly = nativeCards([], ['ninebot:synthetic_month_duration_s']).cards[0];
  assert.equal(monthly.period, 'month'); assert.equal(monthly.days_to_show, 365);
  assert.ok(!JSON.stringify(cards).includes('latitude'));
  assert.throws(() => nativeCards([], ['javascript:alert(1)']));
});

test('simplified routes and true gaps do not become road polylines; display thinning stays contiguous', () => {
  const points = [0, 1, 4, 5].map(sequence => ({ sequence, latitude: 30, longitude: 120, speed_raw: sequence }));
  assert.deepEqual(routePaths({ points, kind: 'server_simplified' }).map(path => path.length), [1, 1, 1, 1]);
  assert.deepEqual(routePaths({ points, kind: 'sampled_unverified' }).map(path => path.length), [2, 2]);
  assert.equal(samplePaths(points).length, 2);
  assert.equal(samplePaths(points.map(point => ({ ...point, segment: 0 }))).length, 1);
  assert.equal(samplePaths(points.map((point, i) => ({ ...point, segment: i < 2 ? 0 : 1 }))).length, 2);
});

test('pinned bundled map files match their source inventory and retain the license', () => {
  const base = new URL('../../custom_components/ninebot/frontend/vendor/leaflet/', import.meta.url);
  const source = JSON.parse(readFileSync(new URL('SOURCES.json', base)));
  assert.equal(source.version, '1.9.4'); assert.equal(source.license, 'BSD-2-Clause');
  for (const [path, record] of Object.entries(source.files)) {
    const bytes = readFileSync(new URL(path, base)); assert.equal(bytes.length, record.bytes);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), record.sha256);
  }
  assert.ok(readFileSync(new URL('LICENSE', base), 'utf8').includes('BSD 2-Clause'));
});


test('saved detail availability is strict and scoped to each response', async () => {
  const { safeRide } = await import('../../custom_components/ninebot/frontend/trip-browser.js');
  assert.equal(safeRide({ detail_saved: false }).detail_saved, false);
  assert.equal(safeRide({ detail_saved: true }).detail_saved, true);
  for (const value of [undefined, 'false', 0]) assert.equal(safeRide({ detail_saved: value }).detail_saved, null);
});


test('partial archive status is retained without keeping raw context or payloads', async () => {
  const { browser } = setup(request => request.service === 'get_recorded_trips' ? page(null) :
    { source_mode: 'ride_archive', query_month: row.query_month, ride: row,
      detail_context: { local_integrity: { partial: true }, raw_secret: 'must-not-retain' } });
  await tick();
  await browser.detail(browser.page.rides[0]);
  const detail = browser.details.get(trackKey('device-a', row));
  assert.equal(detail.local_partial, true);
  assert.ok(!JSON.stringify(detail).includes('must-not-retain'));
});


test('enumeration months remain vehicle-scoped and mixed older backends stay unknown', async () => {
  const supported = setup(() => ({ ...page(null), coverage: {
    ...page(null).coverage, enumerated_months: ['202609'] } }));
  await tick();
  assert.deepEqual(supported.browser.page.coverage.enumerated_months,
    ['device-a:202609', 'device-b:202609']);
  const mixed = setup(request => request.service_data.device_id === 'device-a' ?
    ({ ...page(null), coverage: { ...page(null).coverage, enumerated_months: ['202609'] } }) : page(null));
  await tick();
  assert.equal(mixed.browser.page.coverage.enumerated_months, undefined);
});


test('single-row pages merge ties, empty cars and slow cars without duplicates', async () => {
  const histories = { 'device-a': ['z', 'b', 'a'], 'device-b': [] };
  const { browser } = setup(async request => {
    const { device_id, cursor = '0' } = request.service_data;
    if (device_id === 'device-b') await tick();
    const offset = Number(cursor), ids = histories[device_id];
    return { ...page(null), rides: ids.slice(offset, offset + 1).map(ride_id => ({ ...row, ride_id })),
      next_cursor: offset + 1 < ids.length ? String(offset + 1) : null };
  }, { vehicles: [{ device_id: 'device-a' }, { device_id: 'device-b' }], limit: 1 });
  await tick(); await tick();
  const ids = [];
  do {
    ids.push(...browser.page.rides.map(ride => ride.ride_id));
    if (!browser.page.next_cursor) break;
    await browser.read(browser.page.next_cursor);
  } while (true);
  assert.deepEqual(ids, ['z', 'b', 'a']);
});


test('a changed source revision cannot silently mix fleet pages', async () => {
  let revision = 1;
  const { browser } = setup(request => ({ ...page('next'), revision,
    rides: [{ ...row, ride_id: request.service_data.cursor ? 'older' : 'newer',
      start_time: request.service_data.cursor ? '2026-09-01T00:00:00Z' : row.start_time }] }),
    { device_id: 'device-a', limit: 1 });
  await tick(); revision++;
  await browser.read(browser.page.next_cursor);
  assert.equal(browser.error, 'cursor_error'); assert.equal(browser.page.next_cursor, null);
  assert.equal(browser.streams.length, 0);
});


test('unsorted or non-descending old responses fail instead of pretending to be recent', async () => {
  for (const response of [{ ...page(null), sort: undefined },
    { ...page(null), rides: [{ ...row, start_time: 'invalid' }] },
    { ...page(null), rides: [{ ...row, ride_id: 'old', start_time: '2026-09-01T00:00:00Z' }, row] }]) {
    const { browser } = setup(() => response);
    await tick(); assert.equal(browser.page, null); assert.equal(browser.error, 'read_error');
  }
});


test('revoked history access during explicit detail clears saved fleet pages and buffers', async () => {
  const { browser } = setup(request => {
    if (request.service === 'get_recorded_trips') return page(null);
    throw { code: 'unauthorized' };
  });
  await tick();
  await browser.detail(browser.page.rides[0]);
  assert.equal(browser.error, 'denied'); assert.equal(browser.page, null);
  assert.equal(browser.details.size, 0); assert.equal(browser.streams.length, 0);
});
