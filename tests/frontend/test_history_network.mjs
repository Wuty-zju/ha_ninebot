import assert from 'node:assert/strict';
import { test } from 'node:test';
import { invokeHistory, watchHistory } from '../../custom_components/ninebot/frontend/history-network.js';
const tick = () => new Promise(resolve => setImmediate(resolve));
const data = { device_id: 'a', ride_id: 'ride', local_only: true, point_offset: 0 };

test('only pure local identical in-flight details share one request and never cache resolved GPS', async () => {
  let calls = 0, resolve;
  const hass = { user: { id: 'u' }, connection: {}, callWS: () => { calls++; return new Promise(done => { resolve = done; }); } };
  const a = invokeHistory(hass, 'get_trip_detail', data), b = invokeHistory(hass, 'get_trip_detail', { ...data });
  await tick(); assert.equal(calls, 1); resolve({ response: { sample: true } });
  assert.deepEqual(await a, await b);
  const c = invokeHistory(hass, 'get_trip_detail', data); await tick(); assert.equal(calls, 2); resolve({ response: {} }); await c;
  hass.callWS = async () => { calls++; return { response: { nonce: calls } }; };
  const pending = await Promise.all([invokeHistory(hass, 'get_recorded_trips', { cursor: 'single-use' }), invokeHistory(hass, 'get_recorded_trips', { cursor: 'single-use' })]);
  assert.notEqual(pending[0].nonce, pending[1].nonce);
  assert.equal(calls, 4); // These intentionally do not share consumed tokens.
});

test('different user/connection/parameters never join local private reads', async () => {
  let calls = 0;
  const base = { user: { id: 'u' }, connection: {}, callWS: async () => { calls++; return { response: {} }; } };
  await Promise.all([invokeHistory(base, 'get_trip_detail', data), invokeHistory({ ...base, user: { id: 'other' } }, 'get_trip_detail', data),
    invokeHistory({ ...base, connection: {} }, 'get_trip_detail', data), invokeHistory(base, 'get_trip_detail', { ...data, point_offset: 512 })]);
  assert.equal(calls, 4);
});

test('shared subscriptions validate metadata, count references and cancel late acknowledgements', async () => {
  let callback, calls = 0, stopped = 0, ack;
  const connection = { subscribeMessage(fn) { calls++; callback = fn; return new Promise(resolve => { ack = () => resolve(() => { stopped++; }); }); } };
  const hass = { user: { id: 'u' }, connection }, one = [], two = [];
  const a = watchHistory(hass, ['a'], event => one.push(event)), b = watchHistory(hass, ['a'], event => two.push(event));
  await tick(); assert.equal(calls, 1);
  callback({ schema_version: 1, type: 'changed', vehicles: [{ device_id: 'a', selection_version: 'a'.repeat(32) + ':1', raw: 'must-not-retain' }] });
  assert.equal(one.length, 1); assert.equal(two.length, 1); assert.ok(!JSON.stringify(one).includes('must-not-retain'));
  a(); assert.equal(stopped, 0); b(); ack(); await tick(); assert.equal(stopped, 1);
  callback({ schema_version: 1, type: 'changed', vehicles: [] }); assert.equal(one.length, 1);
});

test('old unsupported backend does not poll/retry and malformed scopes fail closed', async () => {
  let calls = 0;
  const unsupported = [], stop = watchHistory({ user: { id: 'u' }, connection: { subscribeMessage() {
    calls++; return Promise.reject({ code: 'unknown_command' });
  } } }, ['a'], event => unsupported.push(event));
  await tick(); assert.equal(calls, 1); assert.equal(unsupported[0].type, 'unsupported'); stop();
  const events = []; let callback;
  const release = watchHistory({ user: { id: 'u' }, connection: { async subscribeMessage(fn) { callback = fn; return () => {}; } } }, ['a'], event => events.push(event));
  await tick(); callback({ schema_version: 1, type: 'changed', vehicles: [{ device_id: 'other', selection_version: 'a'.repeat(32) + ':1' }] });
  assert.equal(events[0].type, 'denied'); release();
});


test('only transient local detail failures retry once; authorization never retries', async () => {
  for (const code of ['busy', 'unauthorized']) {
    let calls = 0;
    const hass = { user: { id: 'u' }, connection: {}, callWS: async () => {
      calls++; if (calls === 1) throw { code }; return { response: { ok: true } };
    } };
    const promise = invokeHistory(hass, 'get_trip_detail', data);
    if (code === 'busy') { assert.equal((await promise).ok, true); assert.equal(calls, 2); }
    else { await assert.rejects(promise); assert.equal(calls, 1); }
  }
});
