/** Ride selection and local sample replay on a single persistent map surface. */
import { LABELS } from './labels.js';
import { watchHistory } from './history-network.js';
import { FleetBrowser } from './fleet-browser.js';
import { LocalTracks, trackKey } from './local-tracks.js';
import { RouteMap } from './route-map.js';
import { HistorySelection, MAX_SELECTED_ROUTES } from './history-selection.js';
import { TripCardView } from './trip-card-view.js';
import './trip-card-editor.js';

export { TripBrowser, safeRide, businessDates, digits, samplePolyline, samplePaths } from './trip-browser.js';

export class NinebotTripCard extends (globalThis.HTMLElement ?? class {}) {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.selection = new HistorySelection(() => this.render());
    this.routeMap = new RouteMap(() => {
      this.mapError = true;
      this.render();
    }, () => this.view?.playbackInset() ?? 0, keys => {
      if (this.busy) return;
      if (keys.length === 1) this.activate(keys[0]);
      else if (keys.length) this.view.chooseRoute(keys);
    });
    this.tracks = new LocalTracks((service, data) => this.browser.invoke(service, data), () => this.render());
    this.browser = new FleetBrowser(() => this.historyChanged(), () => this.clearPrivate(), () => {
      if (!this.feedReady || this.busy) return false;
      this.stopPlayback();
      for (const key of this.tracks.tracks.keys()) {
        if (!this.selection.selected.has(key)) this.tracks.tracks.delete(key);
      }
      return true;
    });
    this.index = 0;
    this.visibility = () => {
      if (document.hidden) this.stopPlayback();
    };
  }

  clearPrivate() {
    this.stopWatch();
    this.feedStatus = null;
    this.clearRoutes();
  }

  clearRoutes() {
    this.pendingUpdates = false;
    this.resetting = true;
    this.view?.dismissRouteChoices();
    this.follow = false;
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
    this.feedReady = false;
    this.awaitingBaseline = false;
    this.watchToken = null;
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
    const token = this.watchToken = {};
    const stop = watchHistory(this.browser.hass, devices, event => {
      if (token === this.watchToken && this.browser.active) this.historyUpdate(event);
    });
    if (token === this.watchToken) this.unwatch = stop;
    else stop();
  }

  historyUpdate(event) {
    this.feedReady = event.type === 'baseline' || event.type === 'changed';
    if (event.type === 'denied') {
      this.browser.reset();
      this.browser.error = 'denied';
      this.render();
      return;
    }
    if (['retrying', 'unavailable'].includes(event.type) && event.reason !== 'temporary') {
      // Retire in-flight pages while the same watcher waits for a fresh baseline.
      this.browser.clearPage();
      this.browser.awaitApply = true;
      this.awaitingBaseline = true;
      this.clearRoutes();
    }
    if (['retrying', 'unavailable', 'unsupported'].includes(event.type)) {
      this.feedStatus = `feed_${event.type}`;
      this.render();
      return;
    }
    this.feedStatus = null;
    if (!this.browser.page) {
      if (this.browser.awaitApply && !this.awaitingBaseline) {
        if (event.type === 'changed') {
          this.pendingUpdates = true;
          for (const track of this.tracks.tracks.values()) track.stale = true;
        }
        this.render();
        return;
      }
      this.awaitingBaseline = false;
      this.browser.error = null;
      void this.browser.read();
      return;
    }
    const changed = event.vehicles.some(row =>
      this.browser.streams.find(stream => stream.device === row.device_id)?.revision !== row.selection_version);
    if (event.type === 'changed' || changed) {
      this.pendingUpdates = true;
      for (const track of this.tracks.tracks.values()) track.stale = true;
      this.browser.forgetPages(true);
    }
    this.render();
  }

  async refreshLocal() {
    if (this.browser.awaitApply && !this.pendingUpdates && !this.awaitingBaseline) {
      await this.browser.read();
      return;
    }
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
  get busy() { return this.browser.busy || this.tracks.busy; }
  get activeRide() { return this.selection.selected.get(this.selection.primary); }
  get activeTrack() { return this.tracks.tracks.get(this.selection.primary); }

  choose(ride, compare = false) {
    if (this.busy || !ride?.ride_id || !this.browser.active) return;
    const key = trackKey(ride.device_id, ride);
    if (this.selection.selected.has(key)) {
      this.activate(key);
      return;
    }
    if (compare && this.selection.selected.size >= MAX_SELECTED_ROUTES) return;
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

  keepCurrentRide() {
    if (!this.busy) this.selection.retainPrimary();
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
    if (!previous && this.tracks.tracks.size >= MAX_SELECTED_ROUTES) {
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
    this.view?.updatePlayback();
  }

  toggleFollow() {
    this.follow = !this.follow;
    this.view.updatePlayback();
    if (this.follow) this.routeMap.followPoint(true);
  }

  fitRoutes() {
    this.follow = false;
    this.view.updatePlayback();
    this.routeMap.fit();
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
      else this.view?.updatePlayback();
    }, 100);
    this.view?.updatePlayback();
  }

  canReplay(track) {
    return track?.kind === 'sampled_unverified' && track.points.length > 1;
  }

  render() {
    if (this.resetting || !this.browser?.config || !this.isConnected) return;
    this.view ??= new TripCardView(this);
    this.view.render(this.labels());
  }
}

if (globalThis.customElements && !customElements.get('ninebot-trip-card')) {
  customElements.define('ninebot-trip-card', NinebotTripCard);
  globalThis.customCards ??= [];
  globalThis.customCards.push({ type: 'ninebot-trip-card', name: 'Ninebot Ride History',
    description: 'Browse and replay saved Ninebot rides on a shared map', preview: true });
}
