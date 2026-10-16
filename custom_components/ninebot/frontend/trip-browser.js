import { invokeHistory } from './history-network.js';

/** Bounded, account-scoped local pages and explicitly requested cloud details. */

export function businessDates(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return [`${values.year}-${values.month}-01`, `${values.year}-${values.month}-${values.day}`];
}

/** Inclusive Shanghai business-day windows; never infer dates from ride samples. */
export function recentDates(days, now = new Date()) {
  const end = businessDates(now)[1];
  const start = new Date(`${end}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - days + 1);
  return [start.toISOString().slice(0, 10), end];
}

export function monthDates(start, offset) {
  const month = new Date(`${start.slice(0, 7)}-01T00:00:00Z`);
  month.setUTCMonth(month.getUTCMonth() + offset);
  const end = new Date(month); end.setUTCMonth(end.getUTCMonth() + 1); end.setUTCDate(0);
  return [month.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
}

export function digits(value, fallback = 0) {
  return Number.isInteger(value) ? Math.max(0, Math.min(6, value)) : fallback;
}

const SCALARS = ['ride_id', 'detail_id', 'query_month', 'source', 'start_time', 'end_time',
  'distance_m', 'duration_s', 'duration_h', 'max_speed_m_s', 'average_speed_m_s', 'energy_wh',
  'used_electricity_raw', 'energy_intensity_wh_per_km', 'received_at', 'parser_contract'];
export function safeRide(ride) {
  const result = Object.fromEntries(SCALARS.map(key => [key, ride[key] ?? null]));
  result.detail_saved = typeof ride.detail_saved === 'boolean' ? ride.detail_saved : null;
  result.precision = Object.fromEntries(['mileages', 'duration', 'speed', 'ec'].map(
    key => [key, digits(ride.precision?.[key], key === 'mileages' ? 2 : 0)]));
  result.field_provenance = Object.fromEntries(['started_at', 'ended_at', 'distance_m',
    'duration_s', 'server_max_speed_m_s', 'energy_raw'].map(
    key => [key, String(ride.field_provenance?.[key] ?? '').slice(0, 128)]));
  result.speed_samples = (Array.isArray(ride.speed_samples) ? ride.speed_samples : [])
    .slice(0, 500).filter(point => Number.isFinite(point.sequence) && Number.isFinite(point.speed_raw))
    .map(point => ({ sequence: point.sequence, speed_raw: point.speed_raw }));
  result.distance_samples = (Array.isArray(ride.distance_samples) ? ride.distance_samples : [])
    .slice(0, 500).filter(point => Number.isFinite(point.sequence) && Number.isFinite(point.distance_delta_raw))
    .map(point => ({ sequence: point.sequence, distance_delta_raw: point.distance_delta_raw }));
  result.track_summary = { total_known: Number.isInteger(ride.track_summary?.total_known) ? ride.track_summary.total_known : null,
    returned: Number.isInteger(ride.track_summary?.returned) ? ride.track_summary.returned : null,
    truncated: ride.track_summary?.truncated === true };
  return result; // Never retain tracks, raw payloads, signed URLs or extra fields.
}

export function samplePolyline(samples) {
  if (samples.length < 2) return null;
  const xs = samples.map(p => p.sequence), ys = samples.map(p => p.speed_raw);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  if (!Number.isFinite(maxX - minX) || !Number.isFinite(maxY - minY)) return null;
  return samples.map(p => `${8 + (p.sequence - minX) / (maxX - minX || 1) * 284},${
    92 - (p.speed_raw - minY) / (maxY - minY || 1) * 84}`).join(' ');
}

export function samplePaths(samples) {
  if (samples.length < 2) return [];
  const xs = samples.map(p => p.sequence), ys = samples.map(p => p.speed_raw);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  if (!Number.isFinite(maxX - minX) || !Number.isFinite(maxY - minY)) return [];
  const segments = [];
  for (const point of samples) {
    const last = segments.at(-1)?.at(-1);
    const continuous = last && (point.segment !== undefined && last.segment !== undefined ?
      point.segment === last.segment : point.sequence === last.sequence + 1);
    if (!continuous) segments.push([]);
    segments.at(-1).push(point);
  }
  return segments.filter(segment => segment.length > 1).map(segment => segment.map(p =>
    `${8 + (p.sequence - minX) / (maxX - minX || 1) * 284},${92 - (p.speed_raw - minY) / (maxY - minY || 1) * 84}`).join(' '));
}

export class TripBrowser {
  constructor(changed = () => {}) {
    this.changed = changed; this.epoch = 0; this.active = false; this.details = new Map();
  }
  reset() {
    this.epoch++; this.busy = false; this.page = null; this.details.clear(); this.error = null;
    this.device = null; this.pageNumber = 0; this.awaitApply = false; this.cleared?.(); this.changed();
  }
  configure(config) {
    if (!config || (!!config.entity === !!config.device_id) ||
        (config.entity && !/^calendar\.[a-z0-9_]+$/.test(config.entity)) ||
        (config.device_id && (typeof config.device_id !== 'string' || !config.device_id.trim())) ||
        (config.limit !== undefined && (!Number.isInteger(config.limit) || config.limit < 1 || config.limit > 100))) {
      throw new Error('Choose one Ninebot calendar entity or vehicle device_id; limit must be 1–100.');
    }
    const defaults = businessDates();
    const start = config.start_date ?? defaults[0], end = config.end_date ?? defaults[1];
    if (![start, end].every(value => typeof value === 'string' && /^20\d{2}-\d{2}-\d{2}$/.test(value))) {
      throw new Error('Use YYYY-MM-DD dates.');
    }
    this.config = { ...config, limit: config.limit ?? 20 }; this.start = start; this.end = end;
    this.reset(); this.startIfReady();
  }
  setHass(hass) {
    const changed = this.hass?.user?.id !== hass?.user?.id || this.hass?.connection !== hass?.connection;
    const wasConnected = this.hass?.connected !== false;
    this.hass = hass;
    if (changed || (wasConnected && hass?.connected === false)) this.reset();
    this.startIfReady();
  }
  connect() { this.active = true; this.startIfReady(); }
  disconnect() { this.active = false; this.reset(); }
  startIfReady() {
    if (this.active && this.config && this.hass?.user?.id && this.hass.connected !== false &&
        this.hass.callWS && !this.page && !this.busy && !this.error && !this.awaitApply) void this.read();
  }
  range(kind) {
    if (this.busy) return;
    const dates = kind === 'previous_month' || kind === 'next_month' ?
      monthDates(this.start, kind === 'previous_month' ? -1 : 1) :
      recentDates(kind === 'recent' ? 1830 : kind === 'last_7' ? 7 : 30);
    this.dates(...dates); void this.read();
  }
  dates(start, end) { this.start = start; this.end = end; this.reset(); this.awaitApply = true; }
  current(epoch) { return this.active && epoch === this.epoch; }
  async invoke(service, data) { const epoch = this.epoch; return invokeHistory(this.hass, service, data, () => this.current(epoch)); }
  async read(cursor = null) {
    if (!this.active || this.busy || !this.config || !this.hass?.user?.id || this.hass.connected === false) return;
    if (cursor && !this.page) return;
    const epoch = this.epoch;
    this.busy = true; this.error = null; this.awaitApply = false; this.changed();
    try {
      const device = this.device ?? this.config.device_id ?? (await this.hass.callWS({
        type: 'config/entity_registry/get', entity_id: this.config.entity,
      })).device_id;
      if (!this.current(epoch)) return;
      if (!device) throw new Error('missing_device');
      this.device = device;
      const response = await this.invoke('get_recorded_trips', { device_id: device,
        start_date: this.start, end_date: this.end, limit: this.config.limit,
        ...(cursor ? { cursor } : {}) });
      if (!this.current(epoch)) return;
      if (response?.source_mode !== 'ride_archive' || !Array.isArray(response.rides) ||
          response.rides.length > this.config.limit) throw new Error('invalid_response');
      this.page = { rides: response.rides.map(safeRide), next_cursor: response.next_cursor,
        revision: response.revision, coverage: response.coverage, range: response.range };
      this.details.clear(); this.pageNumber = cursor ? this.pageNumber + 1 : 1;
    } catch (error) {
      if (this.current(epoch)) {
        this.error = error?.code === 'unauthorized' ? 'denied' :
          error?.translation_key === 'history_cursor' ? 'cursor_error' : 'read_error';
        if (cursor && this.page) this.page.next_cursor = null;
      }
    } finally { if (this.current(epoch)) { this.busy = false; this.changed(); } }
  }
  async detail(ride) {
    if (!this.active || this.busy || !this.page?.rides.includes(ride) || !ride.ride_id || !ride.detail_id) return;
    const epoch = this.epoch; this.busy = true; this.error = null; this.changed();
    try {
      const response = await this.invoke('get_trip_detail', { device_id: this.device,
        ride_id: ride.ride_id, query_month: ride.query_month, include_track: false, max_points: 500 });
      if (!this.current(epoch)) return;
      if (!response?.ride || response.ride.ride_id !== ride.ride_id ||
          response.query_month !== ride.query_month) throw new Error('invalid_response');
      this.details.delete(ride.ride_id);
      while (this.details.size >= 5) this.details.delete(this.details.keys().next().value);
      this.details.set(ride.ride_id, { ...safeRide(response.ride), received_at: response.received_at,
        local_partial: response.detail_context?.local_integrity?.partial === true });
      // Keep the token; the backend explicitly rejects a changed archive revision.
    } catch (error) {
      if (this.current(epoch)) {
        this.error = error?.code === 'unauthorized' ? 'denied' : 'detail_error';
        if (this.error === 'denied') { this.page = null; this.details.clear(); }
      }
    } finally { if (this.current(epoch)) { this.busy = false; this.changed(); } }
  }
}

