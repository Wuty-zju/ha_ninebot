/** One bounded, in-memory selection for the ride list and shared map. */
import { safeRide } from './trip-browser.js';
import { trackKey } from './local-tracks.js';

export const MAX_SELECTED_ROUTES = 5;

export function selectionTotals(rides) {
  function sum(read) {
    const values = rides.map(read);
    return values.length && values.every(Number.isFinite) ? values.reduce((a, b) => a + b, 0) : null;
  }
  return {
    distance_m: sum(ride => ride.distance_m),
    duration_s: sum(ride => ride.duration_s ?? (Number.isFinite(ride.duration_h) ? ride.duration_h * 3600 : null)),
    energy_wh: sum(ride => ride.energy_wh),
    precision: { mileages: Math.max(0, ...rides.map(ride => ride.precision.mileages)),
      ec: Math.max(0, ...rides.map(ride => ride.precision.ec)) },
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
  remove(key) {
    if (!this.selected.delete(key)) return;
    this.slots.delete(key);
    if (this.primary === key) this.primary = this.selected.keys().next().value ?? null;
    this.changed();
  }
}
