import { HistorySelection } from '/ninebot-static/history-selection.js';
import { LABELS } from '/ninebot-static/labels.js';
import { LocalTracks, hasEndpoints, trackKey } from '/ninebot-static/local-tracks.js';
import { ROUTE_COLORS, RouteMap, onRouteTap } from '/ninebot-static/route-map.js';
import { digits } from '/ninebot-static/trip-browser.js';
import { formatRideDuration, formatRideInterval } from '/ninebot-static/trip-format.js';
import { syntheticDetail, syntheticRide, syntheticVehicles } from './synthetic-archive.mjs';

const parameters = new URL(location.href).searchParams;
const locale = /^zh/i.test(parameters.get('lang') ?? navigator.language) ? 'zh-Hans' : 'en';
const zh = locale === 'zh-Hans';
const labels = LABELS[locale];
const copy = zh ? {
  mode: '合成行程 · 只读预览', intro: '查看已保存的采样点，对比不同月份的两趟行程。',
  rides: '行程', compareBoth: '对比两趟', fit: '显示全部', timeline: '已保存采样点',
  totals: '整程统计', selected: '所选合计', current: '当前', included: '已选',
  archiveId: '共同行程编号', empty: '暂无可用轨迹', loadFailed: '无法读取合成行程',
  mapFailed: '无法加载本地地图', coords: '坐标',
} : {
  mode: 'Synthetic archive · Read-only preview', intro: 'Explore saved points and compare two rides across months.',
  rides: 'Rides', compareBoth: 'Compare both', fit: 'Fit routes', timeline: 'Saved points',
  totals: 'Whole-ride statistics', selected: 'Selected total', current: 'Current', included: 'Selected',
  archiveId: 'Shared archive ride ID', empty: 'No saved route to display', loadFailed: 'Could not read the synthetic archive',
  mapFailed: 'Could not load the local map', coords: 'Coordinates',
};

const el = id => document.getElementById(id);
const selection = new HistorySelection();
const rides = syntheticVehicles.map(({ device_id }, index) => syntheticRide(device_id, {
  august: index === 1, sameId: true, overlap: true,
  incomplete: parameters.get('scenario') === 'incomplete', zero: parameters.get('scenario') === 'zero',
}));
const source = new Map(rides.map(ride => [trackKey(ride.device_id, ride), ride]));
const archive = new LocalTracks(async (service, data) => {
  const ride = source.get(trackKey(data.device_id, data));
  if (!ride || service !== 'get_trip_detail' || data.local_only !== true) throw new Error('Unknown local detail');
  return syntheticDetail(ride, data, {
    overlap: true, endpointsOnly: ride === rides[1] && parameters.get('scenario') === 'endpoints',
  });
});
const map = new RouteMap(() => showMap(false, copy.mapFailed), () => 0, chooseOnMap);
const scrubbing = el('timeline');
const picker = el('picker');
const rideButtons = new Map();
const number = new Intl.NumberFormat(locale);
const metricUnits = new Map([['distance', 'km'], ['duration', null], ['energy', 'Wh'],
  ['energy_intensity', 'Wh/km'], ['average_speed', 'km/h']]);
const metricRows = new Map();
let timer = null;

document.documentElement.lang = locale;
document.title = `Ninebot · ${labels.map_title}`;
for (const node of document.querySelectorAll('[data-copy]')) {
  const key = node.dataset.copy;
  node.textContent = copy[key] ?? labels[key];
}
const language = el('language');
const alternate = new URL(location.href);
alternate.searchParams.set('lang', zh ? 'en' : 'zh-Hans');
language.href = alternate.href;
language.lang = zh ? 'en' : 'zh-Hans';
language.textContent = zh ? 'English' : '简体中文';

function valueText(key, { value, precision }) {
  if (value === null) return '—';
  if (key === 'duration') return formatRideDuration(value, null, locale);
  const places = value === 0 ? 0 : digits(precision);
  const formatter = new Intl.NumberFormat(locale, { minimumFractionDigits: places, maximumFractionDigits: places });
  return `${formatter.format(key === 'distance' ? value / 1000 : value)} ${metricUnits.get(key)}`;
}

function makeRideButton(ride, activate) {
  const button = el('ride-template').content.firstElementChild.cloneNode(true);
  button.querySelector('strong').textContent = ride.vehicle_label;
  button.querySelector('.ride-time').textContent = formatRideInterval(ride.start_time, ride.end_time, locale);
  button.addEventListener('click', activate);
  onRouteTap(button, activate);
  return button;
}

function setRideState(button, route, current) {
  const marker = button.querySelector('.marker');
  const status = button.querySelector('.ride-status');
  marker.textContent = route ? String(route.slot + 1) : '';
  marker.style.setProperty('--route-color', route ? ROUTE_COLORS[route.slot] : '');
  status.textContent = current ? copy.current : route ? copy.included : '';
  button.setAttribute('aria-pressed', String(current));
}

function routeTitle(route) {
  return `${route.slot + 1} · ${route.ride.vehicle_label} · ${formatRideInterval(
    route.ride.start_time, route.ride.end_time, locale)}`;
}

function chooseOnMap(keys) {
  if (picker.open) return;
  const { routes } = selection.snapshot(archive.tracks);
  const matches = routes.filter(route => keys.includes(route.key));
  if (matches.length === 1) {
    changeRide(matches[0].ride);
    rideButtons.get(matches[0].key).focus({ preventScroll: true });
  } else if (matches.length > 1) {
    const choices = matches.map(route => {
      const button = makeRideButton(route.ride, () => {
        picker.close();
        changeRide(route.ride);
        rideButtons.get(route.key).focus({ preventScroll: true });
      });
      setRideState(button, route, route.key === selection.primary);
      return button;
    });
    el('choices').replaceChildren(...choices);
    picker.showModal();
  }
}

function changeRide(ride) {
  const key = trackKey(ride.device_id, ride);
  if (selection.primary === key) return;
  pause();
  const comparing = selection.selected.has(key);
  if (comparing) selection.activate(key);
  else selection.select(archive.tracks.get(key)?.summary ?? ride);
  render(!comparing);
}

function toggleComparison() {
  if (selection.selected.size > 1) selection.retainPrimary();
  else {
    const current = selection.primary;
    const { index } = selection.position(archive.tracks);
    const other = rides.find(ride => trackKey(ride.device_id, ride) !== current);
    const key = trackKey(other.device_id, other);
    selection.select(archive.tracks.get(key)?.summary ?? other, true);
    selection.activate(current);
    selection.seek(index, archive.tracks);
  }
  render(true);
}

function showMap(visible, message = '') {
  map.element.hidden = el('fit').disabled = !visible;
  el('map-message').hidden = visible;
  el('map-message').textContent = message;
}

function render(fit) {
  const { routes, primaryKey, replay, statistics } = selection.snapshot(archive.tracks);
  const active = routes.find(route => route.key === primaryKey);
  const { track } = active ?? {};
  const mapped = routes.filter(route => route.track?.points.length);
  const comparing = routes.length > 1;
  for (const [key, button] of rideButtons) {
    setRideState(button, routes.find(route => route.key === key), key === primaryKey);
  }
  el('compare').setAttribute('aria-pressed', String(comparing));
  const activeText = active ? `${labels.active_route} · ${active.slot + 1} · ${active.ride.vehicle_label}` : '';
  if (el('active').textContent !== activeText) el('active').textContent = activeText;
  el('selected-heading').hidden = !comparing;
  for (const [key, [current, selected]] of metricRows) {
    current.textContent = valueText(key, statistics.current[key]);
    selected.hidden = !comparing;
    if (!comparing) {
      selected.replaceChildren();
      continue;
    }
    const metric = statistics.selected[key];
    selected.textContent = valueText(key, metric);
    if (metric.value === null) {
      const coverage = document.createElement('small');
      coverage.textContent = labels.metric_coverage.replace('{known}', number.format(metric.count))
        .replace('{total}', number.format(routes.length));
      selected.append(coverage);
    }
  }

  const notes = [];
  if (hasEndpoints(track)) notes.push(labels.endpoints_only);
  else if (track?.kind === 'server_simplified' || track?.kind === 'single_point') notes.push(labels.simplified);
  else if (track && !track.points.length) notes.push(labels.saved_without_points);
  if (replay.count) notes.push(labels.playback_note);
  if (mapped.length) notes.push(labels.coordinate_note);
  el('quality').textContent = notes.join(' · ');

  showMap(mapped.length > 0, copy.empty);
  if (mapped.length) {
    void map.update(mapped.map(route => route.track), null, labels,
      mapped.map(route => route.slot), mapped.map(routeTitle));
    if (fit) map.fit();
  } else {
    map.clear();
  }
  renderPlayback(replay, active?.slot);
}

function renderPlayback({ index, count, point }, slot = selection.slots.get(selection.primary)) {
  el('transport').hidden = el('sample').hidden = !point;
  el('play').textContent = timer !== null ? labels.pause_points : labels.play_points;
  scrubbing.max = String(Math.max(0, count - 1));
  scrubbing.value = String(index);
  el('position').textContent = point ? `${index + 1} / ${count}` : '';
  const sequence = point ? `${labels.point_sequence} ${point.sequence}` : '';
  scrubbing.setAttribute('aria-valuetext', point ?
    `${labels.sample_position.replace('{n}', index + 1).replace('{total}', count)} · ${sequence}` : '');
  el('sample').textContent = point ?
    `${sequence} · ${copy.coords} ${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}` : '';
  map.highlight(point, ROUTE_COLORS[slot]);
}

function pause() {
  if (timer === null) return;
  clearInterval(timer);
  timer = null;
  el('play').textContent = labels.play_points;
}

function seek(index) {
  renderPlayback(selection.seek(index, archive.tracks));
}

function togglePlayback() {
  if (timer !== null) {
    pause();
    return;
  }
  const { index, count } = selection.position(archive.tracks);
  if (!count) return;
  timer = setInterval(() => {
    const current = selection.position(archive.tracks);
    if (current.index >= current.count - 2) pause();
    seek(current.index + 1);
  }, 450);
  if (index === count - 1) seek(0);
  else el('play').textContent = labels.pause_points;
}

el('compare').addEventListener('click', toggleComparison);
onRouteTap(el('compare'), toggleComparison);
el('fit').addEventListener('click', () => map.fit());
onRouteTap(el('fit'), () => map.fit());
el('play').addEventListener('click', togglePlayback);
onRouteTap(el('play'), togglePlayback);
el('cancel').addEventListener('click', () => picker.close());
onRouteTap(el('cancel'), () => picker.close());
picker.addEventListener('close', () => el('choices').replaceChildren());
scrubbing.addEventListener('pointerdown', pause);
scrubbing.addEventListener('input', () => {
  pause();
  seek(scrubbing.valueAsNumber);
});
document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
window.addEventListener('pagehide', event => {
  pause();
  if (event.persisted) return;
  picker.close();
  selection.clear();
  archive.clear();
  map.clear();
});

async function initialize() {
  // The preview has exactly two bundled entries. All interactions use this bounded cache.
  for (const ride of rides) {
    await archive.read(ride.device_id, ride);
    if (archive.error) {
      el('startup').textContent = copy.loadFailed;
      return;
    }
    const key = trackKey(ride.device_id, ride);
    const button = makeRideButton(ride, () => changeRide(ride));
    rideButtons.set(key, button);
    el('rides').append(button);
  }
  for (const key of metricUnits.keys()) {
    const row = el('metrics').insertRow();
    const label = document.createElement('th');
    label.scope = 'row';
    label.textContent = labels[key];
    row.append(label);
    const current = row.insertCell();
    current.className = 'current';
    const selected = row.insertCell();
    selected.className = 'selected';
    metricRows.set(key, [current, selected]);
  }
  el('archive-id').textContent = `${copy.archiveId} · ${rides[0].ride_id}`;
  map.element.className = 'route-map';
  el('viewport').append(map.element);
  el('workspace').hidden = false;
  el('startup').hidden = true;
  changeRide(rides[0]);
}

await initialize();
