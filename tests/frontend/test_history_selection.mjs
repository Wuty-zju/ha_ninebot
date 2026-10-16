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

test('sample focus and brush synchronize without changing the selected rides', () => {
  const changes = [], session = new HistorySelection(kind => changes.push(kind));
  const value = ride('a'), key = trackKey('a', value);
  session.select(value); session.focusSample(key, 4); session.brushSamples(key, 9, 2);
  assert.deepEqual(session.focus, { key, sequence: 4 });
  assert.deepEqual(session.brush, { key, start: 2, end: 9 });
  assert.equal(session.selected.size, 1); assert.equal(session.primary, key);
  assert.deepEqual(changes, ['selection', 'focus', 'focus']);
  const revision = session.revision;
  for (const seq of [-1, 1.2, NaN, 1048576]) session.focusSample(key, seq);
  session.focusSample('another-scope', 2); session.focusSample(key, 4);
  assert.equal(session.revision, revision);
  session.select({ ...value, distance_m: 1300 }, true);
  assert.equal(session.focus.sequence, 4); assert.equal(session.selected.get(key).distance_m, 1300);
});

test('removing a focused ride or clearing on scope change leaves no private selection', () => {
  const session = new HistorySelection(), a = ride('a'), b = ride('b');
  const key = trackKey('b', b);
  session.select(a); session.select(b, true); session.focusSample(key, 1); session.brushSamples(key, 0, 4);
  session.remove(key);
  assert.equal(session.primary, trackKey('a', a)); assert.equal(session.focus, null); assert.equal(session.brush, null);
  session.clear(); assert.equal(session.selected.size, 0); assert.equal(session.primary, null);
});
