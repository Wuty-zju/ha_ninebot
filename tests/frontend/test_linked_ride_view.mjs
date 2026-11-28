import assert from 'node:assert/strict';
import { test } from 'node:test';
import { LinkedRideView, readRideLink } from '../../custom_components/ninebot/frontend/linked-ride-view.js';
import { rideInsights } from '../../custom_components/ninebot/frontend/history-selection.js';
import { syntheticRide } from '../../examples/ride-replay/synthetic-archive.mjs';

const token = 'a'.repeat(32);
const link = readRideLink('?view=' + token);
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture({ partial = false, denied = false } = {}) {
  const rides = [syntheticRide('synthetic-a'), syntheticRide('synthetic-b', { overlap: true, incomplete: partial })]
    .map(r => ({ ...r, detail_saved: true }));
  const events = [], reads = [], state = { denied, clear: 0 };
  const hass = {
    user: { id: 'reader' },
    connection: {
      subscribeMessage(notify, message) {
        assert.deepEqual(message.device_ids, ['synthetic-a', 'synthetic-b']);
        assert.equal(message.view, token);
        events.push(notify);
        return () => {};
      },
    },
    async callWS(message) {
      if (state.denied) throw Error('unauthorized');
      if (message.type === 'ninebot/open_ride_view') return message.validate_only ? { valid: true } : { rides };
      assert.equal(message.service, 'get_trip_detail');
      return { response: { local: true } };
    },
  };
  const view = new LinkedRideView(hass, link, {
    setLinkedRides(rows) { state.rows = rows; },
    async readTrack(row) { reads.push(row.device_id); },
    clear() { state.clear++; delete state.rows; },
  }, () => { state.failed = true; }, () => {});
  const baseline = () => events[0]({ type: 'baseline', schema_version: 1,
    vehicles: rides.map(row => ({ device_id: row.device_id, selection_version: token + ':1' })) });
  return { view, state, reads, baseline };
}

test('the same link selects the listed order after fresh authorization and retains partial totals', async () => {
  assert.equal(readRideLink(''), null);
  assert.deepEqual(readRideLink('?view=x&receipt=y'), { kind: 'invalid' });
  for (const partial of [false, true]) {
    const f = fixture({ partial });
    await f.view.open(); await tick();
    assert.equal(f.state.rows, undefined);
    f.baseline(); await tick();
    assert.deepEqual(f.state.rows.map(row => row.device_id), ['synthetic-a', 'synthetic-b']);
    assert.deepEqual(f.reads, ['synthetic-a', 'synthetic-b']);
    const stats = rideInsights(f.state.rows);
    assert.equal(stats.distance.value, 3600);
    assert.equal(stats.energy.value, partial ? null : 210);
    assert.equal(stats.energy.count, partial ? 1 : 2);
    assert.equal(stats.energy_intensity.value?.toFixed(1) ?? null, partial ? null : '58.3');
    f.view.close();
  }
});

test('an old link cannot paint even its first summary following revocation', async () => {
  const f = fixture({ denied: true });
  await f.view.open();
  assert.equal(f.state.failed, true);
  assert.equal(f.state.rows, undefined);
  assert.deepEqual(f.reads, []);
});

test('closing the page before the baseline discards delayed selection and geometry', async () => {
  const f = fixture();
  await f.view.open(); await tick();
  f.view.close(); f.baseline(); await tick();
  assert.equal(f.state.rows, undefined);
  assert.deepEqual(f.reads, []);
});
