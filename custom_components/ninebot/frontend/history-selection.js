/** One bounded, in-memory selection and sample cursor for saved rides. */
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
  #index = 0;

  constructor(changed = () => {}) {
    this.changed = changed;
    this.selected = new Map();
    this.slots = new Map();
    this.primary = null;
  }
  clear() {
    this.selected.clear();
    this.slots.clear();
    this.#focus(null);
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
    this.#focus(key);
    this.changed();
    return true;
  }
  update(ride) {
    const key = trackKey(ride.device_id, ride);
    if (!this.selected.has(key)) return;
    this.selected.set(key, { ...safeRide(ride), device_id: ride.device_id,
      vehicle_label: String(ride.vehicle_label ?? ride.device_id).slice(0, 128) });
  }
  activate(key, tracks) {
    if (this.selected.has(key) && this.primary !== key) {
      const ordinal = tracks ? this.position(tracks).index : 0;
      this.#focus(key);
      if (tracks) this.#index = this.#position(tracks, ordinal).index;
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
    if (this.primary === key) this.#focus(this.selected.keys().next().value ?? null);
    this.changed();
  }

  #focus(key) {
    if (this.primary !== key) this.#index = 0;
    this.primary = key;
  }

  #position(tracks, index) {
    const track = tracks?.get(this.primary);
    const count = track?.kind === 'sampled_unverified' && track.points.length > 1 ? track.points.length : 0;
    const bounded = Math.max(0, Math.min(index, count - 1));
    return { index: bounded, count, point: count ? track.points[bounded] : null };
  }

  /** tracks is the authorized geometry cache keyed by trackKey(). Read point.sequence for the source index. */
  position(tracks) {
    return this.#position(tracks, this.#index);
  }

  /** Compare the Nth saved sample. A short or endpoint-only ride has no peer at that position. */
  replayPeers(tracks) {
    const { index, count } = this.position(tracks);
    if (!count) return [];
    const peers = [];
    for (const [key, slot] of this.slots) {
      const track = tracks.get(key);
      if (key !== this.primary && track?.kind === 'sampled_unverified' &&
          track.points.length > 1 && index < track.points.length) {
        peers.push({ slot, point: track.points[index] });
      }
    }
    return peers;
  }

  /** Move to a zero-based saved-point index and return the clamped replay position. */
  seek(index, tracks) {
    const position = this.#position(tracks, Number.isFinite(index) ? Math.trunc(index) : this.#index);
    this.#index = position.index;
    return position;
  }

  /** Read-only projection for views. The owner must clear the selection and tracks when the scope expires. */
  snapshot(tracks) {
    const routes = [...this.selected].map(([key, ride]) =>
      ({ key, slot: this.slots.get(key), ride, track: tracks?.get(key) ?? null }));
    const current = this.selected.get(this.primary);
    return {
      primaryKey: this.primary,
      routes,
      replay: this.position(tracks),
      statistics: {
        current: rideInsights(current ? [current] : []),
        selected: rideInsights(routes.map(({ ride }) => ride)),
      },
    };
  }
}
