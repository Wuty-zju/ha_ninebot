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
    partial: context.local_integrity?.partial === true,
    fingerprint: JSON.stringify([context.archive_revision, context.scalar_revision,
      context.geometry_received_at, total, summary.geometry_kind,
      context.local_integrity?.geometry_evidence_source ?? 'selected_geometry',
      context.local_integrity?.latest_payload_available ?? true,
      context.local_integrity?.selected_geometry_available ?? true]) };
}

/** Gaps remain separate: never draw a road across rejected/missing source rows. */
export function trackSegments(points, kind) {
  if (kind !== 'sampled_unverified') return points.map(point => [point]);
  const segments = [];
  for (const point of points) {
    const segment = segments.at(-1);
    const last = segment?.at(-1);
    const continuous = last && (point.segment !== undefined && last.segment !== undefined ?
      point.segment === last.segment : point.sequence === last.sequence + 1);
    if (!continuous) segments.push([point]);
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
  remove(key) {
    if (!this.busy) { this.tracks.delete(key); this.changed(); }
  }
  async read(device, ride) {
    if (this.busy || typeof device !== 'string' || !device || !ride?.ride_id ||
        !/^20\d{4}$/.test(ride.query_month)) return;
    const key = trackKey(device, ride), prior = this.tracks.get(key);
    if (prior?.complete) return;
    const epoch = this.epoch;
    let item = prior ? { ...prior, points: [...prior.points] } :
      { points: [], next: 0, fingerprint: null, complete: false, capped: false, bounds: null, valid_read: 0 };
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
        let last = item.points.at(-1), bounds = item.bounds;
        const annotated = chunk.points.map(point => {
          const gap = last && point.sequence !== last.sequence + 1;
          const result = { ...point, segment: (last?.segment ?? 0) + (gap ? 1 : 0),
            speed_segment: (last?.speed_segment ?? 0) + (gap || point.speed_raw === null ? 1 : 0),
            distance_segment: (last?.distance_segment ?? 0) + (gap || point.distance_delta_raw === null ? 1 : 0) };
          bounds = bounds ? [Math.min(bounds[0], point.latitude), Math.min(bounds[1], point.longitude),
            Math.max(bounds[2], point.latitude), Math.max(bounds[3], point.longitude)] :
            [point.latitude, point.longitude, point.latitude, point.longitude];
          last = result; return result;
        });
        const points = [...item.points, ...annotated];
        const step = Math.max(1, Math.ceil(points.length / (MAX_POINTS - 2)));
        item = { ...item, fingerprint: chunk.fingerprint, total: chunk.total, kind: chunk.kind,
          points: step === 1 ? points : points.filter((_, i) => i === 0 || i === points.length - 1 || i % step === 0),
          next: chunk.next, complete: chunk.next === null, bounds,
          partial: item.partial || chunk.partial,
          valid_read: item.valid_read + chunk.points.length, capped: item.capped || step > 1 };
        if (item.complete) break;
      }
      this.tracks.delete(key);
      while (this.tracks.size >= MAX_TRACKS) this.tracks.delete(this.tracks.keys().next().value);
      this.tracks.set(key, { ...item, key, label: String(ride.vehicle_label ?? device).slice(0, 128), ride_id: ride.ride_id });
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
