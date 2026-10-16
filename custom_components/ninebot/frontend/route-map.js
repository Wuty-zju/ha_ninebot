/** Pinned local Leaflet with keyed layers; no CRS conversion or road matching. */
import { trackSegments } from './local-tracks.js';
import { createRouteRenderer } from './route-renderer.js';
export const ROUTE_COLORS = ['#03a9f4', '#ff9800', '#9c27b0', '#009688', '#ef5350'];

export function routePaths(track) {
  return trackSegments(track.points, track.kind).map(segment =>
    segment.map(point => [point.latitude, point.longitude]));
}

export class RouteMap {
  constructor(error = () => {}, select = () => {}, focus = () => {}) {
    this.error = error; this.select = select; this.onFocus = focus;
    this.serial = 0; this.groups = new Map(); this.bounds = [];
    this.element = document.createElement('div');
    const root = this.element.attachShadow({ mode: 'open' });
    const css = document.createElement('link'); css.rel = 'stylesheet';
    css.href = new URL('./vendor/leaflet/leaflet.css', import.meta.url).href;
    this.cssReady = new Promise((resolve, reject) => {
      css.addEventListener('load', resolve, { once: true });
      css.addEventListener('error', reject, { once: true });
    });
    this.css = css; this.root = root;
    this.container = document.createElement('div');
    this.container.style.cssText = 'height:clamp(280px,48vh,480px);width:100%;background:var(--secondary-background-color,#18232b);border-radius:12px';
    root.append(this.container);
  }
  clear() {
    this.serial++; cancelAnimationFrame(this.frame); this.frame = null;
    // Drain every vector owned by this map before Leaflet removes renderers.
    // A focus/brush path can outlive its logical group after selection changes.
    if (this.map && this.library) this.map.eachLayer(layer => {
      if (layer instanceof this.library.Path) this.map.removeLayer(layer);
    });
    this.layer?.clearLayers();
    // Own the renderer explicitly so it is removed after vectors, before Map's
    // generic layer teardown. Do not leave cancellation to layer insertion order.
    this.renderer?.remove(); this.map?.remove();
    this.map = null; this.renderer = null; this.layer = null; this.tiles = null;
    this.focus = null; this.selectedPoint = null; this.brushLayer = null;
    this.groups.clear(); this.bounds = []; this.primary = null; this.brushSignature = null; this.brushTrack = null;
  }
  highlight(point = null) {
    this.selectedPoint = point;
    if (!point) { this.focus?.remove(); this.focus = null; return; }
    if (!this.map) return;
    const location = [point.latitude, point.longitude];
    if (this.focus) this.focus.setLatLng(location);
    else this.focus = this.library.circleMarker(location,
      { color: '#ffffff', fillColor: '#2196f3', radius: 9, weight: 3, fillOpacity: 1,
        interactive: false }).addTo(this.layer);
    this.focus.bringToFront();
  }
  fit() {
    if (this.map && this.bounds.length) this.map.fitBounds(this.bounds,
      { padding: [20, 20], maxZoom: 16, animate: false });
  }
  focusNear(key, location) {
    cancelAnimationFrame(this.frame);
    this.frame = requestAnimationFrame(() => {
      const item = this.groups.get(key);
      if (!this.map || !item || this.primary !== key) return;
      item.pixels ??= item.track.points.map(point => this.map.latLngToLayerPoint([point.latitude, point.longitude]));
      const target = this.map.latLngToLayerPoint(location);
      let best = -1, distance = Infinity;
      item.pixels.forEach((point, index) => {
        const squared = (point.x - target.x) ** 2 + (point.y - target.y) ** 2;
        if (squared < distance) { best = index; distance = squared; }
      });
      if (best >= 0) this.onFocus(key, item.track.points[best].sequence);
    });
  }
  brush(value) {
    const item = value && this.groups.get(value.key);
    const signature = JSON.stringify(value);
    if (signature === this.brushSignature && item?.track === this.brushTrack) return;
    this.brushSignature = signature; this.brushTrack = item?.track;
    this.brushLayer?.remove(); this.brushLayer = null;
    if (!this.map || !item) return;
    this.brushLayer = this.library.layerGroup().addTo(this.layer);
    const points = item.track.points.filter(point => point.sequence >= value.start && point.sequence <= value.end);
    for (const path of routePaths({ ...item.track, points })) {
      const shape = path.length > 1 ? this.library.polyline(path,
        { color: '#ffffff', weight: 8, opacity: .7, interactive: false }) :
        this.library.circleMarker(path[0], { color: '#ffffff', radius: 7, interactive: false });
      shape.addTo(this.brushLayer);
    }
    this.highlight(this.selectedPoint);
  }
  async update(tracks, tiles = false, primary = null, brush = null) {
    const serial = ++this.serial;
    if (!this.css.isConnected) this.root.prepend(this.css);
    try {
      const [L] = await Promise.all([import('./vendor/leaflet/leaflet-src.esm.js'), this.cssReady]);
      if (serial !== this.serial || !this.element.isConnected) return;
      const initial = !this.map;
      if (initial) {
        this.renderer = createRouteRenderer(L);
        this.map = L.map(this.container, { renderer: this.renderer, preferCanvas: true, minZoom: 1, maxZoom: 19,
          scrollWheelZoom: false, zoomAnimation: false }).setView([0, 0], 1);
        this.map.attributionControl.setPrefix('Leaflet 1.9.4');
        this.layer = L.layerGroup().addTo(this.map);
        this.map.on('zoomend moveend', () => {
          for (const item of this.groups.values()) item.pixels = null;
        });
      }
      this.library = L; this.primary = primary;
      if (tiles && !this.tiles) this.tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(this.map);
      if (!tiles && this.tiles) { this.tiles.remove(); this.tiles = null; }
      const keys = new Set(tracks.map(track => track.key));
      for (const [key, item] of this.groups) if (!keys.has(key)) { item.layer.remove(); this.groups.delete(key); }
      const bounds = [];
      for (const track of tracks) {
        const key = track.key;
        if (track.bounds) bounds.push([track.bounds[0], track.bounds[1]], [track.bounds[2], track.bounds[3]]);
        let item = this.groups.get(key);
        if (item?.track !== track) {
          const color = item?.color ?? ROUTE_COLORS.find(color =>
            ![...this.groups.values()].some(group => group.color === color)) ?? ROUTE_COLORS[0];
          item?.layer.remove();
          item = { track, color, layer: L.layerGroup().addTo(this.layer), lines: [], pixels: null };
          this.groups.set(key, item);
          const bind = shape => {
            shape.on('click', event => { this.select(key); this.primary = key; this.focusNear(key, event.latlng); });
            shape.on('mousemove', event => this.focusNear(key, event.latlng));
            shape.addTo(item.layer);
          };
          for (const path of routePaths(track)) {
            bounds.push(...path);
            const shape = path.length > 1 ? L.polyline(path, { color, weight: 4, opacity: .85 }) :
              L.circleMarker(path[0], { color, radius: 5, fillOpacity: .8 });
            item.lines.push(shape); bind(shape);
          }
          if (track.kind === 'sampled_unverified' && track.points.length) {
            for (const [point, markerColor] of [[track.points[0], '#00cba9'], [track.points.at(-1), '#ff5252']]) {
              bind(L.circleMarker([point.latitude, point.longitude], { color: markerColor, radius: 7, fillOpacity: 1 }));
            }
          }
        }
        for (const line of item.lines) line.setStyle({ weight: primary === key ? 6 : 4,
          opacity: !primary || primary === key ? 1 : .55 });
      }
      this.bounds = bounds; this.brush(brush); this.highlight(this.selectedPoint);
      this.map.invalidateSize({ pan: false });
      // User panning survives selection, telemetry, tile changes and continuation.
      if (initial) this.fit();
    } catch {
      if (serial === this.serial) { this.clear(); this.error(); }
    }
  }
}
