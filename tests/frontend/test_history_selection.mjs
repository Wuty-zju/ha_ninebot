import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HistorySelection, selectionTotals } from '../../custom_components/ninebot/frontend/history-selection.js';
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

test('selection totals keep exact seconds, reported zero and incomplete metrics distinct', () => {
  const first = { distance_m: 0, duration_s: 61, energy_wh: 0, precision: { mileages: 1, ec: 0 } };
  const second = { distance_m: 1250, duration_h: .25, energy_wh: null, precision: { mileages: 2, ec: 1 } };
  assert.deepEqual(selectionTotals([first, second]), { distance_m: 1250, duration_s: 961, energy_wh: null,
    precision: { mileages: 2, ec: 1 } });
  assert.equal(selectionTotals([first]).energy_wh, 0);
  assert.equal(selectionTotals([]).distance_m, null);
});
