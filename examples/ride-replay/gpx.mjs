const MAX_BYTES = 1024 * 1024;
const MAX_POINTS = 5000;
const NAMESPACES = new Set([null, 'http://www.topografix.com/GPX/1/0', 'http://www.topografix.com/GPX/1/1']);
const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;

function coordinate(node, name, limit) {
  const raw = node.getAttribute(name)?.trim();
  const value = raw && DECIMAL.test(raw) ? Number(raw) : NaN;
  if (!Number.isFinite(value) || Math.abs(value) > limit) throw new Error('invalid');
  return value;
}

export async function readGpx(file) {
  if (file.size > MAX_BYTES) throw new Error('limit');
  const text = await file.text();
  if (/<!DOCTYPE|<!ENTITY/i.test(text)) throw new Error('invalid');
  const root = new DOMParser().parseFromString(text, 'application/xml').documentElement;
  if (root.localName !== 'gpx' || !NAMESPACES.has(root.namespaceURI) || root.getElementsByTagName('parsererror').length) {
    throw new Error('invalid');
  }
  const children = (parent, name) => [...parent.children].filter(node => node.localName === name && node.namespaceURI === root.namespaceURI);
  const points = [];
  let segment = 0;
  for (const track of children(root, 'trk')) {
    for (const part of children(track, 'trkseg')) {
      for (const point of children(part, 'trkpt')) {
        if (points.length >= MAX_POINTS) throw new Error('limit');
        points.push({ sequence: points.length, segment,
          latitude: coordinate(point, 'lat', 90), longitude: coordinate(point, 'lon', 180) });
      }
      segment++;
    }
  }
  if (!points.length) throw new Error('invalid');
  return { key: 'standalone-gpx', kind: 'sampled_unverified', points, summary: {}, overlay: true };
}
