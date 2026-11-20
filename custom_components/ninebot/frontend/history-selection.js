/** One bounded, in-memory selection for the ride list and shared map. */
import { safeRide } from './trip-browser.js';
import { trackKey } from './local-tracks.js';

export class HistorySelection {
  constructor(changed = () => {}) {
    this.changed = changed;
    this.selected = new Map();
    this.primary = null;
  }
  clear() {
    this.selected.clear();
    this.primary = null;
    this.changed();
  }
  select(ride, compare = false) {
    if (typeof ride?.ride_id !== 'string' || !ride.ride_id || !/^20\d{4}$/.test(ride.query_month) ||
        typeof ride.device_id !== 'string' || !ride.device_id.trim()) return false;
    const key = trackKey(ride.device_id, ride);
    if (compare && !this.selected.has(key) && this.selected.size >= 5) return false;
    if (!compare) this.selected.clear();
    this.selected.set(key, null);
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
    if (this.primary === key) this.primary = this.selected.keys().next().value ?? null;
    this.changed();
  }
}
