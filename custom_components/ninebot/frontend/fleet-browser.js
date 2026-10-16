/** Compose existing scoped queries without cross-account data or cloud fanout. */
import { TripBrowser, safeRide } from './trip-browser.js';
import { trackKey } from './local-tracks.js';

export function vehicleTargets(config) {
  if (!config?.vehicles) return [{ entity: config?.entity, device_id: config?.device_id }];
  if (config.entity || config.device_id || !Array.isArray(config.vehicles) ||
      config.vehicles.length < 1 || config.vehicles.length > 5) throw new Error('Choose 1–5 vehicles.');
  const targets = config.vehicles.map(target => typeof target === 'string' ? { entity: target } : target);
  const keys = new Set();
  for (const target of targets) {
    if (!target || (!!target.entity === !!target.device_id) ||
        (target.entity && !/^calendar\.[a-z0-9_]+$/.test(target.entity)) ||
        (target.device_id && (typeof target.device_id !== 'string' || !target.device_id.trim())) ||
        (target.name !== undefined && (typeof target.name !== 'string' || target.name.length > 128))) {
      throw new Error('Choose one calendar entity or device_id per vehicle.');
    }
    const key = target.entity ?? target.device_id;
    if (keys.has(key)) throw new Error('Duplicate vehicle.');
    keys.add(key);
  }
  return targets.map(target => ({ ...target }));
}

export function nativeCards(targets, statistics = []) {
  if (!Array.isArray(statistics) || statistics.length > 20 || statistics.some(id =>
    typeof id !== 'string' || !/^(sensor\.[a-z0-9_]+|ninebot:[a-z0-9_]+)$/.test(id))) {
    throw new Error('Choose at most 20 sensor/statistic IDs.');
  }
  const calendars = targets.map(target => target.entity).filter(Boolean);
  return { type: 'vertical-stack', cards: [
    ...(calendars.length ? [{ type: 'calendar', entities: calendars, initial_view: 'dayGridMonth' }] : []),
    ...statistics.map(id => {
      // This is our exported statistic ID contract, not an upstream-field guess.
      const monthly = /^ninebot:.*_month_(distance_km|energy_wh|ride_count|duration_s)$/.test(id);
      return { type: 'statistics-graph', entities: [id], period: monthly ? 'month' : 'day',
        days_to_show: monthly ? 365 : 30, chart_type: 'bar', stat_types: ['change'] };
    }),
  ] };
}

/** Same direction as the server keyset; device is a deterministic cross-stream tie. */
export function compareRides(a, b) {
  const time = Date.parse(b.start_time) - Date.parse(a.start_time);
  if (time) return time;
  const order = (a, b) => a === b ? 0 : a < b ? -1 : 1;
  return order(a.device_id, b.device_id) || -order(a.ride_id ?? '', b.ride_id ?? '');
}

export class FleetBrowser extends TripBrowser {
  constructor(changed = () => {}, cleared = () => {}) {
    super(changed); this.cleared = cleared;
  }
  configure(config) {
    this.targets = vehicleTargets(config);
    nativeCards(this.targets, config.statistics_entities ?? []);
    this.selected = new Set(this.targets.map((_, index) => index));
    super.configure({ ...config, ...this.targets[0] });
  }
  reset() { this.resolved = new Map(); this.streams = []; super.reset(); }
  select(index, enabled) {
    if (this.busy || !Number.isInteger(index) || !this.targets[index]) return;
    if (enabled) this.selected.add(index); else this.selected.delete(index);
    this.reset(); this.startIfReady();
  }
  async read(cursor = null) {
    if (!this.active || this.busy || !this.config || !this.hass?.user?.id ||
        this.hass.connected === false || !this.selected?.size ||
        (cursor && cursor !== this.page?.next_cursor)) return;
    const epoch = this.epoch;
    let failed = false;
    // Stage buffers atomically: failure must not consume half of a fleet page.
    const streams = cursor ? this.streams.map(stream => ({ ...stream, rows: [...stream.rows] })) : [];
    this.busy = true; this.error = null; this.awaitApply = false; this.changed();
    try {
      if (!cursor) {
        const resolved = new Set();
        for (const index of this.selected) {
          const target = this.targets[index];
          const device = this.resolved.get(index) ?? target.device_id ?? (await this.hass.callWS({
            type: 'config/entity_registry/get', entity_id: target.entity })).device_id;
          if (!this.current(epoch)) return;
          if (!device || resolved.has(device)) throw new Error('invalid_device');
          this.resolved.set(index, device); resolved.add(device);
          const name = target.name ?? this.hass.states?.[target.entity]?.attributes?.friendly_name ?? device;
          streams.push({ device, name: String(name).slice(0, 128), rows: [], cursor: null,
            loaded: false, coverage: null, revision: null });
        }
      }
      const fill = async stream => {
        if (stream.rows.length || (stream.loaded && !stream.cursor)) return;
        const response = await this.invoke('get_recorded_trips', { device_id: stream.device,
          start_date: this.start, end_date: this.end, limit: this.config.limit, sort: 'desc',
          ...(stream.cursor ? { cursor: stream.cursor } : {}) });
        if (!this.current(epoch)) return;
        if (response?.source_mode !== 'ride_archive' || response.sort !== 'desc' ||
            !Array.isArray(response.rides) || response.rides.length > this.config.limit ||
            (!response.rides.length && response.next_cursor)) throw new Error('invalid_response');
        if (response.selection_version != null && (typeof response.selection_version !== 'string' ||
            !/^[a-f0-9]{32}:[0-9]{1,20}$/.test(response.selection_version))) throw new Error('invalid_response');
        if (stream.loaded && stream.revision !== (response.selection_version ?? response.revision)) throw { translation_key: 'history_cursor' };
        const rows = response.rides.map(ride => ({ ...safeRide(ride), device_id: stream.device,
          vehicle_label: stream.name }));
        for (let i = 0; i < rows.length; i++) {
          if (!Number.isFinite(Date.parse(rows[i].start_time)) ||
              (i && compareRides(rows[i - 1], rows[i]) >= 0) ||
              (!i && stream.last && compareRides(stream.last, rows[i]) >= 0)) {
            throw new Error('invalid_order');
          }
        }
        stream.last = rows.at(-1) ?? stream.last;
        stream.rows = rows; stream.cursor = response.next_cursor;
        stream.coverage = response.coverage; stream.revision = response.selection_version ?? response.revision; stream.loaded = true;
      };
      // At most two local reads in flight. Each stream retains <= one 100-row page.
      let index = 0;
      await Promise.all(Array.from({ length: Math.min(2, streams.length) }, async () => {
        while (index < streams.length) {
          if (failed || !this.current(epoch)) return;
          await fill(streams[index++]);
        }
      }));
      if (!this.current(epoch)) return;
      const rides = [];
      while (rides.length < this.config.limit) {
        const available = streams.filter(stream => stream.rows.length);
        if (!available.length) break;
        const selected = available.reduce((best, stream) =>
          compareRides(stream.rows[0], best.rows[0]) < 0 ? stream : best);
        rides.push(selected.rows.shift());
        // A missing head could precede every other vehicle, so refill before merging.
        if (rides.length < this.config.limit) await fill(selected);
        if (!this.current(epoch)) return;
      }
      const coverage = {};
      for (const field of ['missing_months', 'partial_months', 'unknown_months', 'enumerated_months']) {
        if (field === 'enumerated_months' && streams.some(stream =>
          !Array.isArray(stream.coverage?.[field]))) continue;
        coverage[field] = streams.flatMap(stream => (stream.coverage?.[field] ?? [])
          .map(month => `${stream.device}:${month}`));
      }
      const continuation = Object.fromEntries(streams.filter(stream => stream.rows.length || stream.cursor)
        .map(stream => [stream.device, stream.cursor ?? true]));
      this.streams = streams;
      this.page = { rides, coverage, next_cursor: Object.keys(continuation).length ? continuation : null };
      this.details.clear(); this.pageNumber = cursor ? this.pageNumber + 1 : 1;
    } catch (error) {
      failed = true;
      if (this.current(epoch)) {
        this.cleared?.(); this.details.clear(); this.streams = [];
        this.error = error?.code === 'unauthorized' ? 'denied' :
          error?.translation_key === 'history_cursor' ? 'cursor_error' : 'read_error';
        if (cursor && this.page && this.error === 'cursor_error') this.page.next_cursor = null;
        else this.page = null;
      }
    } finally { if (this.current(epoch)) { this.busy = false; this.changed(); } }
  }
  async detail(ride) {
    if (!this.active || this.busy || !this.page?.rides.includes(ride) || !ride.ride_id || !ride.detail_id) return;
    const epoch = this.epoch; this.busy = true; this.error = null; this.changed();
    try {
      const response = await this.invoke('get_trip_detail', { device_id: ride.device_id,
        ride_id: ride.ride_id, query_month: ride.query_month, include_track: false, max_points: 500 });
      if (!this.current(epoch)) return;
      if (response?.ride?.ride_id !== ride.ride_id || response.query_month !== ride.query_month) {
        throw new Error('invalid_response');
      }
      const key = trackKey(ride.device_id, ride);
      this.details.delete(key);
      while (this.details.size >= 5) this.details.delete(this.details.keys().next().value);
      this.details.set(key, { ...safeRide(response.ride), received_at: response.received_at,
        source_mode: response.source_mode,
        local_partial: response.detail_context?.local_integrity?.partial === true,
        device_id: ride.device_id, vehicle_label: ride.vehicle_label });
    } catch (error) {
      if (this.current(epoch)) {
        this.cleared?.(); this.details.clear();
        this.error = error?.code === 'unauthorized' ? 'denied' : 'detail_error';
        if (this.error === 'denied') { this.page = null; this.streams = []; }
      }
    } finally { if (this.current(epoch)) { this.busy = false; this.changed(); } }
  }
}
