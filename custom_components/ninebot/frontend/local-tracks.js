/** Explicit bounded local archive reads; no cloud, storage or CRS inference. */
const CHUNK = 512;
const MAX_TRACKS = 5;
const MAX_POINTS = 4096;
const KINDS = new Set(['unknown', 'empty', 'invalid', 'single_point',
  'server_simplified', 'sampled_unverified']);

export function trackKey(device, ride) {
  return JSON.stringify([device, ride.ride_id, ride.query_month]);
}

function checkedChunk(response, ride, offset) {
  const value = response?.ride, summary = value?.track_summary, context = response?.detail_context;
  if (response?.source_mode !== 'ride_archive' || context?.source_mode !== 'ride_archive' ||
      response.query_month !== ride.query_month || value?.ride_id !== ride.ride_id ||
      !Array.isArray(value.track) || value.track.length > CHUNK ||
      summary?.offset !== offset || summary?.limit !== CHUNK ||
      !KINDS.has(summary?.geometry_kind) ||
      !Number.isInteger(context?.archive_revision) || context.archive_revision < 1 ||
      !Number.isInteger(context?.scalar_revision) || context.scalar_revision < 1) {
    throw new Error('invalid_track_response');
  }
  const total = summary.total_known, next = summary.next_offset;
  if ((total !== null && (!Number.isInteger(total) || total < 0 || total > 1048576)) ||
      (next !== null && (next !== offset + CHUNK || total === null || next >= total)) ||
      (total !== null && ((offset + CHUNK < total) !== (next !== null)))) {
    throw new Error('invalid_track_response');
  }
  let previous = offset - 1;
  const points = value.track.map(point => {
    if (!Number.isInteger(point.sequence) || point.sequence <= previous ||
        point.sequence < offset || point.sequence >= offset + CHUNK ||
        (total !== null && point.sequence >= total) ||
        !Number.isFinite(point.latitude) || Math.abs(point.latitude) > 90 ||
        !Number.isFinite(point.longitude) || Math.abs(point.longitude) > 180) {
      throw new Error('invalid_track_response');
    }
    previous = point.sequence;
    return { sequence: point.sequence, latitude: point.latitude, longitude: point.longitude,
      speed_raw: Number.isFinite(point.speed_raw) ? point.speed_raw : null,
      distance_delta_raw: Number.isFinite(point.distance_delta_raw) ? point.distance_delta_raw : null };
  });
  return { points, next, total, kind: summary.geometry_kind,
    fingerprint: JSON.stringify([context.archive_revision, context.scalar_revision,
      context.geometry_received_at, total, summary.geometry_kind]) };
}

/** Gaps remain separate: never draw a road across rejected/missing source rows. */
export function trackSegments(points, kind) {
  if (kind !== 'sampled_unverified') return points.map(point => [point]);
  const segments = [];
  for (const point of points) {
    const segment = segments.at(-1);
    if (!segment || point.sequence !== segment.at(-1).sequence + 1) segments.push([point]);
    else segment.push(point);
  }
  return segments;
}

export class LocalTracks {
  constructor(invoke, changed = () => {}) {
    this.invoke = invoke; this.changed = changed; this.epoch = 0; this.tracks = new Map();
    this.busy = false; this.error = null;
  }
  clear() {
    this.epoch++; this.busy = false; this.error = null; this.tracks.clear(); this.changed();
  }
  async read(device, ride) {
    if (this.busy || typeof device !== 'string' || !device || !ride?.ride_id ||
        !/^20\d{4}$/.test(ride.query_month)) return;
    const key = trackKey(device, ride), prior = this.tracks.get(key);
    if (prior?.complete || prior?.capped) return;
    const epoch = this.epoch;
    let item = prior ? { ...prior, points: [...prior.points] } :
      { points: [], next: 0, fingerprint: null, complete: false, capped: false };
    this.busy = true; this.error = null; this.changed();
    try {
      // One explicit interaction, at most four sequential service calls.
      // Invalid-only chunks advance by source offset rather than valid count.
      for (let count = 0; count < 4; count++) {
        const response = await this.invoke('get_trip_detail', { device_id: device,
          ride_id: ride.ride_id, query_month: ride.query_month, local_only: true,
          include_track: true, point_offset: item.next, max_points: CHUNK });
        if (this.epoch !== epoch) return;
        const chunk = checkedChunk(response, ride, item.next);
        if (item.fingerprint !== null && item.fingerprint !== chunk.fingerprint) {
          throw new Error('track_revision_changed');
        }
        const points = [...item.points, ...chunk.points];
        item = { ...item, fingerprint: chunk.fingerprint, total: chunk.total, kind: chunk.kind,
          points: points.slice(0, MAX_POINTS), next: chunk.next, complete: chunk.next === null,
          capped: points.length >= MAX_POINTS };
        if (item.complete || item.capped) break;
      }
      this.tracks.delete(key);
      while (this.tracks.size >= MAX_TRACKS) this.tracks.delete(this.tracks.keys().next().value);
      this.tracks.set(key, item);
    } catch (error) {
      if (this.epoch === epoch) {
        // HA permission failures can also be translated device/coordinate
        // errors. Drop every private projection rather than classifying them.
        this.tracks.clear();
        this.error = error?.translation_key ?? error?.message ?? 'track_read_failed';
      }
    } finally {
      if (this.epoch === epoch) { this.busy = false; this.changed(); }
    }
  }
}
