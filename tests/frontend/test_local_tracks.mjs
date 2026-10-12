import assert from 'node:assert/strict';
import { test } from 'node:test';
import { LocalTracks, trackKey, trackSegments }
  from '../../custom_components/ninebot/frontend/local-tracks.js';

const ride = { ride_id: 'synthetic-ride', query_month: '202609' };
function chunk(offset, total = 1025, revision = 1, points = undefined) {
  return { source_mode: 'ride_archive', query_month: ride.query_month,
    detail_context: { source_mode: 'ride_archive', archive_revision: revision,
      scalar_revision: 1, geometry_received_at: '2026-09-26T00:00:00Z' },
    ride: { ride_id: ride.ride_id, track_summary: { offset, limit: 512, total_known: total,
      next_offset: offset + 512 < total ? offset + 512 : null, geometry_kind: 'sampled_unverified' },
    track: points ?? Array.from({ length: Math.min(512, total - offset) }, (_, i) => ({
      sequence: offset + i, latitude: 30, longitude: 120, speed_raw: i, distance_delta_raw: 1 })) } };
}

test('local chunks preserve all requested source rows without cloud fallback', async () => {
  const calls = [];
  const reader = new LocalTracks(async (service, data) => {
    calls.push({ service, data }); return chunk(data.point_offset);
  });
  await reader.read('device-a', ride);
  const track = reader.tracks.get(trackKey('device-a', ride));
  assert.equal(track.points.length, 1025); assert.equal(track.complete, true);
  assert.equal(calls.length, 3);
  for (const { service, data } of calls) {
    assert.equal(service, 'get_trip_detail'); assert.equal(data.local_only, true);
    assert.equal(data.include_track, true); assert.ok(!('refresh' in data));
  }
  await reader.read('device-a', ride); assert.equal(calls.length, 3);
});

test('four calls per interaction, source-offset continuation and rendering memory bound', async () => {
  const calls = [];
  const reader = new LocalTracks(async (_, data) => {
    calls.push(data.point_offset); return chunk(data.point_offset, 10000);
  });
  await reader.read('device-a', ride);
  assert.deepEqual(calls, [0, 512, 1024, 1536]);
  await reader.read('device-a', ride);
  assert.ok(reader.tracks.get(trackKey('device-a', ride)).points.length <= 4096);
  assert.equal(reader.tracks.get(trackKey('device-a', ride)).complete, false);
  assert.equal(reader.tracks.get(trackKey('device-a', ride)).capped, true);
  for (let i = 0; i < 3; i++) await reader.read('device-a', ride);
  const saved = reader.tracks.get(trackKey('device-a', ride));
  assert.equal(calls.length, 20); assert.equal(saved.valid_read, 10000);
  assert.equal(saved.complete, true); assert.ok(saved.points.length <= 4096);
  assert.equal(saved.points[0].sequence, 0); assert.equal(saved.points.at(-1).sequence, 9999);
  assert.equal(trackSegments(saved.points, saved.kind).length, 1);
  await reader.read('device-a', ride); assert.equal(calls.length, 20);
});

test('invalid-only source chunks advance and gaps/simplified points never connect', async () => {
  const reader = new LocalTracks(async (_, data) => chunk(data.point_offset, 1025, 1,
    data.point_offset === 0 ? [] : undefined));
  await reader.read('device-a', ride);
  const result = reader.tracks.get(trackKey('device-a', ride));
  assert.equal(result.points[0].sequence, 512); assert.equal(result.complete, true);
  const points = [0, 1, 4, 5].map(sequence => ({ sequence }));
  assert.deepEqual(trackSegments(points, 'sampled_unverified').map(s => s.length), [2, 2]);
  assert.deepEqual(trackSegments(points, 'server_simplified').map(s => s.length), [1, 1, 1, 1]);
});

test('mixed revision, malformed scope and invalid geometry discard the selected track', async () => {
  for (const mutate of [
    response => { response.detail_context.archive_revision++; },
    response => { response.ride.ride_id = 'another-ride'; },
    response => { response.source_mode = 'ninecli'; },
    response => { response.ride.track[0].latitude = 91; },
    response => { response.ride.track_summary.next_offset = 513; },
  ]) {
    const reader = new LocalTracks(async (_, data) => {
      const response = chunk(data.point_offset); if (data.point_offset) mutate(response); return response;
    });
    await reader.read('device-a', ride);
    assert.equal(reader.tracks.size, 0); assert.ok(reader.error);
  }
});

test('late GPS responses are ignored after reset and duplicate reads do not queue', async () => {
  let resolve, calls = 0;
  const reader = new LocalTracks(() => { calls++; return new Promise(done => { resolve = done; }); });
  const pending = reader.read('device-a', ride);
  await reader.read('device-b', ride); assert.equal(calls, 1);
  reader.clear(); resolve(chunk(0)); await pending;
  assert.equal(reader.tracks.size, 0); assert.equal(reader.busy, false);
});

test('devices isolate identical ride IDs; permission failure clears every private track', async () => {
  let denied = false;
  const reader = new LocalTracks(async () => {
    if (denied) throw { code: 'unauthorized' };
    return chunk(0, 1);
  });
  await reader.read('device-a', ride); await reader.read('device-b', ride);
  assert.equal(reader.tracks.size, 2);
  denied = true; await reader.read('device-c', ride);
  assert.equal(reader.tracks.size, 0); assert.ok(reader.error);
});

test('only five selected route projections survive and no extra raw fields are retained', async () => {
  const reader = new LocalTracks(async () => {
    const response = chunk(0, 1); response.ride.track[0].password = 'discard-me'; return response;
  });
  for (let i = 0; i < 8; i++) await reader.read(`device-${i}`, ride);
  assert.equal(reader.tracks.size, 5); assert.ok(!reader.tracks.has(trackKey('device-0', ride)));
  assert.ok(!JSON.stringify([...reader.tracks]).includes('discard-me'));
});
