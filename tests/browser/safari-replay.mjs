/** Run on Apple Safari using safaridriver; all ride data comes from the local fixture. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { serveReplay } from './replay-server.mjs';

const platform = process.argv[2] || 'macOS';
assert.ok(['macOS', 'iOS'].includes(platform));
const capabilities = { browserName: 'Safari', platformName: platform };
if (platform === 'iOS') {
  capabilities['safari:useSimulator'] = true;
  capabilities['safari:deviceUDID'] = process.env.SIMULATOR_UDID;
}
const endpoint = 'http://127.0.0.1:4444';
let session = '';
async function request(method, path, data) {
  const res = await fetch(endpoint + session + path, { method,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    signal: AbortSignal.timeout(90000) });
  const { value } = await res.json();
  if (!res.ok) throw new Error(method + ' ' + path + ': ' + JSON.stringify(value));
  return value;
}
const js = (script, ...args) => request('POST', '/execute/sync', { script, args });
const elementKey = 'element-6066-11e4-a52e-4f735466cecf';
async function element(expression) {
  const result = await js('return ' + expression);
  assert.ok(result?.[elementKey], expression);
  return '/element/' + result[elementKey];
}
async function waitFor(expression) {
  for (let i = 0; i < 80; i++) {
    if (await js('return Boolean(' + expression + ')')) return;
    await delay(150);
  }
  const snapshot = await js('const c=window.fixtureCard;return {ride:c?.activeRide?.vehicle_label,'
    + 'busy:c?.busy,events:window.userEvents,picker:c?.view?.ui.picker.open}');
  throw new Error('Timed out: ' + expression + ' ' + JSON.stringify(snapshot));
}
const dom = 'fixtureCard.shadowRoot';
const ui = 'fixtureCard.view.ui.';
async function click(expression) {
  // On iPhone the library and map occupy different screens. Finish scrolling before the tap.
  await js(expression + '.scrollIntoView({block:"center",inline:"nearest",behavior:"instant"})');
  await request('POST', '/execute/async', { script:
    'const done=arguments[0];requestAnimationFrame(()=>requestAnimationFrame(()=>done(null)))', args: [] });
  if (platform !== 'iOS') {
    await request('POST', await element(expression) + '/click', {});
    return;
  }
  await delay(400);
  const target = await js('const e=' + expression + ',r=e.getBoundingClientRect();'
    + 'const x=Math.round(r.left+r.width/2), y=Math.round(r.top+r.height/2);'
    + 'const hit=e.getRootNode().elementFromPoint(x,y); return {x,y,hit:hit?.tagName,'
    + 'matches:e===hit||e.contains(hit),width:innerWidth,height:innerHeight}');
  assert.equal(target.matches, true, expression + ' ' + JSON.stringify(target));
  await request('POST', '/actions', { actions: [{ type: 'pointer', id: 'finger',
    parameters: { pointerType: 'touch' }, actions: [
      { type: 'pointerMove', x: target.x, y: target.y, duration: 0, origin: 'viewport' },
      { type: 'pointerDown', button: 0 }, { type: 'pause', duration: 100 }, { type: 'pointerUp', button: 0 },
    ] }] });
  await request('DELETE', '/actions');
}

async function voiceOverPhrases() {
  // Opt-in only on the disposable macOS runner. Read VoiceOver's own last utterance.
  const command = (file, args) => execFileSync(file, args,
    { encoding: 'utf8', timeout: 4500, stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  const heard = {};
  let unavailable;
  try {
    command('defaults', ['write', 'com.apple.VoiceOver4/default', 'SCREnableAppleScript', '-bool', 'true']);
    command('open', ['-g', '-a', 'VoiceOver']);
    await delay(10000);
    for (const [name, control] of [['choice', 'initialChip'], ['sample', ui + 'slider']]) {
      await js(control + '.focus()');
      for (let attempt = 0; attempt < 6; attempt++) {
        await delay(550);
        const phrase = command('osascript', ['-e', 'tell application "VoiceOver" to get content of last phrase']);
        if (name === 'choice' ? phrase.includes('Synthetic A') : /Point \d+ of 58/i.test(phrase)) {
          heard[name] = phrase;
          break;
        }
      }
    }
  } catch (error) { unavailable = error.code || error.status || 'unavailable'; }
  console.log('VoiceOver utterances:', JSON.stringify({ heard, unavailable }));
  if (!heard.choice || !heard.sample) {
    console.log('::notice::VoiceOver speech was not confirmed for both controls. Browser accessibility checks are reported separately.');
  }
}
const { server, origin } = await serveReplay();
try {
  const opened = await request('POST', '/session', { capabilities: { alwaysMatch: capabilities } });
  session = '/session/' + opened.sessionId;
  console.log('Safari session:', opened.capabilities.browserVersion, opened.capabilities.platformName,
    opened.capabilities['safari:platformVersion']);
  await request('POST', '/url', { url: origin + '/?overlap&english&dashboard' });
  await waitFor('window.fixtureCard?.routeMap.renderer && !fixtureCard.busy');
  await js('window.userEvents=[]; for(const type of ["pointerdown","pointerup","pointercancel","click","change"])' +
    'document.addEventListener(type,e=>{const n=e.composedPath()[0];userEvents.push([type,n.dataset?.control||n.tagName,n.className,e.clientX,e.clientY]);' +
    'if(userEvents.length>12)userEvents.shift()},true)');
  await click(dom + '.querySelectorAll(".overlay")[1]');
  await waitFor('fixtureCard.activeTrack?.label === "Synthetic B" && !fixtureCard.busy');
  const readCount = await js('return fixtureRequests.length');
  const metrics = () => js('return Array.from(' + dom + '.querySelectorAll(".playback > .metrics dd"), n => n.textContent)');
  const initial = await metrics();
  assert.equal(initial[0], '2.4 km');
  assert.equal(initial[2], '180 Wh');
  await js('window.initialMap = fixtureCard.routeMap.renderer.map; window.initialChip = ' +
    dom + '.querySelector(".focus-track")');
  await click('fixtureCard.routeMap.root.querySelectorAll(".ninebot-route-pin")[1]');
  await waitFor(ui + 'picker.open');
  const dialog = await element(ui + 'picker');
  assert.equal(await request('GET', dialog + '/computedrole'), 'dialog');
  assert.equal(await request('GET', dialog + '/computedlabel'), 'Choose a ride at this spot');
  const options = await js('return Array.from(' + ui + 'choices.children, b => b.textContent)');
  assert.equal(options.length, 2);
  assert.match(options[0], /1 · Synthetic A/);
  assert.match(options[1], /2 · Synthetic B/);
  await click(ui + 'choices.firstElementChild');
  await waitFor(dom + '.activeElement === initialChip && fixtureCard.activeTrack?.label === "Synthetic A"');
  assert.equal(await js('return initialChip.getAttribute("aria-pressed")'), 'true');
  const active = await metrics();
  assert.equal(active[0], '1.2 km');
  assert.notEqual(active[1], initial[1]);
  assert.equal(active[2], '90 Wh');
  const slider = await element(ui + 'slider');
  const role = await request('GET', slider + '/computedrole');
  const label = await request('GET', slider + '/computedlabel');
  assert.equal(role, 'slider');
  assert.match(label, /1 · Synthetic A/);
  const announcement = await js('return ' + ui + 'activeRoute.textContent');
  assert.match(announcement, /Current ride · 1 · Synthetic A/);
  await click(ui + 'play');
  await waitFor('fixtureCard.index > 0 && fixtureCard.playback');
  const box = await js('const s = ' + ui + 'slider; s.scrollIntoView({block:"center"});' +
    'const r = s.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]');
  // WebDriver uses a single pointer on both platforms. Mobile Safari delivers a finger gesture.
  await js('window.replayInputs = []; for (const name of ["pointerdown","input","change"]) ' +
    ui + 'slider.addEventListener(name, e => replayInputs.push([e.type, e.isTrusted, e.pointerType]))');
  const [left, top, width, height] = box;
  const move = (fraction, duration) => ({ type: 'pointerMove', duration,
    x: Math.round(left + width * fraction), y: Math.round(top + height / 2), origin: 'viewport' });
  await request('POST', '/actions', { actions: [{ type: 'pointer', id: 'reader',
    parameters: { pointerType: platform === 'iOS' ? 'touch' : 'mouse' },
    actions: [move(.15, 0), { type: 'pointerDown', button: 0 }, { type: 'pause', duration: 220 },
      move(.7, 300), { type: 'pointerUp', button: 0 }] }] });
  const state = await js('const c=fixtureCard,u=c.view.ui,i=c.index,p=c.activeTrack.points[i],'
    + 'xy=c.routeMap.renderer.focus.getLatLng();return {index:i,count:c.activeTrack.points.length,'
    + 'sequence:p.sequence,cursor:[xy.lat,xy.lng],point:[p.latitude,p.longitude],position:u.position.textContent,'
    + 'value:u.slider.getAttribute("aria-valuetext"),playing:!!c.playback,requests:fixtureRequests.length,'
    + 'stableMap:initialMap===c.routeMap.renderer.map,announcement:u.activeRoute.textContent,inputs:replayInputs}');
  assert.ok(state.inputs.some(([name, trusted]) => name === 'input' && trusted), JSON.stringify(state));
  assert.ok(state.index > 16 && state.index < state.count - 1, JSON.stringify(state));
  assert.equal(state.playing, false);
  assert.deepEqual(state.cursor, state.point);
  assert.equal(state.position, (state.index + 1) + ' / ' + state.count);
  assert.equal(state.value, 'Point ' + (state.index + 1) + ' of ' + state.count +
    ' · Source point sequence ' + state.sequence);
  assert.equal(state.stableMap, true);
  assert.equal(state.announcement, announcement);
  assert.equal(state.requests, readCount);
  assert.deepEqual(await metrics(), active);
  console.log(JSON.stringify({ platform, role, label, options, metrics: active, ...state }));
  if (platform === 'macOS' && process.env.PROBE_VOICEOVER === '1' &&
      process.env.RUNNER_ENVIRONMENT === 'github-hosted') await voiceOverPhrases();
} finally {
  try { if (session) await request('DELETE', ''); }
  finally { await new Promise(resolve => server.close(resolve)); }
}
