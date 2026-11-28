import { rideInsights } from './history-selection.js';
import { trackSegments } from './local-tracks.js';
import { ROUTE_COLORS } from './route-map.js';
import { businessDates, digits } from './trip-browser.js';
import { formatRideDuration } from './trip-format.js';

export function postcardMonth(ride) {
  if (typeof ride.start_time === 'string' && Number.isFinite(Date.parse(ride.start_time))) {
    return businessDates(new Date(ride.start_time))[0].slice(0, 7);
  }
  return ride.query_month.replace(/^(\d{4})(\d{2})$/, '$1-$2');
}

export function postcardMetrics(rides, locale, labels) {
  const stats = rideInsights(rides);
  function measurement(key, unit, divisor = 1) {
    const { value, precision } = stats[key];
    if (value === null) return '—';
    const format = new Intl.NumberFormat(locale, { maximumFractionDigits: digits(precision) });
    return `${format.format(value / divisor)} ${unit}`;
  }
  return [[labels.distance, measurement('distance', 'km', 1000)],
    [labels.duration, formatRideDuration(stats.duration.value, null, locale)],
    [labels.energy, measurement('energy', 'Wh')]];
}

function drawRoutes(context, tracks, label) {
  const segments = tracks.flatMap((track, index) => trackSegments(track.points, track.kind)
    .map(points => ({ color: ROUTE_COLORS[index], points })));
  if (!segments.length) return;
  // Fit one local diagram without a basemap, coordinate text or location metadata.
  let south = 90, west = 180, north = -90, east = -180;
  for (const { points } of segments) {
    for (const { latitude, longitude } of points) {
      south = Math.min(south, latitude);
      north = Math.max(north, latitude);
      west = Math.min(west, longitude);
      east = Math.max(east, longitude);
    }
  }
  const aspect = Math.max(0.1, Math.cos((south + north) / 2 * Math.PI / 180));
  const spanX = (east - west) * aspect, spanY = north - south;
  const scale = (spanX || spanY) ? Math.min(760 / spanX, 238 / spanY) : 1;
  const project = ({ latitude, longitude }) => [480 + ((longitude - west) * aspect - spanX / 2) * scale,
    785 + (spanY / 2 - (latitude - south)) * scale];
  context.fillStyle = '#e9f1ed';
  context.fillRect(48, 598, 864, 338);
  context.lineWidth = 6;
  context.lineCap = context.lineJoin = 'round';
  for (const { color, points } of segments) {
    context.beginPath();
    const first = project(points[0]);
    if (points.length === 1) {
      context.arc(...first, 6, 0, Math.PI * 2);
      context.fillStyle = color;
      context.fill();
    } else {
      context.moveTo(...first);
      for (const point of points.slice(1)) context.lineTo(...project(point));
      context.strokeStyle = color;
      context.stroke();
    }
  }
  context.fillStyle = '#355451';
  context.font = '500 24px system-ui, sans-serif';
  context.fillText(label, 72, 638, 816);
}

async function postcardPng(canvas) {
  const encoded = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!encoded) return null;
  const bytes = await encoded.arrayBuffer();
  const view = new DataView(bytes);
  const chunks = [bytes.slice(0, 8)];
  for (let offset = 8; offset + 12 <= bytes.byteLength; ) {
    const end = offset + view.getUint32(offset) + 12;
    if (end > bytes.byteLength) return null;
    // Preserve the raster and essential chunk CRCs. Some browsers add EXIF or text.
    const type = String.fromCharCode(...new Uint8Array(bytes, offset + 4, 4));
    if (['IHDR', 'PLTE', 'IDAT', 'IEND'].includes(type)) chunks.push(bytes.slice(offset, end));
    if (type === 'IEND') return new Blob(chunks, { type: 'image/png' });
    offset = end;
  }
  return null;
}

export function makeRidePostcard({ title, month, metrics, tracks, routeLabel }) {
  const canvas = document.createElement('canvas');
  canvas.width = 960;
  canvas.height = tracks.length ? 984 : 610;
  const context = canvas.getContext('2d');
  if (!context) return Promise.resolve(null);
  context.fillStyle = '#f6f5ee';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#173f3a';
  context.fillRect(0, 0, canvas.width, 202);
  context.fillStyle = '#c6e0d0';
  context.font = '600 29px system-ui, sans-serif';
  context.fillText(month, 64, 72, 830);
  context.fillStyle = '#fff';
  context.font = '700 62px system-ui, sans-serif';
  context.fillText(title, 60, 153, 840);
  metrics.forEach(([label, value], index) => {
    const y = 270 + index * 105;
    context.fillStyle = '#385650';
    context.font = '600 32px system-ui, sans-serif';
    context.fillText(label, 64, y + 25, 260);
    context.textAlign = 'right';
    context.fillStyle = '#173f3a';
    context.font = '650 55px system-ui, sans-serif';
    context.fillText(value, 896, y + 29, 535);
    context.textAlign = 'left';
    context.fillStyle = '#cbd9d2';
    context.fillRect(64, y + 59, 832, 2);
  });
  if (tracks.length) drawRoutes(context, tracks, routeLabel);
  return postcardPng(canvas);
}
