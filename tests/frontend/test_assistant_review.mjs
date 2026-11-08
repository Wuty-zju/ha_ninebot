import test from 'node:test';
import assert from 'node:assert/strict';
import { ReviewSession, TEXT } from '../../custom_components/ninebot/frontend/assistant-review.js';

const vehicle = { vehicle_ref: 'vehicle-a', label: 'Synthetic A' };
const task = () => ({ task_id: 'a'.repeat(32), vehicle_ref: 'vehicle-a', state: 'awaiting_approval',
  plan_hash: 'b'.repeat(64), approval_expires_at: '2099-01-01T00:00:00Z' });
function fixture() {
  const calls = [];
  const hass = { callWS: async (message) => {
    calls.push(message);
    if (message.type.endsWith('/vehicles')) return { data: { vehicles: [vehicle], more_available: false } };
    if (message.operation === 'list') return { tasks: [task()] };
    if (message.operation === 'prepare') return { task: task(), token: 'synthetic-confirmation-token' };
    return { task: { ...task(), state: message.operation === 'approve' ? 'queued' : 'cancelled' } };
  } };
  return { session: new ReviewSession(hass), hass, calls };
}

test('load uses only local vehicle/list commands and single-vehicle selection', async () => {
  const { session, calls } = fixture();
  await session.load();
  assert.equal(session.tasks.length, 1);
  assert.equal(session.vehicleRef, vehicle.vehicle_ref);
  assert.deepEqual(calls.map((c) => c.operation || c.type), ['ninebot/assistant/vehicles', 'list']);
  assert.equal(session.refreshSeconds, 30);
});

test('several vehicles require an explicit selection, never the first', async () => {
  const { session, hass, calls } = fixture();
  hass.callWS = async () => ({ data: { vehicles: [vehicle, { ...vehicle, vehicle_ref: 'vehicle-b' }] } });
  await session.load();
  assert.equal(session.vehicleRef, ''); assert.deepEqual(session.tasks, []); assert.deepEqual(calls, []);
});

test('prepare cannot approve; explicit approval consumes the bound in-memory credential', async () => {
  const { session, calls } = fixture();
  await session.load();
  assert.equal(await session.approve(), false);
  await session.prepare(task().task_id);
  assert.equal(session.refreshSeconds, 0);
  assert.equal(calls.filter((c) => c.operation === 'approve').length, 0);
  await session.approve();
  assert.equal(calls.at(-1).plan_hash, task().plan_hash);
  assert.equal(calls.at(-1).token, 'synthetic-confirmation-token');
  assert.equal(calls.at(-1).vehicle_ref, 'vehicle-a');
  assert.equal(session.prepared, null); assert.equal(session.tasks[0].state, 'queued');
  assert.equal(session.refreshSeconds, 10);
  assert.equal(await session.approve(), false);
});

test('cancel clears a prepared nonce and sends only the exact task identity', async () => {
  const { session, calls } = fixture(); await session.load(); await session.prepare(task().task_id);
  await session.cancel(task().task_id);
  assert.deepEqual(calls.at(-1), { type: 'ninebot/assistant/review', vehicle_ref: 'vehicle-a', operation: 'cancel', task_id: task().task_id });
  assert.equal(session.prepared, null); assert.equal(session.refreshSeconds, 0);
});

test('deselection clears private records and confirmation state', async () => {
  const { session } = fixture(); await session.load(); await session.prepare(task().task_id);
  await session.select('');
  assert.equal(session.prepared, null); assert.deepEqual(session.tasks, []); assert.equal(session.vehicleRef, '');
});

test('late preparation cannot restore a credential after unmount or identity replacement', async () => {
  const { session, hass } = fixture(); await session.load();
  let resolve;
  hass.callWS = () => new Promise((done) => { resolve = done; });
  const pending = session.prepare(task().task_id);
  assert.equal(session.busy, true);
  session.stop();
  resolve({ task: task(), token: 'synthetic-confirmation-token' });
  assert.equal(await pending, false);
  assert.equal(session.prepared, null); assert.deepEqual(session.vehicles, []);
});

test('only one request is in flight and a failed mutation is never retried automatically', async () => {
  const { session, hass, calls } = fixture(); await session.load(); await session.prepare(task().task_id);
  let reject;
  hass.callWS = (message) => { calls.push(message); return new Promise((_, done) => { reject = done; }); };
  const pending = session.approve();
  const count = calls.length;
  assert.equal(await session.refresh(), false); assert.equal(await session.approve(), false);
  reject({ code: 'retry_later', message: 'PRIVATE DO NOT DISPLAY' });
  await pending;
  assert.equal(calls.length, count); assert.equal(session.prepared, null);
  assert.equal(session.error, 'retry_later');
});

test('permission denial clears previously visible records and vehicle selection', async () => {
  const { session, hass } = fixture(); await session.load();
  hass.callWS = async () => { throw { code: 'not_authorized', message: 'PRIVATE' }; };
  await session.refresh();
  assert.equal(session.error, 'not_authorized'); assert.equal(session.vehicleRef, '');
  assert.deepEqual(session.tasks, []); assert.deepEqual(session.vehicles, []);
});

test('malformed confirmation or wrong target never becomes an approvable plan', async () => {
  for (const change of [{ vehicle_ref: 'other' }, { task_id: 'c'.repeat(32) }, { plan_hash: 'bad' }, { approval_expires_at: 'bad' }]) {
    const { session, hass } = fixture(); await session.load();
    hass.callWS = async () => ({ task: { ...task(), ...change }, token: 'synthetic-confirmation-token' });
    assert.equal(await session.prepare(task().task_id), false);
    assert.equal(session.prepared, null);
  }
});

test('local expiry prevents sending the expired confirmation', async () => {
  const { session, calls } = fixture(); await session.load(); await session.prepare(task().task_id);
  session.prepared.task.approval_expires_at = '2000-01-01T00:00:00Z';
  const count = calls.length; assert.equal(await session.approve(), false);
  assert.equal(calls.length, count); assert.equal(session.error, 'approval_expired');
});

test('both primary languages cover all review states and safe errors', () => {
  for (const key of ['states', 'errors', 'reasons']) assert.deepEqual(Object.keys(TEXT.en[key]), Object.keys(TEXT.zh[key]));
});
