/** Open an exact, authorized selection without scanning archive pages or requesting cloud detail. */
import { invokeHistory, watchHistory } from './history-network.js';
import { trackKey } from './local-tracks.js';
import { safeRide } from './trip-browser.js';

export function readRideLink(search) {
  const params = new URLSearchParams(search);
  const keys = ['view', 'receipt'].filter(key => params.has(key));
  if (!keys.length) return null;
  const kind = keys[0], values = params.getAll(kind);
  const valid = kind === 'view' ? /^[a-f0-9]{32}$/ : /^r1\.[a-f0-9]{32}\.[A-Za-z0-9_-]{39,1403}$/;
  if (keys.length !== 1 || values.length !== 1 || !valid.test(values[0])) return { kind: 'invalid' };
  return { kind, value: values[0] };
}

export class LinkedRideView {
  constructor(hass, link, view, failed, changed) {
    this.hass = hass;
    this.link = link;
    this.view = view;
    this.failed = failed;
    this.changed = changed;
    this.closed = false;
  }

  close() {
    this.closed = true;
    this.stop?.();
    this.rows = null;
  }

  fail() {
    if (this.closed) return;
    this.close();
    this.view.clear();
    this.failed();
  }

  async open() {
    try {
      if (!this.hass.connection?.subscribeMessage) {
        this.fail();
        return;
      }
      const { kind, value } = this.link;
      const message = { type: kind === 'view' ? 'ninebot/open_ride_view' : 'ninebot/open_ride_receipt',
        [kind]: value };
      const { rides } = await this.hass.callWS(message);
      if (this.closed) return;
      if (!Array.isArray(rides) || (kind === 'view' ? rides.length < 2 || rides.length > 5 : rides.length !== 1) ||
          rides.some(r => !r || typeof r.device_id !== 'string' || !r.device_id ||
            typeof r.ride_id !== 'string' || !r.ride_id || !/^20\d\d(0[1-9]|1[0-2])$/.test(r.query_month))) {
        this.fail();
        return;
      }
      const rows = rides.map(r => ({ ...safeRide(r), device_id: r.device_id,
        vehicle_label: String(r.vehicle_label || r.device_id).slice(0, 128) }));
      this.rows = new Set(rows.map(row => trackKey(row.device_id, row)));
      if (this.rows.size !== rows.length) {
        this.fail();
        return;
      }
      // No rows or geometry are displayed until a new track-authorized feed accepts the selection.
      const stop = watchHistory(this.hass, rows.map(r => r.device_id), event => {
        if (this.closed) return;
        if (event.type === 'baseline' && !this.ready) {
          this.ready = true;
          void this.show(rows);
        } else if (event.type === 'changed') this.changed();
        else if (event.type !== 'baseline') this.fail();
      }, { includeTrack: true, shared: false, view: kind === 'view' ? value : undefined });
      if (this.closed) stop();
      else this.stop = stop;
    } catch {
      this.fail();
    }
  }

  async show(rows) {
    try {
      this.view.setLinkedRides(rows);
      for (const row of rows) {
        if (this.closed) return;
        if (row.detail_saved) await this.view.readTrack(row);
      }
    } catch {
      this.fail();
    }
  }

  async detail(service, data) {
    const allowed = () => !this.closed && this.ready && this.rows?.has(trackKey(data.device_id, data));
    if (!allowed() || service !== 'get_trip_detail' || data.local_only !== true || data.include_track !== true) {
      throw new Error('unauthorized');
    }
    const result = await invokeHistory(this.hass, service, data, allowed);
    if (allowed() && this.link.kind === 'view') {
      const ack = await this.hass.callWS({ type: 'ninebot/open_ride_view', view: this.link.value, validate_only: true });
      if (ack?.valid !== true) throw new Error('unauthorized');
    }
    if (!allowed()) throw new Error('unauthorized');
    return result;
  }
}
