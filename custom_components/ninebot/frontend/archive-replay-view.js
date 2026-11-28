import { HistorySelection } from './history-selection.js';
import { LABELS } from './labels.js';
import { LocalTracks, hasEndpoints, trackKey } from './local-tracks.js';
import { ROUTE_COLORS, RouteMap, onRouteTap } from './route-map.js';
import { digits } from './trip-browser.js';
import { formatRideDuration, formatRideInterval } from './trip-format.js';
import { replayMarkup } from './archive-replay-template.js';
import { mountRideCollection } from './ride-collection.js';

export const REPLAY_COPY = {
  'zh-Hans': {
    mode: '本地档案 · 只读', intro: '查看 Home Assistant 已保存的行程和路线。',
    sampleMode: '合成行程 · 只读预览', sampleIntro: '查看已保存的采样点，对比不同月份的两趟行程。',
    rides: '行程', compareBoth: '对比两趟', compare: '对比另一趟', stopComparing: '保留当前行程',
    pickComparison: '选择要对比的行程', fit: '显示全部', timeline: '已保存采样点',
    totals: '整程统计', selected: '所选合计', current: '当前', included: '已选', menu: '打开侧栏',
    archiveId: '共同行程编号', empty: '暂无可用轨迹', noRides: '这段日期没有已保存行程。',
    loadFailed: '无法读取本地档案', mapFailed: '无法加载本地地图', coords: '坐标',
    previous: '上一页', next: '下一页', pages: '页码', morePoints: '读取更多采样点',
  },
  en: {
    mode: 'Local archive · Read-only', intro: 'Explore rides and routes saved by Home Assistant.',
    sampleMode: 'Synthetic archive · Read-only preview', sampleIntro: 'Explore saved points and compare two rides across months.',
    rides: 'Rides', compareBoth: 'Compare both', compare: 'Compare another ride', stopComparing: 'Keep current ride',
    pickComparison: 'Choose a ride to compare', fit: 'Fit routes', timeline: 'Saved points',
    totals: 'Whole-ride statistics', selected: 'Selected total', current: 'Current', included: 'Selected', menu: 'Open sidebar',
    archiveId: 'Shared archive ride ID', empty: 'No saved route to display', noRides: 'No saved rides in this date range.',
    loadFailed: 'Could not read the local archive', mapFailed: 'Could not load the local map', coords: 'Coordinates',
    previous: 'Previous', next: 'Next', pages: 'Pages', morePoints: 'Read more saved points',
  },
};

export function replayLocale(language) {
  return /^zh/i.test(language ?? '') ? 'zh-Hans' : 'en';
}

/** Shared full-page replay. Every private projection is owned by one mounted view. */
export function mountReplay(root, locale, { detail, synthetic = false, onTrackError = () => {},
  readCollectionRide, getMapHass = () => null }) {
  const copy = REPLAY_COPY[locale], labels = LABELS[locale];
  root.innerHTML = replayMarkup;
  const el = id => root.querySelector('#' + id);
  root.querySelector('main').lang = locale;
  for (const node of root.querySelectorAll('[data-copy]')) {
    const key = node.dataset.copy;
    node.textContent = copy[key] ?? labels[key];
  }
  if (synthetic) {
    root.querySelector('[data-copy="mode"]').textContent = copy.sampleMode;
    root.querySelector('[data-copy="intro"]').textContent = copy.sampleIntro;
  }
  el('menu').setAttribute('aria-label', copy.menu);
  el('language').hidden = !synthetic;
  el('pages').setAttribute('aria-label', copy.pages);
  for (const [id, key] of [['previous', 'previous'], ['next', 'next'], ['more-points', 'morePoints']]) {
    el(id).textContent = copy[key];
  }

  const selection = new HistorySelection();
  const archive = new LocalTracks(detail, () => render(false));
  const map = new RouteMap(() => showMap(false, copy.mapFailed), () => 0, chooseOnMap);
  const scrubbing = el('timeline'), picker = el('picker');
  const rideButtons = new Map(), rideChecks = new Map();
  const collection = mountRideCollection(el, locale, labels, { readRide: readCollectionRide, detail, changed: syncCollectionRows });
  const number = new Intl.NumberFormat(locale);
  const metricUnits = new Map([['distance', 'km'], ['duration', null], ['energy', 'Wh'],
    ['energy_intensity', 'Wh/km'], ['average_speed', 'km/h']]);
  const metricRows = new Map();
  let rides = [], timer = null, comparingNext = false, epoch = 0, resetting = false;
  let overlay = null, linked = false;

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

  function syncCollectionRows() {
    for (const { label, input, ride } of rideChecks.values()) {
      label.hidden = !collection.available() || !collection.selectable(ride);
      input.disabled = collection.working();
      input.checked = collection.selected(ride);
    }
  }

  function makeRideRow(ride, button) {
    const row = document.createElement('div');
    row.className = 'ride-row';
    const label = document.createElement('label');
    label.className = 'ride-check';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute('aria-label', collection.label(ride));
    input.addEventListener('change', () => collection.select(ride, input.checked));
    label.append(input);
    rideChecks.set(trackKey(ride.device_id, ride), { label, input, ride });
    row.append(label, button);
    return row;
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
    if (picker.open || archive.busy) return;
    const { routes } = selection.snapshot(archive.tracks);
    const matches = routes.filter(route => keys.includes(route.key));
    const activate = route => {
      picker.close();
      comparingNext = false;
      changeRide(route.ride);
      rideButtons.get(route.key)?.focus({ preventScroll: true });
    };
    if (matches.length === 1) activate(matches[0]);
    else if (matches.length > 1) {
      const choices = matches.map(route => {
        const button = makeRideButton(route.ride, () => activate(route));
        setRideState(button, route, route.key === selection.primary);
        return button;
      });
      el('choices').replaceChildren(...choices);
      picker.showModal();
    }
  }

  async function readTrack(ride, index = null) {
    const activeEpoch = epoch;
    await archive.read(ride.device_id, ride);
    if (activeEpoch !== epoch) return;
    if (archive.error) {
      const error = archive.error;
      if (!synthetic) clear();
      onTrackError(error);
      return;
    }
    const key = trackKey(ride.device_id, ride);
    const item = archive.tracks.get(key);
    // Linked statistics come from the same canonical archive row as the Assistant or receipt.
    if (item && !linked) selection.update(item.summary);
    if (index !== null && key === selection.primary) selection.seek(index, archive.tracks);
    render(true);
  }

  function changeRide(ride, index = null) {
    if (archive.busy) return;
    const key = trackKey(ride.device_id, ride);
    if (selection.primary === key) return;
    pause();
    const wasSelected = selection.selected.has(key);
    const saved = archive.tracks.get(key);
    if (wasSelected) selection.activate(key, archive.tracks);
    else if (!selection.select(saved?.summary ?? ride, comparingNext)) return;
    comparingNext = false;
    render(!wasSelected);
    if (!synthetic && ride.detail_saved === true && !saved) void readTrack(ride, index);
  }

  function toggleComparison() {
    if (archive.busy || !selection.primary) return;
    if (selection.selected.size > 1) {
      selection.retainPrimary();
    } else if (synthetic) {
      const current = selection.primary;
      const { index } = selection.position(archive.tracks);
      const other = rides.find(ride => trackKey(ride.device_id, ride) !== current);
      if (!other) return;
      const key = trackKey(other.device_id, other);
      selection.select(archive.tracks.get(key)?.summary ?? other, true);
      selection.activate(current);
      selection.seek(index, archive.tracks);
    } else {
      comparingNext = !comparingNext;
    }
    render(true);
  }

  function showMap(visible, message = '') {
    map.element.hidden = el('fit').disabled = !visible;
    el('map-message').hidden = visible;
    el('map-message').textContent = message;
  }

  function render(fit) {
    if (resetting) return;
    const { routes, primaryKey, replay, statistics } = selection.snapshot(archive.tracks);
    const active = routes.find(route => route.key === primaryKey);
    const { track } = active ?? {};
    const mapped = routes.filter(route => route.track?.points.length);
    const comparing = routes.length > 1;
    for (const [key, button] of rideButtons) {
      setRideState(button, routes.find(route => route.key === key), key === primaryKey);
      button.disabled = archive.busy;
    }
    el('compare').disabled = archive.busy || !active ||
      (synthetic && !comparing && !rides.some(ride => trackKey(ride.device_id, ride) !== primaryKey));
    el('compare').setAttribute('aria-pressed', String(comparing || comparingNext));
    el('compare').textContent = synthetic ? copy.compareBoth : comparing ? copy.stopComparing :
      comparingNext ? copy.pickComparison : copy.compare;
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
    el('more-points').hidden = synthetic || !track || track.complete;
    el('more-points').disabled = archive.busy;

    const message = archive.busy ? labels.loading : rides.length ? copy.empty : copy.noRides;
    showMap(mapped.length > 0 || overlay !== null, message);
    if (mapped.length || overlay) {
      const tracks = mapped.map(route => route.track);
      const slots = mapped.map(route => route.slot), titles = mapped.map(routeTitle);
      if (overlay) {
        tracks.push(overlay.track);
        slots.push(ROUTE_COLORS.length - 1);
        titles.push(overlay.title);
      }
      void map.update(tracks, getMapHass(), labels, slots, titles);
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
    const peers = selection.replayPeers?.(archive.tracks) ?? [];
    const peerLabels = peers.map(({ slot, point }) => ` · ${labels.active_route} ${slot + 1}, ${labels.point_sequence} ${point.sequence}`);
    const peerSamples = peers.map(({ slot, point }) => ` · ${'①②③④⑤'[slot]} #${point.sequence}`);
    scrubbing.setAttribute('aria-valuetext', point ?
      `${labels.sample_position.replace('{n}', index + 1).replace('{total}', count)} · ${sequence}${peerLabels.join('')}` : '');
    el('sample').textContent = point ?
      `${sequence} · ${copy.coords} ${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}${peerSamples.join('')}` : '';
    map.highlight(point, ROUTE_COLORS[slot], false, peers);
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

  function clear() {
    epoch++;
    linked = false;
    resetting = true;
    pause();
    picker.close();
    el('choices').replaceChildren();
    selection.clear();
    archive.clear();
    collection.clear();
    overlay = null;
    map.clear();
    rides = [];
    rideButtons.clear();
    rideChecks.clear();
    el('rides').replaceChildren();
    el('archive-id').replaceChildren();
    el('pages').hidden = true;
    el('page').replaceChildren();
    el('workspace').hidden = true;
    comparingNext = false;
    resetting = false;
    render(false);
  }

  function setPage(pageRides, { number: page = 1, previous = false, next = false, note = '', busy = false,
    restore = null, collectionScope = null } = {}) {
    if (rides !== pageRides) {
      rides = pageRides;
      rideButtons.clear();
      rideChecks.clear();
      const rows = [];
      for (const ride of rides) {
        const key = trackKey(ride.device_id, ride);
        if (!ride.ride_id || rideButtons.has(key)) continue;
        const button = makeRideButton(ride, () => {
          if (rides.includes(ride)) changeRide(ride);
        });
        rideButtons.set(key, button);
        rows.push(readCollectionRide && collectionScope ? makeRideRow(ride, button) : button);
      }
      el('rides').replaceChildren(...rows);
      syncCollectionRows();
    }
    collection.setScope(collectionScope);
    el('workspace').hidden = false;
    el('startup').hidden = true;
    el('archive-id').textContent = note;
    el('pages').hidden = !(previous || next || page > 1);
    el('previous').disabled = busy || !previous;
    el('next').disabled = busy || !next;
    el('page').textContent = number.format(page);
    const first = rides.find(ride => ride.ride_id);
    const restored = restore && rides.find(ride => trackKey(ride.device_id, ride) === restore.key);
    if (!selection.primary && first) changeRide(restored || first, restored ? restore.index : null);
    else render(false);
  }

  function setLinkedRides(ordered) {
    clear();
    linked = true;
    for (const ride of ordered) selection.select(ride, true);
    if (ordered.length) selection.activate(trackKey(ordered[0].device_id, ordered[0]));
    setPage(ordered);
  }

  function setOverlay(track, title) {
    if (!synthetic) return;
    overlay = track?.points.length ? { track, title } : null;
    render(true);
  }

  function bookmark() {
    return selection.primary && { key: selection.primary, index: selection.position(archive.tracks).index };
  }

  el('compare').addEventListener('click', toggleComparison);
  onRouteTap(el('compare'), toggleComparison);
  el('fit').addEventListener('click', () => map.fit());
  onRouteTap(el('fit'), () => map.fit());
  el('play').addEventListener('click', togglePlayback);
  onRouteTap(el('play'), togglePlayback);
  el('cancel').addEventListener('click', () => picker.close());
  onRouteTap(el('cancel'), () => picker.close());
  el('more-points').addEventListener('click', () => {
    if (!archive.busy && selection.primary) {
      pause();
      void readTrack(selection.selected.get(selection.primary));
    }
  });
  picker.addEventListener('close', () => { if (!picker.open) el('choices').replaceChildren(); });
  scrubbing.addEventListener('pointerdown', pause);
  scrubbing.addEventListener('input', () => {
    pause();
    seek(scrubbing.valueAsNumber);
  });
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
  map.element.className = 'route-map';
  el('viewport').append(map.element);
  const status = message => {
    el('startup').hidden = !message;
    el('startup').textContent = message;
  };
  return { el, clear, setPage, setLinkedRides, setOverlay, pause, readTrack, status, bookmark };
}
