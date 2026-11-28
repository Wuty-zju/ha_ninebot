/** One bounded, in-memory selection for the ride list and shared map. */
import { safeRide } from './trip-browser.js';
import { trackKey } from './local-tracks.js';

export const MAX_SELECTED_ROUTES = 5;

const finiteAmount = value => Number.isFinite(value) && value >= 0 ? value : null;

function total(values) {
  return finiteAmount(values.reduce((sum, value) => sum + value, 0));
}

/** Compare complete recorded scalars, independent of geometry and playback state. */
export function rideInsights(rides) {
  const rows = rides.map(ride => ({
    distance: finiteAmount(ride.distance_m),
    duration: finiteAmount(ride.duration_s ?? (Number.isFinite(ride.duration_h) ? ride.duration_h * 3600 : null)),
    energy: finiteAmount(ride.energy_wh),
    durationConflict: ride.warnings?.includes('duration_time_difference'),
  }));

  function sum(key, precision = 0) {
    const values = rows.map(row => row[key]).filter(value => value !== null);
    return { value: values.length && values.length === rows.length ? total(values) : null,
      count: values.length, precision };
  }

  function ratio(numerator, denominator, usable, scale) {
    const paired = rows.filter(usable);
    let value = null;
    if (paired.length && paired.length === rows.length) {
      const top = total(paired.map(row => row[numerator]));
      const bottom = total(paired.map(row => row[denominator]));
      if (top !== null && bottom > 0) value = finiteAmount(top / bottom * scale);
    }
    return { value, count: paired.length, precision: 1 };
  }

  return {
    distance: sum('distance', Math.max(0, ...rides.map(ride => ride.precision?.mileages ?? 0))),
    duration: sum('duration'),
    energy: sum('energy', Math.max(0, ...rides.map(ride => ride.precision?.ec ?? 0))),
    energy_intensity: ratio('energy', 'distance', row => row.energy !== null && row.distance > 0, 1000),
    average_speed: ratio('distance', 'duration',
      row => row.distance !== null && row.duration > 0 && !row.durationConflict, 3.6),
  };
}

export class HistorySelection {
  constructor(changed = () => {}) {
    this.changed = changed;
    this.selected = new Map();
    this.slots = new Map();
    this.primary = null;
  }
  clear() {
    this.selected.clear();
    this.slots.clear();
    this.primary = null;
    this.changed();
  }
  select(ride, compare = false) {
    if (typeof ride?.ride_id !== 'string' || !ride.ride_id || !/^20\d{4}$/.test(ride.query_month) ||
        typeof ride.device_id !== 'string' || !ride.device_id.trim()) return false;
    const key = trackKey(ride.device_id, ride);
    if (!this.selected.has(key)) {
      if (compare && this.selected.size >= MAX_SELECTED_ROUTES) return false;
      if (!compare) {
        this.selected.clear();
        this.slots.clear();
      }
      const occupied = new Set(this.slots.values());
      const slot = Array.from({ length: MAX_SELECTED_ROUTES }, (_, index) => index)
        .find(index => !occupied.has(index));
      this.slots.set(key, slot);
      this.selected.set(key, null);
    }
    this.update(ride);
    this.primary = key;
    this.changed();
    return true;
  }
  update(ride) {
    const key = trackKey(ride.device_id, ride);
    if (!this.selected.has(key)) return;
    this.selected.set(key, { ...safeRide(ride), device_id: ride.device_id,
      vehicle_label: String(ride.vehicle_label ?? ride.device_id).slice(0, 128) });
  }
  activate(key) {
    if (this.selected.has(key) && this.primary !== key) {
      this.primary = key;
      this.changed();
    }
  }
  retainPrimary() {
    if (this.selected.size < 2) return;
    for (const key of this.selected.keys()) {
      if (key !== this.primary) {
        this.selected.delete(key);
        this.slots.delete(key);
      }
    }
    this.changed();
  }
  remove(key) {
    if (!this.selected.delete(key)) return;
    this.slots.delete(key);
    if (this.primary === key) this.primary = this.selected.keys().next().value ?? null;
    this.changed();
  }
}
