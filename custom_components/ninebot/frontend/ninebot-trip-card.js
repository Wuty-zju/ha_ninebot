/** Bounded, account-scoped local pages and explicitly requested cloud details. */
import { LABELS } from './labels.js';

export function businessDates(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return [`${values.year}-${values.month}-01`, `${values.year}-${values.month}-${values.day}`];
}

export function digits(value, fallback = 0) {
  return Number.isInteger(value) ? Math.max(0, Math.min(6, value)) : fallback;
}

const SCALARS = ['ride_id', 'detail_id', 'query_month', 'source', 'start_time', 'end_time',
  'distance_m', 'duration_s', 'max_speed_m_s', 'average_speed_m_s', 'energy_wh',
  'used_electricity_raw', 'energy_intensity_wh_per_km', 'received_at', 'parser_contract'];
export function safeRide(ride) {
  const result = Object.fromEntries(SCALARS.map(key => [key, ride[key] ?? null]));
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

export class TripBrowser {
  constructor(changed = () => {}) {
    this.changed = changed; this.epoch = 0; this.active = false; this.details = new Map();
  }
  reset() {
    this.epoch++; this.busy = false; this.page = null; this.details.clear(); this.error = null;
    this.device = null; this.pageNumber = 0; this.awaitApply = false; this.changed();
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
  dates(start, end) { this.start = start; this.end = end; this.reset(); this.awaitApply = true; }
  current(epoch) { return this.active && epoch === this.epoch; }
  async invoke(service, data) {
    const result = await this.hass.callWS({ type: 'call_service', domain: 'ninebot', service,
      service_data: data, return_response: true });
    return result.response;
  }
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
      this.details.set(ride.ride_id, { ...safeRide(response.ride), received_at: response.received_at });
      // Keep the token; the backend explicitly rejects a changed archive revision.
    } catch (error) {
      if (this.current(epoch)) this.error = error?.code === 'unauthorized' ? 'denied' : 'detail_error';
    } finally { if (this.current(epoch)) { this.busy = false; this.changed(); } }
  }
}

function element(tag, text = '', className = '') {
  const result = document.createElement(tag); result.textContent = String(text);
  if (className) result.className = className;
  return result;
}
const CSS = `:host{display:block}ha-card{padding:16px;color:var(--primary-text-color)}h2{font-size:20px;margin:0 0 12px}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:end}label{display:flex;flex-direction:column;gap:4px;font-size:13px}
input,button{font:inherit;color:inherit;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:9px}
button{cursor:pointer;color:var(--primary-color)}button:disabled{opacity:.5;cursor:default}input{max-width:150px}
.note{font-size:12px;color:var(--secondary-text-color);line-height:1.5;overflow-wrap:anywhere}details{border-top:1px solid var(--divider-color);padding:12px 0}
summary{cursor:pointer;font-weight:500;line-height:1.6}dl{display:grid;grid-template-columns:minmax(100px,1fr) 1fr;gap:8px;font-size:14px}
dt{color:var(--secondary-text-color)}dd{margin:0;overflow-wrap:anywhere}.error{color:var(--error-color)}svg{display:block;width:100%;height:120px}
footer{display:flex;justify-content:space-between;align-items:center;margin-top:12px}.metrics{font-size:14px;font-weight:400}`;

export class NinebotTripCard extends (globalThis.HTMLElement ?? class {}) {
  constructor() {
    super(); this.attachShadow({ mode: 'open' }); this.openRides = new Set();
    this.browser = new TripBrowser(() => this.render());
  }
  setConfig(config) { this.openRides.clear(); this.browser.configure(config); }
  set hass(hass) {
    const oldLanguage = this.browser.hass?.language;
    if (this.browser.hass?.user?.id !== hass.user?.id) this.openRides.clear();
    this.browser.setHass(hass);
    if (oldLanguage !== hass.language) this.render();
  }
  connectedCallback() { this.browser.connect(); this.render(); }
  disconnectedCallback() { this.openRides.clear(); this.browser.disconnect(); }
  getCardSize() { return 5; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(id => id.startsWith('calendar.') &&
      hass.states[id].attributes.source === 'ride_archive');
    return entity ? { entity } : { device_id: 'REPLACE_WITH_VEHICLE_DEVICE_ID' };
  }
  labels() {
    const locale = this.browser.hass?.language ?? 'en';
    return LABELS[locale] ?? (locale.startsWith('zh') ? LABELS['zh-Hans'] : LABELS[locale.split('-')[0]]) ?? LABELS.en;
  }
  number(value, precision, unit = '') {
    if (!Number.isFinite(value)) return '—';
    const language = this.browser.hass?.locale?.language ?? this.browser.hass?.language ?? 'en';
    const n = new Intl.NumberFormat(language, { minimumFractionDigits: value === 0 ? 0 : digits(precision),
      maximumFractionDigits: value === 0 ? 0 : digits(precision) }).format(value);
    return `${n}${unit ? ` ${unit}` : ''}`;
  }
  duration(value) {
    if (!Number.isFinite(value) || value < 0) return '—';
    const seconds = Math.round(value), minutes = Math.floor(seconds / 60), remainder = seconds % 60;
    const language = this.browser.hass?.locale?.language ?? this.browser.hass?.language ?? 'en';
    const format = (value, unit) => new Intl.NumberFormat(language, {
      style: 'unit', unit, unitDisplay: 'short', maximumFractionDigits: 0,
    }).format(value);
    return `${format(minutes, 'minute')}${remainder ? ` ${format(remainder, 'second')}` : ''}`;
  }
  timestamp(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return '—';
    return new Intl.DateTimeFormat(this.browser.hass?.language ?? 'en', {
      timeZone: this.browser.hass?.config?.time_zone ?? 'Asia/Shanghai',
      dateStyle: 'medium', timeStyle: 'medium',
    }).format(new Date(value));
  }
  button(text, action) {
    const button = element('button', text); button.type = 'button'; button.disabled = this.browser.busy;
    button.addEventListener('click', action); return button;
  }
  metrics(ride, labels) {
    const dl = element('dl');
    for (const [name, value] of [
      [labels.start, this.timestamp(ride.start_time)], [labels.end, this.timestamp(ride.end_time)],
      [labels.distance, this.number(ride.distance_m === null ? null : ride.distance_m / 1000, 1, 'km')],
      [labels.duration, this.duration(ride.duration_s)],
      [labels.max_speed, this.number(ride.max_speed_m_s === null ? null : ride.max_speed_m_s * 3.6, 0, 'km/h')],
      [labels.avg_speed, this.number(ride.average_speed_m_s === null ? null : ride.average_speed_m_s * 3.6, 1, 'km/h')],
      [labels.energy, this.number(ride.energy_wh, 0, 'Wh')],
      [labels.energy_intensity, this.number(ride.energy_intensity_wh_per_km, 1, 'Wh/km')],
      [labels.observed, this.timestamp(ride.received_at)],
    ]) { dl.append(element('dt', name), element('dd', value)); }
    return dl;
  }
  render() {
    const browser = this.browser;
    if (!browser?.config) return;
    const labels = this.labels(), card = element('ha-card');
    const title = browser.config.title ?? browser.hass?.states?.[browser.config.entity]?.attributes?.friendly_name ?? labels.title;
    card.append(element('h2', title));
    const toolbar = element('div', '', 'toolbar');
    for (const [key, labelText] of [['start', labels.start_date], ['end', labels.end_date]]) {
      const label = element('label', labelText), input = element('input'); input.type = 'date';
      input.value = browser[key]; input.disabled = browser.busy;
      input.setAttribute('aria-label', labelText);
      input.addEventListener('change', () => {
        this.openRides.clear(); browser.dates(key === 'start' ? input.value : browser.start,
          key === 'end' ? input.value : browser.end);
      });
      label.append(input); toolbar.append(label);
    }
    toolbar.append(this.button(labels.refresh, () => { this.openRides.clear(); void browser.read(); }));
    const displayZone = browser.hass?.config?.time_zone ?? 'Asia/Shanghai';
    const zones = displayZone === 'Asia/Shanghai' ? displayZone : `Asia/Shanghai · ${displayZone}`;
    card.append(toolbar, element('p', `${labels.local} · ${zones}`, 'note'));
    if (browser.error) card.append(element('p', labels[browser.error], 'error'));
    if (browser.busy) card.append(element('p', labels.loading, 'note'));
    const page = browser.page;
    if (page) {
      const coverage = page.coverage;
      card.append(element('p', `${labels.coverage} · ${labels.missing}: ${coverage?.missing_months?.length ?? '—'} · ` +
        `${labels.partial}: ${coverage?.partial_months?.length ?? '—'} · ${labels.unknown}: ${coverage?.unknown_months?.length ?? '—'}`, 'note'));
      if (!page.rides.length) card.append(element('p', labels.empty));
      for (const ride of page.rides) {
        const section = element('details'), summary = element('summary');
        section.open = this.openRides.has(ride.ride_id);
        section.addEventListener('toggle', () => {
          if (!section.isConnected) return;
          if (section.open) this.openRides.add(ride.ride_id); else this.openRides.delete(ride.ride_id);
        });
        summary.append(element('div', this.timestamp(ride.start_time)),
          element('div', `${this.number(ride.distance_m === null ? null : ride.distance_m / 1000, 1, 'km')} · ` +
            `${this.duration(ride.duration_s)}`, 'metrics'));
        section.append(summary);
        const detail = browser.details.get(ride.ride_id), displayed = detail ?? ride;
        section.append(this.metrics(displayed, labels));
        const sourceInfo = element('details');
        sourceInfo.append(element('summary', labels.source_fields), element('p',
          `${detail ? labels.cloud : labels.local} · ${displayed.source ?? '—'} · ${displayed.parser_contract ?? '—'} · ${labels.ride_id}: ${displayed.ride_id}`, 'note'));
        const provenance = Object.entries(displayed.field_provenance).filter(([, value]) => value)
          .map(([key, value]) => `${key}: ${value}`).join(' · ');
        if (provenance) sourceInfo.append(element('p', provenance, 'note'));
        section.append(sourceInfo);
        if (displayed.used_electricity_raw !== null) section.append(element('p',
          `${labels.unknown_units}: ${displayed.used_electricity_raw}`, 'note'));
        if (displayed.track_summary.total_known !== null) section.append(element('p',
          `${labels.track_points}: ${displayed.track_summary.total_known}`, 'note'));
        for (const [title, samples, field] of [
          [labels.samples, displayed.speed_samples, 'speed_raw'],
          [labels.distance_samples, displayed.distance_samples, 'distance_delta_raw'],
        ]) {
          const points = samplePolyline(samples.map(point => ({ sequence: point.sequence, speed_raw: point[field] })));
          if (!points) continue;
          section.append(element('p', title, 'note'));
          const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
          svg.setAttribute('viewBox', '0 0 300 100'); svg.setAttribute('role', 'img');
          svg.setAttribute('aria-label', title);
          const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
          line.setAttribute('points', points); line.setAttribute('fill', 'none');
          line.setAttribute('stroke', 'var(--primary-color)'); line.setAttribute('stroke-width', '2');
          svg.append(line); section.append(svg);
          const values = element('details'); values.append(element('summary', title));
          values.append(element('p', samples.map(p => `${p.sequence}: ${p[field]}`).join(' · '), 'note'));
          section.append(values);
        }
        const button = this.button(labels.cloud_detail, () => void browser.detail(ride));
        button.disabled ||= !ride.detail_id; section.append(button); card.append(section);
      }
      const footer = element('footer'); footer.append(element('span', `${labels.page} ${browser.pageNumber} · ${page.rides.length}`, 'note'));
      const next = this.button(labels.next, () => { this.openRides.clear(); void browser.read(page.next_cursor); });
      next.disabled ||= !page.next_cursor; footer.append(next); card.append(footer);
    }
    const style = element('style', CSS); this.shadowRoot.replaceChildren(style, card);
  }
}

if (globalThis.customElements && !customElements.get('ninebot-trip-card')) {
  customElements.define('ninebot-trip-card', NinebotTripCard);
  globalThis.customCards ??= [];
  globalThis.customCards.push({ type: 'ninebot-trip-card', name: 'Ninebot Ride History',
    description: 'Local date pages and explicitly requested cloud detail', preview: true });
}
