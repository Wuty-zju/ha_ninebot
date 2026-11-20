import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HistorySelection } from '../../custom_components/ninebot/frontend/history-selection.js';
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
  session.select(ride('b')); assert.deepEqual([...session.selected.keys()], [b]);
  for (let i = 0; i < 10; i++) session.select(ride(`car-${i}`), true);
  assert.equal(session.selected.size, 5);
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
