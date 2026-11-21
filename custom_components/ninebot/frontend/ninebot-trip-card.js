/** Ride selection and local sample replay on a single persistent map surface. */
import { LABELS } from './labels.js';
import { formatRideDuration, formatRideInterval, formatEntityLabel } from './trip-format.js';
import { watchHistory } from './history-network.js';
import { digits } from './trip-browser.js';
import { FleetBrowser } from './fleet-browser.js';
import { hasEndpoints, LocalTracks, trackKey } from './local-tracks.js';
import { RouteMap, ROUTE_COLORS } from './route-map.js';
import { HistorySelection } from './history-selection.js';
import { HISTORY_STYLE } from './trip-card-style.js';
import './trip-card-editor.js';

export { TripBrowser, safeRide, businessDates, digits, samplePolyline, samplePaths } from './trip-browser.js';

function element(tag, text = '', className = '') {
  const result = document.createElement(tag);
  result.textContent = text;
  if (className) result.className = className;
  return result;
}

export class NinebotTripCard extends (globalThis.HTMLElement ?? class {}) {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.selection = new HistorySelection(() => this.render());
    this.routeMap = new RouteMap(() => {
      this.mapError = true;
      this.render();
    });
    this.tracks = new LocalTracks((service, data) => this.browser.invoke(service, data), () => this.render());
    this.browser = new FleetBrowser(() => this.historyChanged(), () => this.clearPrivate());
    this.index = 0;
    this.visibility = () => {
      if (document.hidden) this.stopPlayback();
    };
  }

  clearPrivate() {
    this.stopWatch();
    this.pendingUpdates = false;
    this.feedStatus = null;
    this.resetting = true;
    this.stopPlayback();
    this.index = 0;
    this.mapError = false;
    this.selection.clear();
    this.tracks.clear();
    this.routeMap.clear();
    this.resetting = false;
  }

  historyChanged() {
    const { browser } = this;
    if (!browser) return;
    const first = browser.page?.rides[0];
    if (!browser.busy && first && !this.selection.primary) this.choose(first);
    if (!browser.busy && browser.page) this.ensureWatch();
    this.render();
  }

  stopWatch() {
    this.unwatch?.();
    this.unwatch = null;
    this.watchSignature = null;
  }

  bindConnection() {
    const connection = this.browser.hass?.connection;
    if (this.boundConnection === connection) return;
    this.unbindConnection();
    if (!connection?.addEventListener) return;
    this.boundConnection = connection;
    this.connectionLost = () => this.browser.setHass({ ...this.browser.hass, connected: false });
    this.connectionReady = () => this.browser.setHass({ ...this.browser.hass, connected: true });
    connection.addEventListener('disconnected', this.connectionLost);
    connection.addEventListener('ready', this.connectionReady);
  }

  unbindConnection() {
    this.boundConnection?.removeEventListener('disconnected', this.connectionLost);
    this.boundConnection?.removeEventListener('ready', this.connectionReady);
    this.boundConnection = null;
  }

  ensureWatch() {
    const devices = [...this.browser.resolved.values()].sort();
    if (!devices.length || !this.isConnected) return;
    const signature = JSON.stringify([this.browser.hass.user.id, devices]);
    if (signature === this.watchSignature) return;
    this.stopWatch();
    this.watchSignature = signature;
    const epoch = this.browser.epoch;
    const stop = watchHistory(this.browser.hass, devices, event => {
      if (this.browser.current(epoch)) this.historyUpdate(event);
    });
    if (signature === this.watchSignature) this.unwatch = stop;
    else stop();
  }

  historyUpdate(event) {
    if (event.type === 'denied') {
      this.browser.reset();
      this.browser.error = 'denied';
      this.render();
      return;
    }
    if (event.type === 'retrying' && event.reason !== 'temporary') {
      // An invalidated view must drop its GPS even while the feed reconnects.
      this.resetting = true;
      this.stopPlayback();
      this.selection.clear();
      this.tracks.clear();
      this.routeMap.clear();
      this.browser.page = null;
      this.browser.streams = [];
      this.browser.details.clear();
      this.resetting = false;
    }
    if (['retrying', 'unavailable', 'unsupported'].includes(event.type)) {
      this.feedStatus = `feed_${event.type}`;
      this.render();
      return;
    }
    this.feedStatus = null;
    if (!this.browser.page) {
      this.browser.error = null;
      void this.browser.read();
      return;
    }
    const changed = event.vehicles.some(row =>
      this.browser.streams.find(stream => stream.device === row.device_id)?.revision !== row.selection_version);
    if (event.type === 'changed' || changed) {
      this.pendingUpdates = true;
      for (const track of this.tracks.tracks.values()) track.stale = true;
      if (changed) this.browser.page.next_cursor = null;
    }
    this.render();
  }

  async refreshLocal() {
    this.stopPlayback();
    this.stopWatch();
    this.pendingUpdates = false;
    for (const track of this.tracks.tracks.values()) track.stale = true;
    const epoch = this.browser.epoch;
    await this.browser.read();
    if (!this.browser.current(epoch) || this.browser.error) return;
    for (const ride of this.browser.page?.rides ?? []) this.selection.update(ride);
    const ride = this.activeRide;
    if (ride && ride.detail_saved !== false) await this.readRoute(ride);
    this.render();
  }

  setConfig(config) { this.browser.configure(config); }
  set hass(hass) {
    const oldLanguage = this.browser.hass?.language;
    this.browser.setHass(hass);
    if (this.isConnected) this.bindConnection();
    if (oldLanguage !== hass.language) this.render();
  }
  connectedCallback() {
    document.addEventListener('visibilitychange', this.visibility);
    this.bindConnection();
    this.browser.connect();
    this.render();
  }
  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this.visibility);
    this.unbindConnection();
    this.browser.disconnect();
  }
  getCardSize() { return 10; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }
  static getConfigElement() { return document.createElement('ninebot-trip-card-editor'); }
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(id =>
      id.startsWith('calendar.') && hass.states[id].attributes.source === 'ride_archive');
    return entity ? { entity } : { device_id: 'REPLACE_WITH_VEHICLE_DEVICE_ID' };
  }

  labels() {
    const locale = this.browser.hass?.language ?? 'en';
    return LABELS[locale] ?? (locale.startsWith('zh') ? LABELS['zh-Hans'] : LABELS[locale.split('-')[0]]) ?? LABELS.en;
  }
  number(value, precision, unit = '') {
    if (!Number.isFinite(value)) return '—';
    const language = this.browser.hass?.locale?.language ?? this.browser.hass?.language ?? 'en';
    const places = value === 0 ? 0 : digits(precision);
    const n = new Intl.NumberFormat(language, { minimumFractionDigits: places, maximumFractionDigits: places }).format(value);
    return `${n}${unit ? ` ${unit}` : ''}`;
  }
  duration(value, seconds) {
    const locale = this.browser.hass?.locale?.language ?? this.browser.hass?.language ?? 'en';
    return formatRideDuration(seconds, value, locale);
  }
  timestamp(value) {
    if (!value || !Number.isFinite(Date.parse(value))) return '—';
    return new Intl.DateTimeFormat(this.browser.hass?.language ?? 'en', {
      timeZone: this.browser.hass?.config?.time_zone ?? 'Asia/Shanghai',
      dateStyle: 'medium', timeStyle: 'short',
    }).format(new Date(value));
  }
  metrics(ride) {
    return [
      Number.isFinite(ride.distance_m) ? this.number(ride.distance_m / 1000, ride.precision.mileages, 'km') : null,
      Number.isFinite(ride.duration_s) || Number.isFinite(ride.duration_h) ? this.duration(ride.duration_h, ride.duration_s) : null,
      Number.isFinite(ride.energy_wh) ? this.number(ride.energy_wh, ride.precision.ec, 'Wh') : null,
    ].filter(Boolean).join(' · ');
  }

  get busy() { return this.browser.busy || this.tracks.busy; }
  get activeRide() { return this.selection.selected.get(this.selection.primary); }
  get activeTrack() { return this.tracks.tracks.get(this.selection.primary); }

  choose(ride, compare = false) {
    if (this.busy || !ride?.ride_id || !this.browser.active) return;
    if (compare && this.selection.selected.size >= 5) return;
    this.stopPlayback();
    this.index = 0;
    this.mapError = false;
    if (!this.selection.select(ride, compare)) return;
    // The only automatic detail read is through the authenticated local archive.
    if (ride.detail_saved === true && !this.activeTrack?.complete) void this.readRoute(ride);
    else if (this.activeTrack?.stale) void this.readRoute(ride);
    else if (this.activeTrack) this.routeMap.fit();
  }

  activate(key) {
    if (!this.selection.selected.has(key) || key === this.selection.primary || this.busy) return;
    this.stopPlayback();
    this.index = 0;
    this.selection.activate(key);
    if (this.activeTrack?.stale) void this.readRoute(this.activeRide);
  }

  removeRide(key) {
    if (this.busy) return;
    if (key === this.selection.primary) {
      this.stopPlayback();
      this.index = 0;
    }
    this.selection.remove(key);
  }

  async readRoute(ride = this.activeRide, continuation = false) {
    if (!ride || this.busy || !this.browser.active || ride.detail_saved === false) return;
    const key = trackKey(ride.device_id, ride);
    if (!this.selection.selected.has(key)) return;
    const previous = this.tracks.tracks.get(key);
    this.stopPlayback();
    if (!continuation) this.index = 0;
    this.mapError = false;
    if (!previous && this.tracks.tracks.size >= 5) {
      const inactive = [...this.tracks.tracks.keys()].find(cached => !this.selection.selected.has(cached));
      if (inactive) this.tracks.remove(inactive);
    }
    const epoch = this.browser.epoch;
    await this.tracks.read(ride.device_id, ride, { refresh: previous?.stale === true });
    if (this.browser.current(epoch) && !previous && this.activeTrack?.key === key) this.routeMap.fit();
  }

  stopPlayback() {
    if (this.playback) clearInterval(this.playback);
    this.playback = null;
    this.updateTransport();
  }

  playOrPause() {
    const track = this.activeTrack;
    if (!this.canReplay(track)) return;
    if (this.playback) {
      this.stopPlayback();
      return;
    }
    if (this.index >= track.points.length - 1) this.index = 0;
    const step = Math.max(1, Math.ceil((track.points.length - 1) / 250));
    this.playback = setInterval(() => {
      this.index = Math.min(this.index + step, track.points.length - 1);
      if (this.index === track.points.length - 1) this.stopPlayback();
      else this.updateTransport();
    }, 100);
    this.updateTransport();
  }

  canReplay(track) {
    return track?.kind === 'sampled_unverified' && track.points.length > 1;
  }

  button(text, action, className = '') {
    const button = element('button', text, className);
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
  }
  iconButton(icon, action, className) {
    const button = this.button('', action, className);
    const symbol = element('ha-icon');
    symbol.setAttribute('icon', icon);
    button.append(symbol);
    // Browser test fixtures and minimal dashboards can render without HA icons.
    symbol.textContent = { 'mdi:play': '▶', 'mdi:rewind': '↶', 'mdi:fit-to-screen-outline': '⌖' }[icon] ?? '';
    return button;
  }
  nameControl(control, text) {
    control.title = text;
    control.setAttribute('aria-label', text);
  }

  installLayout() {
    const card = element('ha-card'), bar = element('header', '', 'bar');
    const title = element('h2'), filters = element('details', '', 'filters'), range = element('summary');
    const toolbar = element('div', '', 'toolbar'), messages = element('div', '', 'messages');
    messages.setAttribute('role', 'status');
    filters.append(range, toolbar);
    bar.append(title, filters);
    const layout = element('div', '', 'layout'), player = element('section', '', 'player');
    const frame = element('div', '', 'map-frame'), placeholder = element('p', '', 'map-placeholder');
    this.routeMap.element.className = 'map-surface';
    const fit = this.iconButton('mdi:fit-to-screen-outline', () => this.routeMap.fit(), 'fit');
    frame.append(this.routeMap.element, placeholder, fit);
    const transport = element('div', '', 'transport'), now = element('div');
    const name = element('p', '', 'track-title'), metrics = element('p', '', 'ride-metrics');
    now.append(name, metrics);
    const compare = element('div', '', 'compare-list'), controls = element('div', '', 'controls');
    const restart = this.iconButton('mdi:rewind', () => { this.index = 0; this.stopPlayback(); });
    const play = this.iconButton('mdi:play', () => this.playOrPause(), 'play');
    restart.dataset.control = 'restart';
    play.dataset.control = 'play';
    const scrubber = element('label', '', 'scrubber'), slider = element('input'), sample = element('span');
    slider.type = 'range'; slider.min = '0'; slider.step = '1'; slider.dataset.control = 'scrub';
    slider.addEventListener('input', () => { this.index = Number(slider.value); this.stopPlayback(); });
    scrubber.append(slider, sample);
    controls.append(restart, play, scrubber);
    const quality = element('div', '', 'note'), more = element('div');
    transport.append(now, compare, controls, quality, more);
    player.append(frame, transport);
    const list = element('section', '', 'rides'), listHeading = element('h3');
    const coverage = element('p', '', 'note'), items = element('div', '', 'items'), pager = element('footer', '', 'pager');
    list.append(listHeading, coverage, items, pager);
    layout.append(player, list);
    card.append(bar, messages, layout);
    this.shadowRoot.append(element('style', HISTORY_STYLE), card);
    this.ui = { title, range, toolbar, messages, placeholder, fit, name, metrics, compare, controls, restart,
      play, slider, sample, quality, more, listHeading, coverage, items, pager };
  }

  updateTransport() {
    if (this.resetting || !this.ui) return;
    const { controls, restart, play, slider, sample } = this.ui;
    const track = this.activeTrack, labels = this.labels();
    const enabled = this.canReplay(track);
    controls.hidden = !enabled;
    this.nameControl(restart, labels.replay_restart);
    this.nameControl(play, this.playback ? labels.pause_points : labels.play_points);
    const icon = play.firstElementChild;
    icon.setAttribute('icon', this.playback ? 'mdi:pause' : 'mdi:play');
    icon.textContent = this.playback ? '⏸' : '▶';
    play.disabled = this.busy;
    restart.disabled = this.busy;
    slider.disabled = this.busy;
    const index = Math.min(this.index, (track?.points.length ?? 1) - 1);
    slider.max = String(Math.max(0, (track?.points.length ?? 1) - 1));
    slider.value = String(Math.max(0, index));
    slider.setAttribute('aria-label', labels.point_sequence);
    const point = enabled ? track.points[index] : null;
    sample.textContent = point ? `${index + 1} / ${track.points.length}` : '';
    slider.setAttribute('aria-valuetext', point ? `${labels.point_sequence} ${point.sequence}` : '');
    this.routeMap.highlight(point);
  }

  renderFilters(labels) {
    const { browser, ui } = this;
    ui.range.textContent = `${labels.filters} · ${browser.start} – ${browser.end}`;
    const fragment = document.createDocumentFragment();
    if (browser.targets.length > 1) browser.targets.forEach((target, index) => {
      const label = element('label', target.name ?? formatEntityLabel(browser.hass,
        browser.hass?.states?.[target.entity], target.entity ?? target.device_id), 'check');
      const input = element('input');
      input.type = 'checkbox';
      input.checked = browser.selected.has(index);
      input.disabled = this.busy;
      input.dataset.control = `vehicle:${index}`;
      input.addEventListener('change', () => browser.select(index, input.checked));
      label.prepend(input);
      fragment.append(label);
    });
    for (const [key, title] of [['start', labels.start_date], ['end', labels.end_date]]) {
      const label = element('label', title), input = element('input');
      input.type = 'date';
      input.value = browser[key];
      input.required = true;
      input.disabled = this.busy;
      input.dataset.control = key;
      input.addEventListener('change', () => {
        if (input.value) browser.dates(key === 'start' ? input.value : browser.start,
          key === 'end' ? input.value : browser.end);
      });
      label.append(input);
      fragment.append(label);
    }
    const quick = element('div', '', 'quick');
    for (const kind of ['last_7', 'last_30', 'previous_month', 'next_month', 'recent']) {
      const button = this.button(labels[kind], () => browser.range(kind));
      button.disabled = this.busy;
      button.dataset.control = kind;
      quick.append(button);
    }
    const refresh = this.button(labels.refresh, () => void this.refreshLocal());
    refresh.disabled = this.busy || browser.start > browser.end || !browser.selected.size;
    refresh.dataset.control = 'refresh';
    quick.append(refresh);
    fragment.append(quick);
    ui.toolbar.replaceChildren(fragment);
  }

  renderMessages(labels) {
    const status = [];
    const error = this.browser.error || this.tracks.error;
    if (error) status.push(element('p', labels[error] ?? labels.track_error, 'error'));
    if (this.busy) status.push(element('p', labels.loading, 'note'));
    if (this.pendingUpdates) {
      const refresh = this.button(labels.history_updated, () => void this.refreshLocal());
      refresh.disabled = this.busy;
      refresh.dataset.control = 'latest';
      status.push(refresh);
    }
    if (this.feedStatus) status.push(element('p', labels[this.feedStatus], 'note'));
    this.ui.messages.replaceChildren(...status);
  }

  renderRideList(labels) {
    const { page, pageNumber } = this.browser;
    const { listHeading, coverage, items, pager } = this.ui;
    listHeading.textContent = labels.title;
    coverage.textContent = page ? [
      [labels.enumerated, page.coverage.enumerated_months],
      [labels.partial, page.coverage.partial_months],
      [labels.missing, page.coverage.missing_months],
      [labels.unknown, page.coverage.unknown_months],
    ].filter(([, value]) => value?.length).map(([label, value]) => `${label} ${value.length}`).join(' · ') : '';
    const rows = page?.rides ?? [];
    const scroll = items.scrollTop;
    items.replaceChildren(...rows.map((ride, index) => {
      const key = trackKey(ride.device_id, ride), row = element('article', '', 'ride');
      const selected = this.selection.selected.has(key);
      const choose = this.button('', () => this.choose(ride), 'choose');
      choose.append(element('strong', ride.vehicle_label), element('small',
        [this.timestamp(ride.start_time), this.metrics(ride)].filter(Boolean).join(' · ')));
      choose.dataset.control = `ride:${index}`;
      choose.disabled = this.busy;
      choose.setAttribute('aria-pressed', String(key === this.selection.primary));
      choose.addEventListener('keydown', event => {
        const nextIndex = index + (event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0);
        if (nextIndex === index || !rows[nextIndex]) return;
        event.preventDefault();
        items.querySelectorAll('.choose')[nextIndex]?.focus();
      });
      const overlay = this.button(selected ? '−' : '+',
        () => selected ? this.removeRide(key) : this.choose(ride, true), 'compare');
      this.nameControl(overlay, `${selected ? labels.remove_track : labels.compare} · ${ride.vehicle_label} · ${this.timestamp(ride.start_time)}`);
      overlay.setAttribute('aria-pressed', String(selected));
      overlay.dataset.control = `overlay:${index}`;
      overlay.disabled = this.busy || (!selected && this.selection.selected.size >= 5);
      row.append(choose, overlay);
      return row;
    }));
    if (page && !rows.length) items.append(element('p', labels.empty, 'note'));
    items.scrollTop = scroll;
    const next = this.button(labels.next, () => void this.browser.read(page.next_cursor));
    next.disabled = this.busy || !page?.next_cursor;
    next.dataset.control = 'next';
    pager.replaceChildren(...(page && (page.next_cursor || pageNumber > 1) ?
      [element('span', `${labels.page} ${pageNumber}`, 'note'), next] : []));
  }

  renderMap(labels) {
    const { ui } = this;
    const active = this.activeRide, track = this.activeTrack;
    const tracks = [...this.selection.selected.keys()].map(key => this.tracks.tracks.get(key))
      .filter(item => item?.points.length);
    const showMap = tracks.length > 0 && !this.mapError;
    this.routeMap.element.hidden = !showMap;
    ui.fit.hidden = !showMap;
    ui.placeholder.hidden = showMap;
    ui.placeholder.textContent = this.mapError ? labels.map_error : track && !track.points.length ?
      labels.saved_without_points : active?.detail_saved === false ? labels.detail_missing :
        this.busy ? labels.loading : labels.map_placeholder;
    this.nameControl(ui.fit, labels.fit_routes);
    ui.name.textContent = active?.vehicle_label ?? '';
    const interval = active && formatRideInterval(active.start_time, active.end_time,
      this.browser.hass?.language ?? 'en', this.browser.hass?.config?.time_zone ?? 'Asia/Shanghai');
    ui.metrics.textContent = active ? [interval, this.metrics(track?.summary ?? active)].filter(Boolean).join(' · ') : '';
    const compare = this.selection.selected.size > 1 ? [...this.selection.selected].map(([key, ride]) => {
      const index = tracks.findIndex(item => item.key === key);
      const chip = this.button(ride.vehicle_label, () => this.activate(key));
      chip.setAttribute('aria-pressed', String(key === this.selection.primary));
      chip.dataset.control = `focus:${key}`;
      chip.disabled = this.busy;
      if (index >= 0) {
        const swatch = element('span', '', 'swatch');
        swatch.style.background = ROUTE_COLORS[index];
        chip.prepend(swatch);
      }
      return chip;
    }) : [];
    ui.compare.replaceChildren(...compare);
    const notes = [];
    if (track) {
      notes.push(`${labels.track_points} ${track.valid_read}/${track.total ?? '—'}`);
      if (hasEndpoints(track)) {
        notes.push(labels.endpoints_only);
      } else if (track.kind === 'server_simplified' || track.kind === 'single_point') {
        notes.push(labels.simplified);
      }
      if (track.kind === 'server_simplified' && track.windowDays) {
        const days = this.number(track.windowDays, 0);
        notes.push(labels.detail_window.replace('{days}', days));
      }
      if (track.partial) notes.push(labels.partial_archive);
      if (track.capped) notes.push(labels.display_limit);
      if (this.canReplay(track)) notes.push(labels.playback_note);
    }
    if (showMap) notes.push(labels.coordinate_note);
    ui.quality.textContent = notes.join(' · ');
    if (active && active.detail_saved !== false && (!track?.complete || track.stale || this.mapError)) {
      const more = this.button(track && !track.complete && !track.stale ? labels.more_track : labels.show_track,
        () => void this.readRoute(active, Boolean(track && !track.stale)));
      more.disabled = this.busy;
      more.dataset.control = 'route';
      ui.more.replaceChildren(more);
    } else {
      ui.more.replaceChildren();
    }
    if (showMap) {
      const content = ui.quality.textContent;
      void this.routeMap.update(tracks, this.browser.hass, labels).then(() => {
        if (this.routeMap.renderer?.simplified && ui.quality.textContent === content && !track?.capped) {
          ui.quality.textContent += ` · ${labels.display_limit}`;
        }
      });
    } else {
      void this.routeMap.update([], this.browser.hass, labels);
    }
    this.updateTransport();
  }

  render() {
    if (this.resetting || !this.browser?.config || !this.isConnected) return;
    if (!this.ui) this.installLayout();
    const active = this.shadowRoot.activeElement?.dataset.control;
    const labels = this.labels();
    this.ui.title.textContent = this.browser.config.title ?? labels.map;
    this.renderFilters(labels);
    this.renderMessages(labels);
    this.renderRideList(labels);
    this.renderMap(labels);
    if (active && this.shadowRoot.activeElement?.dataset.control !== active) {
      [...this.shadowRoot.querySelectorAll('[data-control]')].find(item =>
        item.dataset.control === active)?.focus({ preventScroll: true });
    }
  }
}

if (globalThis.customElements && !customElements.get('ninebot-trip-card')) {
  customElements.define('ninebot-trip-card', NinebotTripCard);
  globalThis.customCards ??= [];
  globalThis.customCards.push({ type: 'ninebot-trip-card', name: 'Ninebot Ride History',
    description: 'Browse and replay saved Ninebot rides on a shared map', preview: true });
}
