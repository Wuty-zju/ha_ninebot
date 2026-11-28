import { LocalTracks, trackKey } from './local-tracks.js';
import { onRouteTap } from './route-map.js';
import { formatRideInterval } from './trip-format.js';
import { makeRidePostcard, postcardMetrics, postcardMonth } from './ride-collection-postcard.js';

const MAX_RIDES = 100;
const MAP_LIMIT = 5;
const SHELVES = ['weekend', 'commute'];
const MONTH = /^20\d{2}(0[1-9]|1[0-2])$/;
const COPY = {
  'zh-Hans': {
    collection: '行程收藏', selected: '已选 {n} 趟', selectRide: '选择 {ride}', remove: '移除 {ride}',
    shelf: '收藏类别', weekend: '周末路线', commute: '通勤路线', open: '打开收藏', save: '保存所选到收藏',
    clear: '清空选择', month: '明信片月份', make: '生成明信片', title: '月度骑行明信片',
    map: '附上最多五趟的路线示意图', mapNote: '路线形状可能透露经过的地点。',
    download: '下载 PNG', close: '关闭', routeLabel: '已保存路线示意图',
    working: '正在检查所选行程…', unavailable: '部分收藏行程暂不可用。', none: '这个收藏里尚无行程。',
    saved: '已保存所选行程。', storage: '浏览器无法保留此次更改。', full: '最多可选择 100 趟行程。',
    noRoute: '本月所选行程没有可用的路线示意图。', failed: '无法生成明信片，请重试。',
  },
  en: {
    collection: 'Ride collections', selected: '{n} selected', selectRide: 'Select {ride}', remove: 'Remove {ride}',
    shelf: 'Collection', weekend: 'Weekend routes', commute: 'Commute routes', open: 'Open collection',
    save: 'Save selection to collection', clear: 'Clear selection', month: 'Postcard month',
    make: 'Create postcard', title: 'Monthly ride postcard', map: 'Include a sketch of up to five saved routes',
    mapNote: 'Route shapes can reveal places you rode through.',
    download: 'Download PNG', close: 'Close', routeLabel: 'Saved route sketches',
    working: 'Checking selected rides…', unavailable: 'Some saved rides are unavailable.',
    none: 'This collection has no rides yet.', saved: 'Selection saved.', storage: 'The browser could not keep this change.',
    full: 'Select up to 100 rides.', noRoute: 'No route sketches are available for the selected rides in this month.',
    failed: 'Could not create the postcard. Try again.',
  },
};

function reference({ device_id, ride_id, query_month } = {}) {
  const opaque = (value, max) => typeof value === 'string' && value.length > 0 && value.length <= max &&
    value.trim() === value && !/[\u0000-\u001f]/.test(value);
  return opaque(device_id, 128) && opaque(ride_id, 256) && MONTH.test(query_month) ?
    { device_id, ride_id, query_month } : null;
}

const keyOf = ride => trackKey(ride.device_id, ride);
const storageKey = (scope, shelf) => `ninebot.ride-collections.v1:${JSON.stringify([scope, shelf])}`;

function loadReferences(scope, shelf) {
  try {
    const raw = localStorage.getItem(storageKey(scope, shelf));
    const stored = raw && raw.length <= 100000 ? JSON.parse(raw) : null;
    if (!Array.isArray(stored)) return [];
    const refs = stored.slice(0, MAX_RIDES).flatMap(tuple => {
      const ref = Array.isArray(tuple) && tuple.length === 3 &&
        reference({ device_id: tuple[0], ride_id: tuple[1], query_month: tuple[2] });
      return ref ? [[keyOf(ref), ref]] : [];
    });
    return [...new Map(refs).values()];
  } catch {
    return [];
  }
}

function saveReferences(scope, shelf, refs) {
  try {
    const key = storageKey(scope, shelf);
    if (refs.length) localStorage.setItem(key, JSON.stringify(refs.map(ref => [ref.device_id, ref.ride_id, ref.query_month])));
    else localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

function option(value, title) {
  const item = document.createElement('option');
  item.value = value;
  item.textContent = title;
  return item;
}

export function mountRideCollection(el, locale, labels, { readRide, detail, changed }) {
  const copy = COPY[locale], count = new Intl.NumberFormat(locale);
  const monthFormat = new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'long', timeZone: 'UTC' });
  const labelMonth = value => monthFormat.format(new Date(`${value}-01T00:00:00Z`));
  const controls = el('collection-controls'), list = el('collection-list'), shelf = el('collection-shelf');
  const month = el('postcard-month'), mapChoice = el('postcard-map'), dialog = el('postcard-preview');
  const image = el('postcard-image'), download = el('postcard-download');
  let scope = null, refs = [], rides = [], working = false, epoch = 0, previewUrl = null;

  for (const node of [controls, dialog].flatMap(root => [...root.querySelectorAll('[data-collection-copy]')])) {
    node.textContent = copy[node.dataset.collectionCopy];
  }
  shelf.replaceChildren(...SHELVES.map(value => option(value, copy[value])));

  function labelRide(ride) {
    return `${ride.vehicle_label} · ${formatRideInterval(ride.start_time, ride.end_time, locale)}`;
  }

  function button(node, action) {
    const activate = () => { if (!node.disabled) action(); };
    node.addEventListener('click', activate);
    onRouteTap(node, activate);
  }

  function status(message = '') {
    el('collection-status').textContent = copy[message] ?? message;
  }

  function discardPreview() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    image.removeAttribute('src');
    image.alt = '';
    download.removeAttribute('href');
    download.removeAttribute('download');
  }

  function render() {
    controls.hidden = scope === null;
    el('collection-total').textContent = copy.selected.replace('{n}', count.format(rides.length));
    const oldMonth = month.value;
    const months = [...new Set(rides.map(postcardMonth))].sort().reverse();
    month.replaceChildren(...months.map(value => option(value, labelMonth(value))));
    if (months.includes(oldMonth)) month.value = oldMonth;
    list.replaceChildren(...rides.map(ride => {
      const item = document.createElement('li');
      const name = labelRide(ride);
      const text = document.createElement('span');
      text.textContent = name;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.setAttribute('aria-label', copy.remove.replace('{ride}', () => name));
      remove.disabled = working;
      button(remove, () => {
        select(ride, false);
        (list.querySelector('button') ?? el('collection-summary')).focus();
      });
      item.append(text, remove);
      return item;
    }));
    list.hidden = !rides.length;
    const hasRides = rides.length > 0;
    for (const node of [el('collection-save'), el('postcard-make'), month, mapChoice]) {
      node.disabled = working || !hasRides;
    }
    el('collection-clear').disabled = working || !refs.length;
    el('collection-open').disabled = shelf.disabled = working;
    controls.setAttribute('aria-busy', String(working));
    changed();
  }

  function clear() {
    epoch++;
    scope = null;
    refs = [];
    rides = [];
    working = false;
    mapChoice.checked = false;
    controls.open = false;
    shelf.selectedIndex = 0;
    dialog.close();
    discardPreview();
    status();
    render();
  }

  async function refresh(next, token) {
    const results = [];
    for (let index = 0; index < next.length && token === epoch; index += 4) {
      const batch = await Promise.all(next.slice(index, index + 4).map(async ref => {
        try {
          const ride = await readRide(ref);
          return ride && reference(ride) && keyOf(ride) === keyOf(ref) ? ride : null;
        } catch {
          return null;
        }
      }));
      results.push(...batch);
    }
    return results.filter(Boolean);
  }

  function start() {
    working = true;
    status('working');
    render();
    return ++epoch;
  }

  function finish(message = '') {
    working = false;
    status(message);
    render();
  }

  async function restore(next, fromShelf = false) {
    rides = [];
    const token = start();
    const allowed = await refresh(next, token);
    if (token !== epoch) return;
    refs = next;
    rides = allowed;
    const persisted = !fromShelf || saveReferences(scope, 'selection', refs);
    finish(!persisted ? 'storage' : allowed.length < next.length ? 'unavailable' : fromShelf && !next.length ? 'none' : '');
  }

  function setScope(next) {
    if (scope === next) return;
    clear();
    if (!next || !readRide) return;
    scope = next;
    refs = loadReferences(scope, 'selection');
    if (refs.length) void restore(refs);
    else render();
  }

  function select(ride, enabled) {
    const ref = reference(ride);
    if (!scope || working || !ref) return;
    const key = keyOf(ref);
    if (enabled && !refs.some(item => keyOf(item) === key) && refs.length === MAX_RIDES) {
      status('full');
      render();
      return;
    }
    refs = refs.filter(item => keyOf(item) !== key);
    rides = rides.filter(item => keyOf(item) !== key);
    if (enabled) {
      refs.push(ref);
      rides.push(ride);
    }
    status(saveReferences(scope, 'selection', refs) ? '' : 'storage');
    render();
  }

  async function create() {
    const requestedMonth = month.value, includeMap = mapChoice.checked;
    const token = start();
    try {
      const allowed = await refresh(refs, token);
      if (token !== epoch) return;
      rides = allowed;
      const chosen = allowed.filter(ride => postcardMonth(ride) === requestedMonth);
      if (!chosen.length) {
        finish('unavailable');
        return;
      }
      const tracks = [];
      if (includeMap) {
        const store = new LocalTracks(detail);
        for (const ride of chosen.filter(row => row.detail_saved === true).slice(0, MAP_LIMIT)) {
          await store.read(ride.device_id, ride);
          if (token !== epoch) return;
          if (store.error && store.error !== 'query_detail_not_recorded') throw new Error(store.error);
          const track = store.tracks.get(keyOf(ride));
          if (track?.points.length) tracks.push(track);
        }
      }
      const metrics = postcardMetrics(chosen, locale, labels);
      const displayMonth = labelMonth(requestedMonth);
      const blob = await makeRidePostcard({ title: copy.title, month: displayMonth, metrics, tracks,
        routeLabel: copy.routeLabel });
      if (token !== epoch) return;
      if (!blob) throw new Error('postcard_image');
      discardPreview();
      previewUrl = URL.createObjectURL(blob);
      image.src = download.href = previewUrl;
      image.alt = [copy.title, displayMonth, ...metrics.map(([label, value]) => `${label} ${value}`),
        ...(tracks.length ? [copy.routeLabel] : [])].join(' · ');
      download.download = `ninebot-postcard-${requestedMonth}.png`;
      el('postcard-note').textContent = includeMap && !tracks.length ? copy.noRoute :
        allowed.length < refs.length ? copy.unavailable : '';
      finish(allowed.length < refs.length ? 'unavailable' : '');
      dialog.showModal();
    } catch {
      if (token === epoch) finish('failed');
    }
  }

  button(el('collection-open'), () => void restore(loadReferences(scope, shelf.value), true));
  button(el('collection-save'), () => status(saveReferences(scope, shelf.value, refs.filter(ref =>
    rides.some(ride => keyOf(ride) === keyOf(ref)))) ? 'saved' : 'storage'));
  button(el('collection-clear'), () => {
    refs = [];
    rides = [];
    status(saveReferences(scope, 'selection', refs) ? '' : 'storage');
    render();
  });
  button(el('postcard-make'), () => void create());
  button(el('postcard-close'), () => dialog.close());
  dialog.addEventListener('close', () => { if (!dialog.open) discardPreview(); });

  return { clear, setScope, select, available: () => scope !== null, working: () => working,
    selected: ride => rides.some(item => keyOf(item) === keyOf(ride)),
    selectable: ride => Boolean(reference(ride)),
    label: ride => copy.selectRide.replace('{ride}', () => labelRide(ride)) };
}
