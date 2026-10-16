/** Local map-centric ride workbench. No historical GPS is written into HA state. */
import { LABELS } from './labels.js';
import { samplePaths, digits } from './trip-browser.js';
export { TripBrowser, safeRide, businessDates, digits, samplePolyline, samplePaths } from './trip-browser.js';
import { FleetBrowser, nativeCards } from './fleet-browser.js';
import { LocalTracks, trackKey } from './local-tracks.js';
import { RouteMap } from './route-map.js';
import { HistorySelection } from './history-selection.js';
import './trip-card-editor.js';

function element(tag, text = '', className = '') {
  const result = document.createElement(tag); result.textContent = String(text);
  if (className) result.className = className;
  return result;
}
const CSS = `:host{display:block;container-type:inline-size}*{box-sizing:border-box}ha-card{display:block;padding:16px;color:var(--primary-text-color)}
h2{font-size:20px;margin:0 0 12px}h3{font-size:16px}.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:end}
label{display:flex;flex-direction:column;gap:4px;font-size:13px}input,button{font:inherit;color:inherit;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:10px;min-height:44px}
button{cursor:pointer;color:var(--primary-color)}button:disabled{opacity:.5;cursor:default}input[type=date]{max-width:150px}input[type=checkbox]{min-height:24px;width:24px}
.note{font-size:12px;color:var(--secondary-text-color);line-height:1.5;overflow-wrap:anywhere}.error{color:var(--error-color)}
.workbench{display:grid;grid-template-columns:minmax(0,2fr) minmax(230px,1fr);grid-template-areas:"map list" "detail list";gap:16px;align-items:start}.main,.rides,.ride-detail{min-width:0}.main{grid-area:map}.rides{grid-area:list}.ride-detail{grid-area:detail}
.rides{max-height:850px;overflow:auto;overscroll-behavior:contain}.ride{padding:10px 0;border-bottom:1px solid var(--divider-color)}
.ride.selected{border-left:3px solid var(--primary-color);padding-left:8px}.ride button{width:100%;text-align:start;white-space:normal}.compare{flex-direction:row;align-items:center}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chips button[aria-pressed=true]{background:var(--primary-color);color:var(--text-primary-color,#fff)}
details{border-top:1px solid var(--divider-color);padding:12px 0}summary{cursor:pointer;font-weight:500;line-height:1.6}
dl{display:grid;grid-template-columns:minmax(100px,1fr) 1fr;gap:8px;font-size:14px}dt{color:var(--secondary-text-color)}dd{margin:0;overflow-wrap:anywhere}
svg{display:block;width:100%;height:140px;touch-action:pan-y}.metrics{font-size:13px;font-weight:400}.samples input[type=range]{width:100%}
footer{display:flex;justify-content:space-between;align-items:center;margin-top:12px}.placeholder{padding:36px 16px;text-align:center;background:var(--secondary-background-color);border-radius:12px}
:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
@media(max-width:650px){.workbench{grid-template-columns:minmax(0,1fr);grid-template-areas:"map" "list" "detail"}.rides{max-height:300px}.toolbar button{flex:1 1 auto}ha-card{padding:12px}}
@container(max-width:650px){.workbench{grid-template-columns:minmax(0,1fr);grid-template-areas:"map" "list" "detail"}.rides{max-height:300px}.toolbar button{flex:1 1 auto}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}`;

export class NinebotTripCard extends (globalThis.HTMLElement ?? class {}) {
  constructor() {
    super(); this.attachShadow({ mode: 'open' }); this.focusBindings = []; this.curves = [];
    this.selection = new HistorySelection(kind => {
      if (this.resetting) return;
      if (kind === 'focus') this.updateFocus(); else this.render();
    });
    this.routeMap = this.makeMap();
    this.tracks = new LocalTracks((service, data) => this.browser.invoke(service, data), () => {
      if (!this.resetting) this.render();
    });
    this.browser = new FleetBrowser(() => this.historyChanged(), () => this.clearPrivate());
    this.visibility = () => { if (document.hidden) this.stopPlayback(); };
  }
  makeMap() {
    return new RouteMap(() => { this.mapError = true; this.render(); },
      key => this.selection.activate(key), (key, sequence) => this.selection.focusSample(key, sequence));
  }
  clearPrivate() {
    this.resetting = true; cancelAnimationFrame(this.curveFrame); this.pendingFit = null; this.stopPlayback(true); this.tiles = false; this.mapError = false;
    this.selection.clear(); this.tracks.clear(); this.routeMap.clear(); this.notice = null;
    this.resetting = false;
  }
  historyChanged() {
    const browser = this.browser;
    if (!browser) return;
    if (!browser.busy && browser.page?.rides.length && !this.selection.selected.size) {
      const ride = browser.page.rides[0], epoch = browser.epoch;
      this.resetting = true; this.selection.select(ride); this.resetting = false;
      // Only saved local data; never implicitly fill a cloud detail.
      if (ride.detail_saved === true) queueMicrotask(() => {
        if (this.isConnected && browser.current(epoch) && this.selection.primary === trackKey(ride.device_id, ride)) void this.readRoute(ride);
      });
    }
    this.render();
  }
  setConfig(config) { this.browser.configure(config); }
  set hass(hass) {
    const oldLanguage = this.browser.hass?.language;
    this.browser.setHass(hass);
    if (oldLanguage !== hass.language) this.render();
  }
  connectedCallback() { document.addEventListener('visibilitychange', this.visibility); this.browser.connect(); this.render(); }
  disconnectedCallback() { document.removeEventListener('visibilitychange', this.visibility); this.browser.disconnect(); this.clearPrivate(); }
  getCardSize() { return 12; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }
  static getConfigElement() { return document.createElement('ninebot-trip-card-editor'); }
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(id => id.startsWith('calendar.') && hass.states[id].attributes.source === 'ride_archive');
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
      [labels.ride_energy_intensity, this.number(ride.energy_intensity_wh_per_km, 1, 'Wh/km')],
      [labels.observed, this.timestamp(ride.received_at)],
    ]) { dl.append(element('dt', name), element('dd', value)); }
    return dl;
  }
  stopPlayback(clearFocus = false) {
    clearInterval(this.playback); this.playback = null; this.playKey = null;
    if (this.playButton) this.playButton.textContent = this.labels().play_points;
    this.playButton = null;
    if (clearFocus) this.routeMap?.highlight();
  }
  choose(ride, compare = false) {
    if (this.browser.busy || this.tracks.busy) return;
    this.resetting = true;
    const accepted = this.selection.select(ride, compare);
    if (accepted) {
      const key = trackKey(ride.device_id, ride);
      if (!this.routeMap.groups.has(key)) this.pendingFit = key;
      // Reuse up to five same-scope tracks when switching the focused record.
      this.notice = null;
    } else this.notice = this.labels().selection_limit;
    this.resetting = false; this.render();
    if (accepted && ride.detail_saved === true) void this.readRoute(ride);
  }
  remove(key) {
    if (this.tracks.busy) return;
    this.resetting = true; this.selection.remove(key); this.tracks.remove(key); this.resetting = false;
    this.render();
  }
  async readRoute(ride) {
    const key = trackKey(ride.device_id, ride);
    if (!this.browser.active || this.browser.busy || this.tracks.busy ||
        (!this.browser.page?.rides.includes(ride) && !this.selection.selected.has(key))) return;
    if (!this.selection.selected.has(key) && !this.selection.select(ride, true)) return;
    this.selection.activate(key);
    if (this.mapError) { this.routeMap.clear(); this.routeMap = this.makeMap(); }
    this.mapError = false;
    if (!this.tracks.tracks.has(key)) {
      this.pendingFit = key;
      // A cached inactive ride must not evict a still-selected comparison track.
      this.resetting = true;
      for (const cached of this.tracks.tracks.keys()) {
        if (this.tracks.tracks.size < 5) break;
        if (!this.selection.selected.has(cached)) this.tracks.remove(cached);
      }
      this.resetting = false;
    }
    await this.tracks.read(ride.device_id, ride);
  }
  updateFocus() {
    const focus = this.selection.focus;
    const track = focus && this.tracks.tracks.get(focus.key);
    const point = track?.points.find(point => point.sequence === focus.sequence);
    this.routeMap.highlight(point);
    this.routeMap.brush(this.selection.brush);
    for (const binding of this.focusBindings) {
      const brush = this.selection.brush;
      binding.range.textContent = brush?.key === binding.key ? `${this.labels().sample_selection}: ${brush.start}–${brush.end}` : '';
      if (focus?.key !== binding.key || !point) { binding.status.textContent = ''; continue; }
      const index = track.points.indexOf(point);
      binding.input.value = String(index);
      binding.status.textContent = `${this.labels().point_sequence}: ${point.sequence} · ${this.labels().point_speed}: ${point.speed_raw ?? '—'} · ${this.labels().point_distance}: ${point.distance_delta_raw ?? '—'}`;
    }
    for (const curve of this.curves) {
      const x = sequence => 8 + (sequence - curve.first) / (curve.last - curve.first || 1) * 284;
      const visible = focus?.key === curve.key && focus.sequence >= curve.first && focus.sequence <= curve.last;
      curve.line.setAttribute('visibility', visible ? 'visible' : 'hidden');
      if (visible) { curve.line.setAttribute('x1', x(focus.sequence)); curve.line.setAttribute('x2', x(focus.sequence)); }
      const brush = this.selection.brush;
      curve.region.setAttribute('visibility', brush?.key === curve.key ? 'visible' : 'hidden');
      if (brush?.key === curve.key) {
        curve.region.setAttribute('x', x(brush.start)); curve.region.setAttribute('width', Math.max(0, x(brush.end) - x(brush.start)));
      }
    }
  }
  pointControls(key, track, labels) {
    const controls = element('div', '', 'samples'), status = element('p', '', 'note'), range = element('p', '', 'note');
    const label = element('label', labels.point_sequence), input = element('input'); input.type = 'range';
    input.min = '0'; input.max = String(Math.max(0, track.points.length - 1)); input.step = '1'; input.value = '0';
    input.setAttribute('aria-label', `${labels.point_sequence} · ${track.label}`);
    input.dataset.control = `samples:${key}`; input.disabled = !track.points.length;
    const show = () => {
      const point = track.points[Number(input.value)];
      if (point) this.selection.focusSample(key, point.sequence);
    };
    input.addEventListener('input', () => { this.stopPlayback(); show(); });
    const play = this.button(labels.play_points, () => {
      if (this.playback) { this.stopPlayback(); return; }
      this.stopPlayback(); show(); this.playKey = key; this.playButton = play;
      play.textContent = labels.pause_points;
      const reduced = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      this.playback = setInterval(() => {
        if (!input.isConnected || document.hidden || Number(input.value) >= track.points.length - 1) { this.stopPlayback(); return; }
        input.value = String(Number(input.value) + 1); show();
      }, reduced ? 1500 : 500);
    });
    play.disabled ||= track.points.length < 2;
    this.focusBindings.push({ key, input, status, range });
    label.append(input); controls.append(label, play, status, range, element('p', labels.playback_note, 'note'));
    return controls;
  }
  curve(parent, title, samples, field, key = null) {
    const values = samples.filter(point => Number.isFinite(point[field]));
    const paths = samplePaths(values.map(point => ({ sequence: point.sequence, speed_raw: point[field],
      segment: point[field === 'speed_raw' ? 'speed_segment' : 'distance_segment'] })));
    if (!paths.length) return;
    parent.append(element('p', title, 'note'));
    const svgElement = tag => document.createElementNS('http://www.w3.org/2000/svg', tag);
    const svg = svgElement('svg'); svg.setAttribute('viewBox', '0 0 300 100');
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', title);
    const region = svgElement('rect'); region.setAttribute('y', 0); region.setAttribute('height', 100);
    region.setAttribute('fill', 'var(--primary-color)'); region.setAttribute('opacity', .18); svg.append(region);
    for (const points of paths) {
      const line = svgElement('polyline'); line.setAttribute('points', points); line.setAttribute('fill', 'none');
      line.setAttribute('stroke', 'var(--primary-color)'); line.setAttribute('stroke-width', '2'); svg.append(line);
    }
    const line = svgElement('line'); line.setAttribute('y1', 0); line.setAttribute('y2', 100);
    line.setAttribute('stroke', 'var(--primary-text-color)'); line.setAttribute('pointer-events', 'none'); svg.append(line);
    const first = values[0].sequence, last = values.at(-1).sequence;
    this.curves.push({ key, first, last, line, region });
    if (key) {
      const nearest = event => {
        const bounds = svg.getBoundingClientRect();
        const sequence = first + Math.max(0, Math.min(1, ((event.clientX - bounds.left) / bounds.width * 300 - 8) / 284)) * (last - first);
        return values.reduce((best, value) => Math.abs(value.sequence - sequence) < Math.abs(best.sequence - sequence) ? value : best).sequence;
      };
      let start = null;
      svg.addEventListener('pointermove', event => {
        const sequence = nearest(event);
        cancelAnimationFrame(this.curveFrame);
        this.curveFrame = requestAnimationFrame(() => { if (svg.isConnected) this.selection.focusSample(key, sequence); });
      });
      svg.addEventListener('pointerdown', event => { start = nearest(event); svg.setPointerCapture(event.pointerId); });
      svg.addEventListener('pointerup', event => {
        if (start !== null) this.selection.brushSamples(key, start, nearest(event));
        start = null;
      });
      svg.addEventListener('pointercancel', () => { start = null; });
    }
    parent.append(svg, element('p', `${this.labels().sample_axis}: ${first}–${last}`, 'note'));
  }
  provenance(parent, ride, labels) {
    const info = element('details'); info.append(element('summary', labels.source_fields));
    info.append(element('p', `${ride.source_mode === 'ninecli' ? labels.cloud : labels.local} · ${ride.source ?? '—'} · ${ride.parser_contract ?? '—'} · ${labels.ride_id}: ${ride.ride_id}`, 'note'));
    const text = Object.entries(ride.field_provenance).filter(([, value]) => value)
      .map(([key, value]) => `${key}: ${value}`).join(' · ');
    if (text) info.append(element('p', text, 'note'));
    if (ride.used_electricity_raw !== null) info.append(element('p', `${labels.unknown_units}: ${ride.used_electricity_raw}`, 'note'));
    parent.append(info);
  }
  routeActions(parent, ride, labels) {
    const key = trackKey(ride.device_id, ride), saved = this.tracks.tracks.get(key);
    const detail = this.browser.details.get(key), displayed = detail ?? ride;
    const route = this.button(saved && !saved.complete ? labels.more_track : labels.show_track, () => void this.readRoute(ride));
    route.disabled ||= saved?.complete || displayed.detail_saved === false;
    parent.append(route);
    if (displayed.detail_saved === false) parent.append(element('p', labels.detail_missing, 'note'));
    const source = this.browser.page?.rides.find(row => trackKey(row.device_id, row) === key);
    if (source) {
      const cloud = this.button(labels.cloud_detail, () => void this.browser.detail(source));
      cloud.disabled ||= !source.detail_id; parent.append(cloud);
    }
  }
  render() {
    if (this.resetting || !this.browser?.config) return;
    this.stopPlayback(); cancelAnimationFrame(this.curveFrame);
    const active = this.shadowRoot.activeElement?.dataset.control;
    const scroll = this.shadowRoot.querySelector('.rides')?.scrollTop ?? 0;
    this.focusBindings = []; this.curves = [];
    const browser = this.browser, labels = this.labels(), card = element('ha-card');
    card.append(element('h2', browser.config.title ?? labels.title));
    const toolbar = element('div', '', 'toolbar');
    if (browser.targets.length > 1) browser.targets.forEach((target, index) => {
      const label = element('label', target.name ?? browser.hass?.states?.[target.entity]?.attributes?.friendly_name ?? target.entity ?? target.device_id);
      const input = element('input'); input.type = 'checkbox'; input.checked = browser.selected.has(index);
      input.disabled = browser.busy || this.tracks.busy;
      input.addEventListener('change', () => browser.select(index, input.checked)); label.prepend(input); toolbar.append(label);
    });
    for (const [key, text] of [['start', labels.start_date], ['end', labels.end_date]]) {
      const label = element('label', text), input = element('input'); input.type = 'date'; input.value = browser[key];
      input.disabled = browser.busy || this.tracks.busy; input.setAttribute('aria-label', text); input.dataset.control = key;
      input.addEventListener('change', () => browser.dates(key === 'start' ? input.value : browser.start, key === 'end' ? input.value : browser.end));
      label.append(input); toolbar.append(label);
    }
    for (const kind of ['recent', 'last_7', 'last_30', 'previous_month', 'next_month']) toolbar.append(this.button(labels[kind], () => browser.range(kind)));
    toolbar.append(this.button(labels.refresh, () => void browser.read()));
    const filters = element('details', '', 'filters'); filters.open = this.filtersOpen === true;
    filters.append(element('summary', `${labels.filters} · ${browser.start}–${browser.end}`), toolbar);
    filters.addEventListener('toggle', () => { if (filters.isConnected) this.filtersOpen = filters.open; });
    card.append(filters);
    card.append(element('p', `${labels.local} · Asia/Shanghai · ${labels.workbench_note}`, 'note'));
    for (const [text, style] of [[labels[browser.error], 'error'], [browser.busy || this.tracks.busy ? labels.loading : null, 'note'],
      [this.tracks.error ? labels[this.tracks.error] ?? labels.track_error : null, 'error'], [this.notice, 'note']]) if (text) card.append(element('p', text, style));
    const bench = element('div', '', 'workbench'), main = element('section', '', 'main'), list = element('section', '', 'rides');
    const detailsArea = element('section', '', 'ride-detail');
    main.setAttribute('aria-label', labels.map); list.setAttribute('aria-label', labels.title);
    bench.append(main, list, detailsArea); card.append(bench);
    const selected = [...this.selection.selected.entries()], chips = element('div', '', 'chips');
    for (const [key, ride] of selected) {
      const button = this.button(`${ride.vehicle_label} · ${this.timestamp(ride.start_time)}`, () => this.selection.activate(key));
      button.setAttribute('aria-pressed', key === this.selection.primary); chips.append(button);
    }
    main.append(chips);
    const tracks = [...this.tracks.tracks.values()].filter(track => this.selection.selected.has(track.key));
    if (tracks.length) {
      const controls = element('div', '', 'toolbar');
      controls.append(this.button(labels.fit_routes, () => this.routeMap.fit()),
        this.button(this.tiles ? labels.disable_tiles : labels.enable_tiles, () => { this.tiles = !this.tiles; this.render(); }),
        this.button(labels.clear_tracks, () => { this.clearPrivate(); this.render(); }));
      main.append(controls, element('p', labels.coordinate_note, 'note'), element('p', labels.tile_privacy, 'note'));
      if (this.mapError) main.append(element('p', labels.map_error, 'error')); else main.append(this.routeMap.element);
    } else { this.routeMap.clear(); main.append(element('p', labels.map_placeholder, 'placeholder')); }
    if (selected.length > 1) {
      const values = selected.map(([key, ride]) => browser.details.get(key) ?? ride);
      // These are selected-record totals, never a claim about all filtered history.
      const total = field => values.every(ride => Number.isFinite(ride[field])) ? values.reduce((n, ride) => n + ride[field], 0) : null;
      main.append(element('p', `${labels.selected_total} (${values.length}) · ${this.number(total('distance_m') === null ? null : total('distance_m') / 1000, 1, 'km')} · ${this.duration(total('duration_h'))} · ${this.number(total('energy_wh'), 0, 'Wh')}`, 'note'));
    }
    const primary = this.selection.primary, ride = this.selection.selected.get(primary);
    if (ride) {
      const detail = browser.details.get(primary), displayed = detail ?? ride;
      const panel = element('section', '', 'primary'); panel.dataset.key = primary;
      panel.append(element('h3', `${ride.vehicle_label} · ${this.timestamp(ride.start_time)}`), this.metrics(displayed, labels));
      this.routeActions(panel, ride, labels);
      panel.append(this.button(labels.remove_track, () => this.remove(primary)));
      const track = this.tracks.tracks.get(primary);
      if (track) {
        panel.append(element('p', `${labels.track_points}: ${track.valid_read}/${track.total ?? '—'} · ${track.kind === 'server_simplified' ? labels.simplified : labels.unverified_track}` +
          (track.capped ? ` · ${labels.display_limit}` : '') + (track.complete ? '' : ` · ${labels.partial_track}`), 'note'));
        if (track.partial) panel.append(element('p', labels.partial_archive, 'note'));
        panel.append(this.pointControls(primary, track, labels));
        this.curve(panel, labels.samples, track.points, 'speed_raw', primary);
        this.curve(panel, labels.distance_samples, track.points, 'distance_delta_raw', primary);
      } else {
        this.curve(panel, labels.samples, displayed.speed_samples, 'speed_raw', primary);
        this.curve(panel, labels.distance_samples, displayed.distance_samples, 'distance_delta_raw', primary);
      }
      if (displayed.local_partial) panel.append(element('p', labels.partial_archive, 'note'));
      this.provenance(panel, displayed, labels); detailsArea.append(panel);
    }
    const page = browser.page;
    if (page) {
      const coverage = page.coverage;
      list.append(element('p', `${labels.coverage} · ${labels.missing}: ${coverage?.missing_months?.length ?? '—'} · ${labels.partial}: ${coverage?.partial_months?.length ?? '—'} · ${labels.unknown}: ${coverage?.unknown_months?.length ?? '—'} · ${labels.enumerated}: ${coverage?.enumerated_months?.length ?? '—'}`, 'note'));
      if (!page.rides.length) list.append(element('p', labels.empty));
      for (const ride of page.rides) {
        const key = trackKey(ride.device_id, ride), section = element('article', '', `ride${key === primary ? ' selected' : ''}`);
        section.dataset.key = key;
        const choose = this.button(`${ride.vehicle_label} · ${this.timestamp(ride.start_time)}\n${this.number(ride.distance_m === null ? null : ride.distance_m / 1000, 1, 'km')} · ${this.duration(ride.duration_h)}`, () => this.choose(ride));
        choose.dataset.control = `ride:${key}`; choose.setAttribute('aria-pressed', key === primary);
        choose.addEventListener('keydown', event => {
          if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
          event.preventDefault(); const index = page.rides.indexOf(ride), next = page.rides[index + (event.key === 'ArrowDown' ? 1 : -1)];
          if (next) { this.choose(next); this.shadowRoot.querySelectorAll('.ride button')[page.rides.indexOf(next)]?.focus({ preventScroll: true }); }
        });
        const label = element('label', labels.compare, 'compare'), compare = element('input'); compare.type = 'checkbox';
        compare.checked = this.selection.selected.has(key); compare.disabled = browser.busy || this.tracks.busy;
        compare.setAttribute('aria-label', `${labels.compare} · ${ride.vehicle_label} · ${this.timestamp(ride.start_time)}`);
        compare.addEventListener('change', () => compare.checked ? this.choose(ride, true) : this.remove(key));
        label.prepend(compare); section.append(choose, label); list.append(section);
      }
      const footer = element('footer'); footer.append(element('span', `${labels.page} ${browser.pageNumber} · ${page.rides.length}`, 'note'));
      const next = this.button(labels.next, () => void browser.read(page.next_cursor)); next.disabled ||= !page.next_cursor; footer.append(next); list.append(footer);
    }
    const native = nativeCards(browser.targets.filter((_, index) => browser.selected.has(index)), browser.config.statistics_entities ?? []);
    if (native.cards.length) {
      const help = element('details'); help.append(element('summary', labels.native_cards), element('p', labels.native_note, 'note'), element('pre', JSON.stringify(native, null, 2), 'note'));
      const link = element('a', labels.open_calendar); link.href = '/calendar'; help.append(link); card.append(help);
    }
    card.append(element('small', 'Ninebot · 2.0.0b91', 'note'));
    this.shadowRoot.replaceChildren(element('style', CSS), card);
    this.shadowRoot.querySelector('.rides').scrollTop = scroll;
    if (active) [...this.shadowRoot.querySelectorAll('[data-control]')].find(item => item.dataset.control === active)?.focus({ preventScroll: true });
    this.updateFocus();
    if (tracks.length && !this.mapError) void this.routeMap.update(tracks, this.tiles === true, primary, this.selection.brush).then(() => {
      if (this.pendingFit && this.routeMap.groups.has(this.pendingFit)) { this.routeMap.fit(); this.pendingFit = null; }
    });
  }
}
if (globalThis.customElements && !customElements.get('ninebot-trip-card')) {
  customElements.define('ninebot-trip-card', NinebotTripCard);
  globalThis.customCards ??= [];
  globalThis.customCards.push({ type: 'ninebot-trip-card', name: 'Ninebot Ride History',
    description: 'Linked local history map, list, comparison and sample curves', preview: true });
}
