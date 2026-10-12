/** Pinned local Leaflet, optional tiles; raw coordinates are never converted. */
import { trackSegments } from './local-tracks.js';
export const ROUTE_COLORS = ['#03a9f4', '#ff9800', '#9c27b0', '#009688', '#ef5350'];

export function routePaths(track) {
  return trackSegments(track.points, track.kind).map(segment =>
    segment.map(point => [point.latitude, point.longitude]));
}

export class RouteMap {
  constructor(error = () => {}) {
    this.error = error; this.serial = 0;
    this.element = document.createElement('div');
    const root = this.element.attachShadow({ mode: 'open' });
    const css = document.createElement('link'); css.rel = 'stylesheet';
    css.href = new URL('./vendor/leaflet/leaflet.css', import.meta.url).href;
    this.cssReady = new Promise((resolve, reject) => {
      css.addEventListener('load', resolve, { once: true });
      css.addEventListener('error', reject, { once: true });
    });
    // The stylesheet is loaded only when an explicit saved route is displayed.
    this.css = css; this.root = root;
    this.container = document.createElement('div');
    this.container.style.cssText = 'height:340px;width:100%;background:#18232b;border-radius:8px';
    root.append(this.container);
  }
  clear() {
    this.serial++; this.map?.remove(); this.map = null; this.layer = null; this.tiles = null;
  }
  async update(tracks, tiles = false) {
    const serial = ++this.serial;
    if (!this.css.isConnected) this.root.prepend(this.css);
    try {
      const [L] = await Promise.all([import('./vendor/leaflet/leaflet-src.esm.js'), this.cssReady]);
      if (serial !== this.serial || !this.element.isConnected) return;
      if (!this.map) {
        this.map = L.map(this.container, { preferCanvas: true, minZoom: 1, maxZoom: 19,
          scrollWheelZoom: false, zoomAnimation: false }).setView([0, 0], 1);
        this.map.attributionControl.setPrefix('Leaflet 1.9.4');
        this.layer = L.layerGroup().addTo(this.map);
      }
      this.layer.clearLayers();
      if (tiles && !this.tiles) this.tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(this.map);
      if (!tiles && this.tiles) { this.tiles.remove(); this.tiles = null; }
      const bounds = [];
      tracks.forEach((track, index) => {
        const color = ROUTE_COLORS[index % ROUTE_COLORS.length];
        if (track.bounds) bounds.push([track.bounds[0], track.bounds[1]], [track.bounds[2], track.bounds[3]]);
        for (const path of routePaths(track)) {
          bounds.push(...path);
          if (path.length > 1) L.polyline(path, { color, weight: 4, opacity: 0.85 }).addTo(this.layer);
          else if (path.length) L.circleMarker(path[0], { color, radius: 5, fillOpacity: 0.8 }).addTo(this.layer);
        }
        if (track.kind === 'sampled_unverified' && track.points.length) {
          for (const [point, markerColor] of [[track.points[0], '#00cba9'], [track.points.at(-1), '#ff5252']]) {
            L.circleMarker([point.latitude, point.longitude], { color: markerColor,
              radius: 7, fillOpacity: 1 }).addTo(this.layer);
          }
        }
      });
      this.map.invalidateSize();
      if (bounds.length) this.map.fitBounds(bounds, { padding: [20, 20], maxZoom: 16, animate: false });
    } catch {
      if (serial === this.serial) { this.clear(); this.error(); }
    }
  }
}
