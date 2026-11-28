import { mountReplay, replayLocale, REPLAY_COPY } from '/ninebot-static/archive-replay-view.js';
import { trackKey } from '/ninebot-static/local-tracks.js';
import { readGpx } from './gpx.mjs';
import { syntheticDetail, syntheticRide, syntheticVehicles } from './synthetic-archive.mjs';

const parameters = new URL(location.href).searchParams;
const locale = replayLocale(parameters.get('lang') ?? navigator.language);
const zh = locale === 'zh-Hans';
const copy = REPLAY_COPY[locale];
const rides = syntheticVehicles.map(({ device_id }, index) => syntheticRide(device_id, {
  august: index === 1, sameId: true, overlap: true,
  incomplete: parameters.get('scenario') === 'incomplete', zero: parameters.get('scenario') === 'zero',
}));
const shorterRide = { ...rides[0], ride_id: 'synthetic-august-list', query_month: '202608',
  start_time: '2026-08-01T08:00:00Z', end_time: '2026-08-01T08:05:00Z',
  distance_m: 700, duration_s: 300, duration_h: 1 / 12, energy_wh: 38,
  source: 'trip_list', detail_id: null, detail_saved: false };
const accountRides = {
  a: [rides, [shorterRide]],
  b: [[{ ...rides[0], ride_id: 'synthetic-other-account', vehicle_label: 'Synthetic C', energy_wh: 0 }]],
};
let account = 'a', page = 0, failed = false, epoch = 0;
const getRide = data => accountRides[account].flat().find(ride =>
  trackKey(ride.device_id, ride) === trackKey(data.device_id, data));
const view = mountReplay(document.getElementById('replay'), locale, {
  synthetic: true,
  onTrackError: () => { failed = true; view.status(copy.loadFailed); },
  readCollectionRide: async ref => {
    const ride = getRide(ref);
    if (!ride || (ride === shorterRide && parameters.get('scenario') === 'unavailable')) return null;
    return { ...ride, detail_saved: ride.detail_saved !== false };
  },
  detail: async (service, data) => {
    const ride = getRide(data);
    if (!ride || service !== 'get_trip_detail' || data.local_only !== true || ride.detail_saved === false) {
      throw new Error('Unknown local detail');
    }
    return syntheticDetail(ride, data, {
      overlap: true, endpointsOnly: ride === rides[1] && parameters.get('scenario') === 'endpoints',
    });
  },
});

document.documentElement.lang = locale;
document.title = 'Ninebot · ' + view.el('map-heading').textContent;
const alternate = new URL(location.href);
alternate.searchParams.set('lang', zh ? 'en' : 'zh-Hans');
const language = view.el('language');
language.href = alternate.href;
language.lang = zh ? 'en' : 'zh-Hans';
language.textContent = zh ? 'English' : '简体中文';

view.el('archive-controls').innerHTML = `
  <form>
    <label><span id="preview-account-label"></span><select id="preview-account"></select></label>
    <label><span id="gpx-label"></span><input id="gpx-file" type="file" accept=".gpx,application/gpx+xml"></label>
    <button id="gpx-clear" type="button" disabled></button>
  </form>
  <output id="gpx-status" aria-live="polite"></output>`;
const accounts = view.el('preview-account'), gpx = view.el('gpx-file'), gpxClear = view.el('gpx-clear');
const imported = zh ? '导入的 GPX' : 'Imported GPX';
view.el('preview-account-label').textContent = zh ? '示例账号' : 'Demo account';
view.el('gpx-label').textContent = zh ? '叠加本机 GPX' : 'Overlay a local GPX file';
gpxClear.textContent = zh ? '移除 GPX' : 'Remove GPX';
for (const id of Object.keys(accountRides)) {
  const choice = document.createElement('option');
  choice.value = id;
  choice.textContent = zh ? `示例账号 ${id.toUpperCase()}` : `Demo account ${id.toUpperCase()}`;
  accounts.append(choice);
}

let gpxEpoch = 0;
function clearGpx() {
  gpxEpoch++;
  gpx.value = '';
  gpxClear.disabled = true;
  view.el('gpx-status').replaceChildren();
  view.setOverlay(null);
}

gpx.addEventListener('change', async () => {
  const [file] = gpx.files;
  if (!file) return;
  const active = ++gpxEpoch;
  gpx.value = '';
  try {
    const overlay = await readGpx(file);
    if (active !== gpxEpoch) return;
    view.setOverlay(overlay, imported);
    gpxClear.disabled = false;
    view.el('gpx-status').textContent = zh ? `已叠加 ${overlay.points.length} 个本机 GPX 点。` :
      `${overlay.points.length} local GPX points overlaid.`;
  } catch (error) {
    if (active !== gpxEpoch) return;
    view.el('gpx-status').textContent = error.message === 'limit' ?
      zh ? '请选择不超过 1 MiB 且最多含 5,000 个轨迹点的 GPX。' : 'Choose a GPX file up to 1 MiB and 5,000 track points.' :
      zh ? '无法读取 GPX 轨迹。' : 'Could not read a GPX track.';
  }
});
gpxClear.addEventListener('click', clearGpx);

async function openPage(number) {
  const active = ++epoch;
  const pages = accountRides[account];
  const rows = pages[number];
  if (!rows) return;
  page = number;
  failed = false;
  for (const ride of rows) {
    if (ride.detail_saved !== false) await view.readTrack(ride);
    if (failed || active !== epoch) return;
  }
  view.setPage(rows, { number: number + 1, previous: number > 0, next: number + 1 < pages.length,
    note: account === 'a' && !number ? `${copy.archiveId} · ${rides[0].ride_id}` : '',
    collectionScope: JSON.stringify(['synthetic-user', account]) });
}

accounts.addEventListener('change', () => {
  account = accounts.value;
  clearGpx();
  view.clear();
  void openPage(0);
});
view.el('previous').addEventListener('click', () => void openPage(page - 1));
view.el('next').addEventListener('click', () => void openPage(page + 1));
document.addEventListener('visibilitychange', () => { if (document.hidden) view.pause(); });
window.addEventListener('pagehide', () => { epoch++; clearGpx(); view.clear(); });
window.addEventListener('pageshow', event => { if (event.persisted) void openPage(page); });
await openPage(0);
