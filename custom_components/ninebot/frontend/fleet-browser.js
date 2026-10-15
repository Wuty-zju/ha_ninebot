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

export class FleetBrowser extends TripBrowser {
  constructor(changed = () => {}, cleared = () => {}) {
    super(changed); this.cleared = cleared;
  }
  configure(config) {
    this.targets = vehicleTargets(config);
    nativeCards(this.targets, config.statistics_entities ?? []);
    if (config.limit !== undefined && config.limit < this.targets.length) {
      throw new Error('The total page limit must allow one row per vehicle.');
    }
    this.selected = new Set(this.targets.map((_, index) => index));
    super.configure({ ...config, ...this.targets[0] });
  }
  reset() { this.resolved = new Map(); super.reset(); }
  select(index, enabled) {
    if (this.busy || !Number.isInteger(index) || !this.targets[index]) return;
    if (enabled) this.selected.add(index); else this.selected.delete(index);
    this.reset(); this.startIfReady();
  }
  async read(cursor = null) {
    if (!this.active || this.busy || !this.config || !this.hass?.user?.id ||
        this.hass.connected === false || !this.selected?.size || (cursor && !this.page)) return;
    const epoch = this.epoch, rides = [], coverage = { missing_months: [], partial_months: [], unknown_months: [], enumerated_months: [] };
    const continuation = {}, resolved = new Set();
    this.busy = true; this.error = null; this.awaitApply = false; this.changed();
    try {
      const limit = Math.max(1, Math.floor(this.config.limit / this.selected.size));
      for (const index of this.selected) {
        const target = this.targets[index];
        const device = this.resolved.get(index) ?? target.device_id ?? (await this.hass.callWS({
          type: 'config/entity_registry/get', entity_id: target.entity })).device_id;
        if (!this.current(epoch)) return;
        if (!device || resolved.has(device)) throw new Error('invalid_device');
        this.resolved.set(index, device); resolved.add(device);
        if (cursor && !cursor[device]) continue; // Do not restart completed vehicles.
        const response = await this.invoke('get_recorded_trips', { device_id: device,
          start_date: this.start, end_date: this.end, limit,
          ...(cursor ? { cursor: cursor[device] } : {}) });
        if (!this.current(epoch)) return;
        if (response?.source_mode !== 'ride_archive' || !Array.isArray(response.rides) ||
            response.rides.length > limit) throw new Error('invalid_response');
        const name = target.name ?? this.hass.states?.[target.entity]?.attributes?.friendly_name ?? device;
        rides.push(...response.rides.map(ride => ({ ...safeRide(ride), device_id: device,
          vehicle_label: String(name).slice(0, 128) })));
        if (response.next_cursor) continuation[device] = response.next_cursor;
        // A mixed old/new backend fleet cannot claim an exact enumeration count.
        if (!Array.isArray(response.coverage?.enumerated_months)) delete coverage.enumerated_months;
        for (const field of Object.keys(coverage)) {
          coverage[field].push(...(response.coverage?.[field] ?? []).map(month => `${device}:${month}`));
        }
      }
      // Stable ordering preserves source row order for equal/missing timestamps.
      rides.sort((a, b) => (b.start_time ?? '').localeCompare(a.start_time ?? ''));
      this.page = { rides, coverage, next_cursor: Object.keys(continuation).length ? continuation : null };
      this.details.clear(); this.pageNumber = cursor ? this.pageNumber + 1 : 1;
    } catch (error) {
      if (this.current(epoch)) {
        this.cleared?.(); this.details.clear();
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
      }
    } finally { if (this.current(epoch)) { this.busy = false; this.changed(); } }
  }
}
