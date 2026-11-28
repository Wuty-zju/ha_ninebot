import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HistorySelection, rideInsights } from '../../custom_components/ninebot/frontend/history-selection.js';
import { trackKey } from '../../custom_components/ninebot/frontend/local-tracks.js';

const ride = device_id => ({ device_id, ride_id: 'shared-id', query_month: '202610',
  distance_m: 1200, vehicle_label: device_id, trail: 'private-raw', account: 'secret' });

test('single and multi-car selections share one primary and retain only reviewed summaries', () => {
  const session = new HistorySelection();
  session.select(ride('a')); session.select(ride('b'), true);
  const a = trackKey('a', ride('a')), b = trackKey('b', ride('b'));
  assert.equal(session.selected.size, 2); assert.equal(session.primary, b);
  session.activate(a); assert.equal(session.primary, a);
  assert.ok(!JSON.stringify([...session.selected]).includes('private-raw'));
  assert.ok(!JSON.stringify([...session.selected]).includes('secret'));
  const originalSlot = session.slots.get(b);
  session.select(ride('b')); assert.deepEqual([...session.selected.keys()], [a, b]);
  assert.equal(session.primary, b);
  session.remove(a);
  assert.equal(session.slots.get(b), originalSlot);
  session.select(ride('c'), true);
  assert.notEqual(session.slots.get(trackKey('c', ride('c'))), originalSlot);
  session.select(ride('d'));
  assert.deepEqual([...session.selected.keys()], [trackKey('d', ride('d'))]);
  for (let i = 0; i < 10; i++) session.select(ride(`car-${i}`), true);
  assert.equal(session.selected.size, 5);
  assert.equal(new Set(session.slots.values()).size, 5);
});

test('refresh updates selected summaries without changing the focused ride', () => {
  const session = new HistorySelection();
  const a = ride('a'), b = ride('b');
  const key = trackKey('b', b);
  session.select(a);
  session.select(b, true);
  session.update({ ...b, distance_m: 1300 });
  session.update(ride('unknown'));
  assert.equal(session.selected.size, 2);
  assert.equal(session.primary, key);
  assert.equal(session.selected.get(key).distance_m, 1300);
});

test('removing a ride or leaving a scope drops its private selection', () => {
  const session = new HistorySelection(), a = ride('a'), b = ride('b');
  session.select(a);
  session.select(b, true);
  session.remove(trackKey('b', b));
  assert.equal(session.primary, trackKey('a', a));
  for (const value of [{}, { ...a, device_id: '' }, { ...a, query_month: 'invalid' }]) {
    assert.equal(session.select(value), false);
  }
  assert.equal(session.selected.size, 1);
  session.clear();
  assert.equal(session.selected.size, 0);
  assert.equal(session.primary, null);
});

test('retaining the current ride keeps its identity, summary and color slot in one update', () => {
  let changes = 0;
  const session = new HistorySelection(() => changes++);
  for (const car of ['a', 'b', 'c']) session.select(ride(car), true);
  const key = trackKey('b', ride('b'));
  session.activate(key);
  const summary = session.selected.get(key), slot = session.slots.get(key);
  session.retainPrimary();
  assert.deepEqual([...session.selected], [[key, summary]]);
  assert.deepEqual([...session.slots], [[key, slot]]);
  assert.equal(session.primary, key);
  assert.equal(changes, 5);
  session.retainPrimary();
  assert.equal(changes, 5);
});

test('insights keep exact seconds, zero and incomplete metrics distinct', () => {
  const first = { distance_m: 0, duration_s: 61, energy_wh: 0, precision: { mileages: 1, ec: 0 } };
  const second = { distance_m: 1250, duration_h: .25, energy_wh: null, precision: { mileages: 2, ec: 1 } };
  const metrics = rideInsights([first, second]);
  assert.deepEqual(metrics.distance, { value: 1250, count: 2, precision: 2 });
  assert.deepEqual(metrics.duration, { value: 961, count: 2, precision: 0 });
  assert.deepEqual(metrics.energy, { value: null, count: 1, precision: 1 });
  assert.deepEqual(metrics.energy_intensity, { value: null, count: 0, precision: 1 });
  assert.equal(metrics.average_speed.value, 1250 / 961 * 3.6);
  assert.equal(rideInsights([first]).energy.value, 0);
  assert.equal(rideInsights([first]).average_speed.value, 0);
  assert.deepEqual(rideInsights([]).distance, { value: null, count: 0, precision: 0 });
});

test('ratios use summed archived inputs across rides and independent usable counts', () => {
  const first = { distance_m: 1000, duration_s: 360, energy_wh: 80, max_speed_m_s: 20 };
  const second = { distance_m: 3000, duration_s: 540, energy_wh: 120, average_speed_m_s: 99, energy_intensity_wh_per_km: 99 };
  const complete = rideInsights([first, second]);
  assert.deepEqual(complete.energy_intensity, { value: 50, count: 2, precision: 1 });
  assert.deepEqual(complete.average_speed, { value: 16, count: 2, precision: 1 });

  const withConflictingTime = rideInsights([first, { ...second, warnings: ['duration_time_difference'] }]);
  assert.equal(withConflictingTime.duration.value, 900);
  assert.deepEqual(withConflictingTime.energy_intensity, complete.energy_intensity);
  assert.deepEqual(withConflictingTime.average_speed, { value: null, count: 1, precision: 1 });

  const unusableTime = rideInsights([first, { ...second, duration_s: 0, duration_h: 1 }]);
  assert.equal(unusableTime.duration.value, 360);
  assert.equal(unusableTime.average_speed.value, null);
  assert.equal(unusableTime.average_speed.count, 1);
  assert.deepEqual(rideInsights([{ ...first, energy_wh: 0 }]).energy_intensity,
    { value: 0, count: 1, precision: 1 });
  assert.equal(rideInsights([{ ...first, distance_m: 0 }]).energy_intensity.count, 0);
  const invalid = rideInsights([{ distance_m: -100, duration_s: '30', energy_wh: Infinity }]);
  assert.ok(Object.values(invalid).every(metric => metric.value === null && metric.count === 0));
});
