/** One map surface, with a native basemap where HA supplies the tile service. */
import { hasEndpoints, trackSegments } from './local-tracks.js';
import { createRouteRenderer } from './route-renderer.js';

export const ROUTE_COLORS = ['#168af9', '#e67723', '#9968df', '#1a9b7b', '#de567a'];

const location = point => [point.latitude, point.longitude];

export function routePaths(track) {
  return trackSegments(track.points, track.kind).map(segment => segment.map(location));
}

function spaced(items, limit) {
  if (items.length <= limit) return items;
  return Array.from({ length: limit }, (_, index) =>
    items[Math.round(index * (items.length - 1) / (limit - 1))]);
}

function displayedPaths(track, labels) {
  const paths = routePaths(track);
  const started = track.summary.start_time;
  if (!hasEndpoints(track)) {
    return paths.map(points => ({ points, name: labels.start, time: started }));
  }
  const [start, end] = paths;
  const startLabel = labels.track_start, endLabel = labels.end;
  if (start[0][0] === end[0][0] && start[0][1] === end[0][1]) {
    return [{ points: start, name: `${startLabel} / ${endLabel}`, time: started }];
  }
  return [
    { points: start, name: startLabel, time: started },
    { points: end, name: endLabel, time: track.summary.end_time },
  ];
}

function nativePaths(track, labels) {
  // HA path markers display the trip start or end time. Ordinary samples
  // share the labelled trip start because their timestamps are unknown.
  const paths = spaced(displayedPaths(track, labels), 24);
  const pointsPerPath = Math.max(2, Math.floor(720 / Math.max(1, paths.length)));
  return paths.flatMap(path => {
    const time = new Date(path.time ?? NaN);
    if (!Number.isFinite(time.getTime())) return [];
    return [{ name: path.name, color: track.color, fullDatetime: true,
      points: spaced(path.points, pointsPerPath).map(point => ({ point, timestamp: time })) }];
  });
}

async function loadNativeMap(hass) {
  if (!hass?.config?.components?.includes('map_tiles')) return null;
  if (!customElements.get('ha-map') && typeof window.loadCardHelpers === 'function') {
    const helpers = await window.loadCardHelpers();
    // Loads the normal Map card module without connecting another map to the page.
    await helpers.createCardElement({ type: 'map', entities: [] });
    if (!customElements.get('ha-map')) await customElements.whenDefined('ha-map');
  }
  if (!customElements.get('ha-map')) return null;
  const map = document.createElement('ha-map');
  return 'paths' in map && 'editableLocations' in map && typeof map.getView === 'function' &&
    typeof map.fitBounds === 'function' ? map : null;
}

class NativeRouteMap {
  constructor(map, host) {
    this.map = map;
    this.canFollow = typeof map.setView === 'function' && typeof map.containsLocation === 'function';
    this.cursor = document.createElement('div');
    this.cursor.style.cssText = 'width:18px;height:18px;border:3px solid white;border-radius:100%;background:#168af9;box-shadow:0 1px 6px #0008';
    map.style.cssText = 'display:block;width:100%;height:100%';
    map.autoFit = false;
    if ('scaleRuler' in map) map.scaleRuler = true;
    host.append(map);
  }
  setRoutes(tracks, labels) {
    this.map.paths = tracks.flatMap(track => nativePaths(track, labels));
    this.bounds = tracks.flatMap(trackBounds);
    this.simplified = this.map.paths.reduce((total, path) => total + path.points.length, 0) <
      tracks.reduce((total, track) => total + track.points.length, 0);
  }
  followPoint(point, center) {
    const coords = location(point);
    if (this.canFollow && (center || !this.map.containsLocation(coords))) {
      this.map.setView(coords, this.map.getView()?.zoom);
    }
  }
  highlight(point, color) {
    if (color) this.cursor.style.backgroundColor = color;
    this.map.editableLocations = point ? [{ id: 'ninebot-replay', location: location(point),
      element: this.cursor, elementSize: [18, 18], locationEditable: false, fit: false }] : [];
  }
  fit(bottomInset) {
    if (this.bounds?.length) this.map.fitBounds(this.bounds, {
      zoom: 16, pad: 0.16, padding: bottomInset ? { bottom: bottomInset } : undefined,
    });
  }
  destroy() {
    this.map.paths = [];
    this.map.editableLocations = [];
    this.map.remove();
  }
}

function trackBounds(track) {
  if (track.bounds) {
    const [south, west, north, east] = track.bounds;
    return [[south, west], [north, east]];
  }
  return track.points.map(location);
}

class OfflineRouteMap {
  constructor(L, host) {
    this.library = L;
    this.container = document.createElement('div');
    this.container.style.cssText = 'width:100%;height:100%;background:var(--replay-surface)';
    host.append(this.container);
    this.renderer = createRouteRenderer(L);
    this.map = L.map(this.container, { renderer: this.renderer, preferCanvas: true, minZoom: 1,
      scrollWheelZoom: false, zoomAnimation: false }).setView([0, 0], 1);
    this.map.attributionControl.setPrefix('Leaflet 1.9.4');
    L.control.scale({ imperial: false }).addTo(this.map);
    this.routes = L.layerGroup().addTo(this.map);
    this.resize = new ResizeObserver(() => this.map.invalidateSize({ pan: false, debounceMoveend: true }));
    this.resize.observe(this.container);
  }
  setRoutes(tracks, labels) {
    this.routes.clearLayers();
    this.bounds = tracks.flatMap(trackBounds);
    for (const track of tracks) {
      for (const { points, name } of displayedPaths(track, labels)) {
        const layer = points.length > 1 ?
          this.library.polyline(points, { color: track.color, weight: 4, interactive: false }) :
          this.library.circleMarker(points[0], { color: track.color, radius: 4, fillOpacity: 0.85,
            interactive: false });
        if (hasEndpoints(track)) {
          const title = document.createElement('span');
          title.textContent = name;
          layer.bindTooltip(title, { permanent: true, direction: 'top' });
        }
        layer.addTo(this.routes);
      }
    }
    this.focus?.bringToFront();
  }
  followPoint(point, center) {
    const coords = location(point);
    if (center || !this.map.getBounds().contains(coords)) this.map.panTo(coords, { animate: false });
  }
  highlight(point, color = ROUTE_COLORS[0]) {
    if (!point) {
      this.focus?.remove();
      this.focus = null;
    } else if (this.focus) {
      this.focus.setLatLng(location(point));
      if (this.focus.options.fillColor !== color) this.focus.setStyle({ fillColor: color });
    } else {
      this.focus = this.library.circleMarker(location(point), { color: '#ffffff',
        fillColor: color, radius: 8, weight: 3, fillOpacity: 1, interactive: false }).addTo(this.map);
    }
  }
  fit(bottomInset) {
    if (this.bounds?.length) this.map.fitBounds(this.bounds, {
      paddingTopLeft: [22, 22], paddingBottomRight: [22, Math.max(22, bottomInset)],
      maxZoom: 16, animate: false,
    });
  }
  destroy() {
    this.resize.disconnect();
    this.highlight(null);
    this.routes.clearLayers();
    // Remove vectors before draining Canvas work owned by the renderer.
    this.renderer.remove();
    this.map.remove();
    this.container.remove();
  }
}

export class RouteMap {
  constructor(error = () => {}, playbackInset = () => 0) {
    this.playbackInset = playbackInset;
    this.error = error;
    this.element = document.createElement('div');
    this.root = this.element.attachShadow({ mode: 'open' });
    this.serial = 0;
  }
  async create(hass, serial) {
    let timer;
    try {
      const map = await Promise.race([loadNativeMap(hass),
        new Promise(resolve => { timer = setTimeout(() => resolve(null), 3500); })]);
      if (serial !== this.serial) return null;
      if (map) return new NativeRouteMap(map, this.root);
    } catch {
      if (serial !== this.serial) return null;
    } finally {
      clearTimeout(timer);
    }
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = new URL('./vendor/leaflet/leaflet.css', import.meta.url).href;
    const loaded = new Promise((resolve, reject) => {
      css.addEventListener('load', resolve, { once: true });
      css.addEventListener('error', reject, { once: true });
    });
    this.root.append(css);
    const [L] = await Promise.all([import('./vendor/leaflet/leaflet-src.esm.js'), loaded]);
    if (serial !== this.serial) {
      css.remove();
      return null;
    }
    return new OfflineRouteMap(L, this.root);
  }
  clear() {
    this.serial++;
    this.renderer?.destroy();
    this.renderer = null;
    this.pending = null;
    this.root.replaceChildren();
    this.tracks = null;
    this.slots = [];
    this.labels = null;
    this.point = null;
    this.fitting = false;
  }
  async update(tracks, hass, labels, slots = []) {
    if (this.labels === labels && this.tracks?.length === tracks.length &&
        this.slots?.length === slots.length && slots.every((slot, index) => this.slots[index] === slot) &&
        tracks.every((track, index) => this.tracks[index] === track)) return this.pending;
    this.tracks = tracks;
    this.slots = slots;
    this.labels = labels;
    if (this.renderer) this.draw();
    else if (tracks.length) {
      this.pending ??= this.initialize(hass, this.serial);
      return this.pending;
    }
  }
  async initialize(hass, serial) {
    try {
      const renderer = await this.create(hass, serial);
      if (serial !== this.serial || !renderer) return;
      this.renderer = renderer;
      this.pending = null;
      this.fitting = true;
      this.draw();
    } catch {
      if (serial === this.serial) {
        this.clear();
        this.error();
      }
    }
  }
  draw() {
    this.renderer.setRoutes(this.tracks.map((track, index) => ({
      ...track, color: ROUTE_COLORS[this.slots[index] ?? index],
    })), this.labels);
    this.renderer.highlight(this.point, this.pointColor);
    if (this.fitting) this.fit();
  }
  highlight(point, color, follow = false) {
    if (this.point === point && this.pointColor === color) return;
    this.point = point;
    this.pointColor = color;
    this.renderer?.highlight(point, color);
    if (follow) this.followPoint(false);
  }
  followPoint(center) {
    if (this.point) this.renderer?.followPoint(this.point, center);
  }
  fit() {
    this.fitting = !this.renderer;
    this.renderer?.fit(this.playbackInset());
  }
}
