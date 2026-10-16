/** One bounded, in-memory selection for list, map, metrics and sample curves. */
import { safeRide } from './trip-browser.js';
import { trackKey } from './local-tracks.js';

export class HistorySelection {
  constructor(changed = () => {}) {
    this.changed = changed; this.revision = 0;
    this.selected = new Map(); this.primary = null; this.focus = null; this.brush = null;
  }
  notify(kind) { this.revision++; this.changed(kind); }
  clear() {
    this.selected.clear(); this.primary = null; this.focus = null; this.brush = null;
    this.notify('selection');
  }
  select(ride, compare = false) {
    if (typeof ride?.ride_id !== 'string' || !ride.ride_id || !/^20\d{4}$/.test(ride.query_month) ||
        typeof ride.device_id !== 'string' || !ride.device_id.trim()) return false;
    const key = trackKey(ride.device_id, ride);
    if (compare && !this.selected.has(key) && this.selected.size >= 5) return false;
    const value = { ...safeRide(ride), device_id: ride.device_id,
      vehicle_label: String(ride.vehicle_label ?? ride.device_id).slice(0, 128) };
    const same = this.primary === key;
    if (!compare) this.selected.clear();
    this.selected.set(key, value); this.primary = key;
    if (!same || !this.selected.has(this.focus?.key)) this.focus = null;
    if (!same || !this.selected.has(this.brush?.key)) this.brush = null;
    this.notify('selection'); return true;
  }
  update(ride) {
    const key = trackKey(ride.device_id, ride);
    if (!this.selected.has(key)) return;
    this.selected.set(key, { ...safeRide(ride), device_id: ride.device_id,
      vehicle_label: String(ride.vehicle_label ?? ride.device_id).slice(0, 128) });
  }
  activate(key) {
    if (!this.selected.has(key) || this.primary === key) return;
    this.primary = key; this.focus = null; this.brush = null; this.notify('selection');
  }
  remove(key) {
    if (!this.selected.delete(key)) return;
    if (this.primary === key) this.primary = this.selected.keys().next().value ?? null;
    if (this.focus?.key === key) this.focus = null;
    if (this.brush?.key === key) this.brush = null;
    this.notify('selection');
  }
  focusSample(key, sequence) {
    if (!this.selected.has(key) || !Number.isInteger(sequence) || sequence < 0 ||
        sequence > 1048575 || (this.focus?.key === key && this.focus.sequence === sequence)) return;
    // Focus does not change the chosen comparison set or cause a data query.
    this.focus = { key, sequence }; this.notify('focus');
  }
  brushSamples(key, start, end) {
    if (!this.selected.has(key) || ![start, end].every(value =>
      Number.isInteger(value) && value >= 0 && value < 1048576)) return;
    const first = Math.min(start, end), last = Math.max(start, end);
    if (this.brush?.key === key && this.brush.start === first && this.brush.end === last) return;
    this.brush = { key, start: first, end: last };
    this.notify('focus');
  }
}
