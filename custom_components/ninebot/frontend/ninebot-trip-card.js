/** Optional local history maps; runtime assets are served by this integration. */
import { LABELS } from './labels.js';
import { samplePaths, digits } from './trip-browser.js';
export { TripBrowser, safeRide, businessDates, digits, samplePolyline, samplePaths } from './trip-browser.js';
import { FleetBrowser, nativeCards } from './fleet-browser.js';
import { LocalTracks, trackKey } from './local-tracks.js';
import { RouteMap, ROUTE_COLORS } from './route-map.js';

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
    this.routeMap = new RouteMap(() => { this.mapError = true; this.render(); });
    this.tracks = new LocalTracks((service, data) => this.browser.invoke(service, data), () => this.render());
    this.browser = new FleetBrowser(() => this.render(), () => {
      this.tiles = false; this.mapError = false; this.tracks.clear(); this.routeMap.clear();
    });
  }
  setConfig(config) { this.openRides.clear(); this.browser.configure(config); }
  set hass(hass) {
    const oldLanguage = this.browser.hass?.language;
    if (this.browser.hass?.user?.id !== hass.user?.id) this.openRides.clear();
    this.browser.setHass(hass);
    if (oldLanguage !== hass.language) this.render();
  }
  connectedCallback() { this.browser.connect(); this.render(); }
  disconnectedCallback() { this.openRides.clear(); this.browser.disconnect(); this.routeMap.clear(); }
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
  number(value, precision, unit = '', preserveZero = false) {
    if (!Number.isFinite(value)) return '—';
    const language = this.browser.hass?.locale?.language ?? this.browser.hass?.language ?? 'en';
    const n = new Intl.NumberFormat(language, { minimumFractionDigits: value === 0 && !preserveZero ? 0 : digits(precision),
      maximumFractionDigits: value === 0 && !preserveZero ? 0 : digits(precision) }).format(value);
    return `${n}${unit ? ` ${unit}` : ''}`;
  }
  duration(value) {
    return this.number(value, 2, 'h', true);
  }
  timestamp(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return '—';
    return new Intl.DateTimeFormat(this.browser.hass?.language ?? 'en', {
      timeZone: this.browser.hass?.config?.time_zone ?? 'Asia/Shanghai',
      dateStyle: 'medium', timeStyle: 'medium',
    }).format(new Date(value));
  }
  button(text, action) {
    const button = element('button', text); button.type = 'button'; button.disabled = this.browser.busy || this.tracks.busy;
    button.addEventListener('click', action); return button;
  }
  metrics(ride, labels) {
    const dl = element('dl');
    for (const [name, value] of [
      [labels.start, this.timestamp(ride.start_time)], [labels.end, this.timestamp(ride.end_time)],
      [labels.distance, this.number(ride.distance_m === null ? null : ride.distance_m / 1000, 1, 'km')],
      [labels.duration, this.duration(ride.duration_h)],
      [labels.max_speed, this.number(ride.max_speed_m_s === null ? null : ride.max_speed_m_s * 3.6, 0, 'km/h')],
      [labels.avg_speed, this.number(ride.average_speed_m_s === null ? null : ride.average_speed_m_s * 3.6, 1, 'km/h')],
      [labels.energy, this.number(ride.energy_wh, 0, 'Wh')],
      [labels.energy_intensity, this.number(ride.energy_intensity_wh_per_km, 1, 'Wh/km')],
      [labels.observed, this.timestamp(ride.received_at)],
    ]) { dl.append(element('dt', name), element('dd', value)); }
    return dl;
  }
  async readRoute(ride) {
    if (!this.browser.active || this.browser.busy || !this.browser.page?.rides.includes(ride)) return;
    if (this.mapError) {
      this.routeMap.clear(); this.routeMap = new RouteMap(() => { this.mapError = true; this.render(); });
    }
    this.mapError = false;
    await this.tracks.read(ride.device_id, ride);
    this.render();
  }
  curve(parent, title, samples, field) {
    const paths = samplePaths(samples.filter(point => Number.isFinite(point[field])).map(point =>
      ({ sequence: point.sequence, speed_raw: point[field], segment: point[field === 'speed_raw' ? 'speed_segment' : 'distance_segment'] })));
    if (!paths.length) return;
    parent.append(element('p', title, 'note'));
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 300 100'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', title);
    for (const points of paths) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      line.setAttribute('points', points); line.setAttribute('fill', 'none');
      line.setAttribute('stroke', 'var(--primary-color)'); line.setAttribute('stroke-width', '2'); svg.append(line);
    }
    parent.append(svg, element('p', `${this.labels().sample_axis}: ${samples[0]?.sequence ?? '—'}–${samples.at(-1)?.sequence ?? '—'}`, 'note'));
  }
  render() {
    const browser = this.browser;
    if (!browser?.config) return;
    const labels = this.labels(), card = element('ha-card');
    const title = browser.config.title ?? (browser.targets.length === 1 ? browser.hass?.states?.[browser.config.entity]?.attributes?.friendly_name : null) ?? labels.title;
    card.append(element('h2', title));
    if (browser.targets.length > 1) {
      const chooser = element('div', '', 'toolbar');
      browser.targets.forEach((target, index) => {
        const label = element('label', target.name ?? browser.hass?.states?.[target.entity]?.attributes?.friendly_name ?? target.entity ?? target.device_id);
        const input = element('input'); input.type = 'checkbox'; input.checked = browser.selected.has(index);
        input.disabled = browser.busy || this.tracks.busy;
        input.addEventListener('change', () => { this.openRides.clear(); browser.select(index, input.checked); });
        label.prepend(input); chooser.append(label);
      });
      card.append(chooser);
    }
    const toolbar = element('div', '', 'toolbar');
    for (const [key, labelText] of [['start', labels.start_date], ['end', labels.end_date]]) {
      const label = element('label', labelText), input = element('input'); input.type = 'date';
      input.value = browser[key]; input.disabled = browser.busy || this.tracks.busy;
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
    if (browser.busy || this.tracks.busy) card.append(element('p', labels.loading, 'note'));
    if (this.tracks.error) card.append(element('p', labels[this.tracks.error] ?? labels.track_error, 'error'));
    const tracks = [...this.tracks.tracks.values()];
    if (tracks.length) {
      card.append(element('h3', labels.map), element('p', labels.coordinate_note, 'note'));
      const controls = element('div', '', 'toolbar');
      controls.append(this.button(labels.clear_tracks, () => { this.tiles = false; this.tracks.clear(); }),
        this.button(this.tiles ? labels.disable_tiles : labels.enable_tiles, () => { this.tiles = !this.tiles; this.render(); }));
      card.append(controls, element('p', labels.tile_privacy, 'note'));
      if (this.mapError) card.append(element('p', labels.map_error, 'error'));
      else card.append(this.routeMap.element);
      for (const [index, track] of tracks.entries()) {
        const legend = element('p', `${track.label} · ${track.ride_id} · ${labels.track_points}: ${track.valid_read}/${track.total ?? '—'} · ` +
          (track.kind === 'server_simplified' ? labels.simplified : labels.unverified_track) +
          (track.capped ? ` · ${labels.display_limit}` : '') + (track.complete ? '' : ` · ${labels.partial_track}`), 'note');
        legend.style.borderLeft = `4px solid ${ROUTE_COLORS[index]}`;
        legend.style.paddingLeft = '8px'; card.append(legend);
        const key = [...this.tracks.tracks.entries()].find(([, value]) => value === track)[0];
        card.append(this.button(labels.remove_track, () => this.tracks.remove(key)));
        this.curve(card, labels.samples, track.points, 'speed_raw');
        this.curve(card, labels.distance_samples, track.points, 'distance_delta_raw');
      }
    } else this.routeMap.clear();
    const native = nativeCards(browser.targets.filter((_, index) => browser.selected.has(index)), browser.config.statistics_entities ?? []);
    if (native.cards.length) {
      const help = element('details'); help.append(element('summary', labels.native_cards), element('p', labels.native_note, 'note'),
        element('pre', JSON.stringify(native, null, 2), 'note'));
      const link = element('a', labels.open_calendar); link.href = '/calendar'; help.append(link); card.append(help);
    }
    const page = browser.page;
    if (page) {
      const coverage = page.coverage;
      card.append(element('p', `${labels.coverage} · ${labels.missing}: ${coverage?.missing_months?.length ?? '—'} · ` +
        `${labels.partial}: ${coverage?.partial_months?.length ?? '—'} · ${labels.unknown}: ${coverage?.unknown_months?.length ?? '—'}`, 'note'));
      if (!page.rides.length) card.append(element('p', labels.empty));
      for (const ride of page.rides) {
        const section = element('details'), summary = element('summary');
        const key = trackKey(ride.device_id, ride);
        section.open = this.openRides.has(key);
        section.addEventListener('toggle', () => {
          if (!section.isConnected) return;
          if (section.open) this.openRides.add(key); else this.openRides.delete(key);
        });
        summary.append(element('div', `${ride.vehicle_label} · ${this.timestamp(ride.start_time)}`),
          element('div', `${this.number(ride.distance_m === null ? null : ride.distance_m / 1000, 1, 'km')} · ` +
            `${this.duration(ride.duration_h)}`, 'metrics'));
        section.append(summary);
        const detail = browser.details.get(key), displayed = detail ?? ride;
        section.append(this.metrics(displayed, labels));
        const sourceInfo = element('details');
        sourceInfo.append(element('summary', labels.source_fields), element('p',
          `${detail?.source_mode === 'ninecli' ? labels.cloud : labels.local} · ${displayed.source ?? '—'} · ${displayed.parser_contract ?? '—'} · ${labels.ride_id}: ${displayed.ride_id}`, 'note'));
        const provenance = Object.entries(displayed.field_provenance).filter(([, value]) => value)
          .map(([key, value]) => `${key}: ${value}`).join(' · ');
        if (provenance) sourceInfo.append(element('p', provenance, 'note'));
        section.append(sourceInfo);
        if (displayed.used_electricity_raw !== null) section.append(element('p',
          `${labels.unknown_units}: ${displayed.used_electricity_raw}`, 'note'));
        if (displayed.track_summary.total_known !== null) section.append(element('p',
          `${labels.track_points}: ${displayed.track_summary.total_known}`, 'note'));
        this.curve(section, labels.samples, displayed.speed_samples, 'speed_raw');
        this.curve(section, labels.distance_samples, displayed.distance_samples, 'distance_delta_raw');
        const saved = this.tracks.tracks.get(key);
        const routeButton = this.button(saved && !saved.complete ? labels.more_track : labels.show_track, () => void this.readRoute(ride));
        routeButton.disabled ||= saved?.complete || !ride.ride_id;
        section.append(routeButton);
        const button = this.button(labels.cloud_detail, () => void browser.detail(ride));
        button.disabled ||= !ride.detail_id; section.append(button); card.append(section);
      }
      const footer = element('footer'); footer.append(element('span', `${labels.page} ${browser.pageNumber} · ${page.rides.length}`, 'note'));
      const next = this.button(labels.next, () => { this.openRides.clear(); void browser.read(page.next_cursor); });
      next.disabled ||= !page.next_cursor; footer.append(next); card.append(footer);
    }
    const style = element('style', CSS); this.shadowRoot.replaceChildren(style, card);
    if (tracks.length && !this.mapError) void this.routeMap.update(tracks, this.tiles === true);
  }
}

if (globalThis.customElements && !customElements.get('ninebot-trip-card')) {
  customElements.define('ninebot-trip-card', NinebotTripCard);
  globalThis.customCards ??= [];
  globalThis.customCards.push({ type: 'ninebot-trip-card', name: 'Ninebot Ride History',
    description: 'Local single/multi-vehicle history, saved tracks and explicit cloud details', preview: true });
}
