import { mountReplay, replayLocale, REPLAY_COPY } from '/ninebot-static/archive-replay-view.js';
import { trackKey } from '/ninebot-static/local-tracks.js';
import { syntheticDetail, syntheticRide, syntheticVehicles } from './synthetic-archive.mjs';

const parameters = new URL(location.href).searchParams;
const locale = replayLocale(parameters.get('lang') ?? navigator.language);
const zh = locale === 'zh-Hans';
const copy = REPLAY_COPY[locale];
const rides = syntheticVehicles.map(({ device_id }, index) => syntheticRide(device_id, {
  august: index === 1, sameId: true, overlap: true,
  incomplete: parameters.get('scenario') === 'incomplete', zero: parameters.get('scenario') === 'zero',
}));
const source = new Map(rides.map(ride => [trackKey(ride.device_id, ride), ride]));
let failed = false;
const view = mountReplay(document.getElementById('replay'), locale, {
  synthetic: true,
  onTrackError: () => { failed = true; view.status(copy.loadFailed); },
  detail: async (service, data) => {
    const ride = source.get(trackKey(data.device_id, data));
    if (!ride || service !== 'get_trip_detail' || data.local_only !== true) throw new Error('Unknown local detail');
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

let epoch = 0;
async function initialize() {
  const active = ++epoch;
  failed = false;
  for (const ride of rides) {
    await view.readTrack(ride);
    if (failed || active !== epoch) return;
  }
  view.setPage(rides, { note: `${copy.archiveId} · ${rides[0].ride_id}` });
}
document.addEventListener('visibilitychange', () => { if (document.hidden) view.pause(); });
window.addEventListener('pagehide', () => { epoch++; view.clear(); });
window.addEventListener('pageshow', event => { if (event.persisted) void initialize(); });
await initialize();
