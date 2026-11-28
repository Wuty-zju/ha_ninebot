/** Authenticated Home Assistant host for the same replay used by the standalone example. */
import { mountReplay, replayLocale, REPLAY_COPY } from './archive-replay-view.js';
import { FleetBrowser } from './fleet-browser.js';
import { invokeHistory, watchHistory } from './history-network.js';
import { LinkedRideView, readRideLink } from './linked-ride-view.js';
import { recentDates } from './trip-browser.js';

const MAX_VEHICLES = 5;
const MAX_DAYS = 5 * 366;
const TEXT = {
  'zh-Hans': {
    account: '账号', choose: '选择账号', accountName: '账号 {n}', vehicle: '车辆', all: '账号下的全部车辆',
    start: '起始日期', end: '截止日期', apply: '查看行程', refresh: '重新检查权限',
    loading: '正在读取本地档案…', noAccess: '没有可以回放的车辆。', chooseAccount: '选择一个九号账号以查看行程。',
    disconnected: '与 Home Assistant 的连接已断开。', withdrawn: '行程或位置权限已失效，请重新选择账号。',
    unavailable: '本地档案暂时不可用，请重新检查权限。', invalid: '请选择不超过五年的有效日期范围。',
    cursorExpired: '档案分页已失效。点击“查看行程”从第一页开始。',
    stale: '档案正在更新…', coverage: '所选日期内有月份的本地清单缺失或未确认完整。',
    zone: '日期按上海时区计算。', limited: '仅显示前 100 辆可用车辆。',
    archive: '浏览行程档案', reopen: '重新打开行程', linkUnavailable: '无法打开这组行程。请检查权限或重新选择行程。',
    updated: '档案有更新。重新打开行程即可查看。',
  },
  en: {
    account: 'Account', choose: 'Choose an account', accountName: 'Account {n}', vehicle: 'Vehicle', all: 'All vehicles in this account',
    start: 'Start date', end: 'End date', apply: 'Show rides', refresh: 'Check access again',
    loading: 'Reading the local archive…', noAccess: 'No vehicles are available for replay.',
    chooseAccount: 'Choose a Ninebot account to open its archive.',
    disconnected: 'Disconnected from Home Assistant.', withdrawn: 'Ride or location access was withdrawn. Choose an account again.',
    unavailable: 'The local archive is unavailable. Check access again.', invalid: 'Choose a valid date range of up to five years.',
    cursorExpired: 'This archive page has expired. Select Show rides to start again.',
    stale: 'The archive is updating…', coverage: 'Some months in this range have missing or unverified local ride lists.',
    zone: 'Dates use the Shanghai time zone.', limited: 'Showing the first 100 available vehicles.',
    archive: 'Browse ride archive', reopen: 'Reopen rides', linkUnavailable: 'These rides could not be opened. Check access or select the rides again.',
    updated: 'The archive has changed. Reopen the rides to see the update.',
  },
};

const controls = `
  <form>
    <label><span id="account-label"></span><select id="account" aria-labelledby="account-label"></select></label>
    <label id="vehicle-filter"><span id="vehicle-label"></span><select id="vehicle" aria-labelledby="vehicle-label"></select></label>
    <label><span id="start-label"></span><input id="start-date" aria-labelledby="start-label" type="date" required></label>
    <label><span id="end-label"></span><input id="end-date" aria-labelledby="end-label" type="date" required></label>
    <button id="apply" type="submit"></button>
    <button id="refresh" type="button"></button>
  </form>
  <output id="date-note"></output>
  <nav id="linked-controls" hidden><a class="language" href="/ninebot-archive" id="all-rides"></a>
    <button id="reopen" type="button"></button></nav>`;

function accountGroups(vehicles) {
  if (!Array.isArray(vehicles) || vehicles.length > 100) throw new Error('invalid_discovery');
  const accounts = new Map(), devices = new Set();
  for (const { account_id, device_id, label } of vehicles) {
    if (![account_id, device_id, label].every(value =>
      typeof value === 'string' && value.length > 0 && value.length <= 128) || devices.has(device_id)) {
      throw new Error('invalid_discovery');
    }
    devices.add(device_id);
    if (!accounts.has(account_id)) accounts.set(account_id, []);
    accounts.get(account_id).push({ device_id, name: label });
  }
  return accounts;
}

function appendOption(select, value, label) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = label;
  select.append(option);
}

function isDateRange(start, end) {
  if (![start, end].every(value => /^20\d\d-\d\d-\d\d$/.test(value))) return false;
  const days = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
  return days >= 1 && days <= MAX_DAYS;
}

export class NinebotArchivePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._accounts = new Map();
    this._session = 0;
    this._visibility = () => {
      if (document.hidden) this._suspend();
      else this._resume();
    };
    this._pagehide = () => {
      this._pageHidden = true;
      this._suspend();
    };
    this._pageshow = () => {
      this._pageHidden = false;
      this._resume();
    };
    this._lost = () => this._setOnline(false);
    this._ready = () => this._setOnline(true);
    this._routeChanged = () => {
      if (this.isConnected && this._online && this._search !== window.location.search) void this._discover();
    };
  }

  set route(_value) { this._routeChanged(); }

  set hass(value) {
    const user = value?.user?.id, connection = value?.connection, locale = replayLocale(value?.language);
    const identityChanged = user !== this._user || connection !== this._connection;
    this._hass = value;
    if (identityChanged) {
      this._clearSession();
      this._unbindConnection();
      this._user = user;
      this._connection = connection;
      if (this.isConnected) this._bindConnection();
    }
    const online = Boolean(user && connection && value.connected !== false && connection.connected !== false);
    if (!this.isConnected) return;
    if (locale !== this._locale) {
      this._clearSession();
      this._mount(locale);
      this._setOnline(online, true);
    } else this._setOnline(online, identityChanged);
    if (this._scope) {
      this._scope.browser.hass = value;
      this._scope.browser.startIfReady();
    }
  }

  connectedCallback() {
    this._suspended = document.hidden;
    this._mount(replayLocale(this._hass?.language));
    this._bindConnection();
    document.addEventListener('visibilitychange', this._visibility);
    window.addEventListener('pagehide', this._pagehide);
    window.addEventListener('pageshow', this._pageshow);
    window.addEventListener('popstate', this._routeChanged);
    window.addEventListener('location-changed', this._routeChanged);
    const online = Boolean(this._user && this._connection && this._hass.connected !== false &&
      this._connection.connected !== false);
    this._setOnline(online, true);
  }

  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._visibility);
    window.removeEventListener('pagehide', this._pagehide);
    window.removeEventListener('pageshow', this._pageshow);
    window.removeEventListener('popstate', this._routeChanged);
    window.removeEventListener('location-changed', this._routeChanged);
    this._unbindConnection();
    this._clearSession();
    this._online = false;
    this._pageHidden = false;
  }

  _mount(locale) {
    this._locale = locale;
    this._text = TEXT[locale];
    this._view = mountReplay(this.shadowRoot, locale, {
      detail: (service, data) => this._readDetail(service, data),
      getMapHass: () => this._linked ? this._hass : null,
      readCollectionRide: ref => this._collectionRide(ref),
      onTrackError: error => this._linked ? this._linked.fail() : void this._discover(
        ['unauthorized', 'query_coordinates', 'query_device'].includes(error) ? 'withdrawn' : 'unavailable', false),
    });
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = new URL('./archive-replay.css', import.meta.url).href;
    this.shadowRoot.prepend(css);
    const { el } = this._view;
    const area = el('archive-controls');
    area.innerHTML = controls;
    for (const field of ['account', 'vehicle', 'start', 'end']) {
      el(field + '-label').textContent = this._text[field];
    }
    el('apply').textContent = this._text.apply;
    el('refresh').textContent = this._text.refresh;
    el('all-rides').textContent = this._text.archive;
    el('reopen').textContent = this._text.reopen;
    el('reopen').addEventListener('click', () => void this._discover());
    el('date-note').textContent = this._text.zone;
    el('menu').hidden = false;
    el('menu').addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-toggle-menu', {
      bubbles: true, composed: true,
    })));
    this._dates = recentDates(30);
    [el('start-date').value, el('end-date').value] = this._dates;
    el('account').addEventListener('change', () => this._chooseAccount(el('account').value));
    el('vehicle').addEventListener('change', () => this._open(el('vehicle').value));
    el('refresh').addEventListener('click', () => void this._discover());
    el('previous').addEventListener('click', () => {
      const browser = this._scope?.browser;
      browser?.showPage(browser.pageNumber - 1);
    });
    el('next').addEventListener('click', () => {
      const browser = this._scope?.browser;
      if (!browser) return;
      if (browser.pages.has(browser.pageNumber + 1)) browser.showPage(browser.pageNumber + 1);
      else if (browser.page?.next_cursor) void browser.read(browser.page.next_cursor);
    });
    area.querySelector('form').addEventListener('submit', event => {
      event.preventDefault();
      const dates = [el('start-date').value, el('end-date').value];
      if (!isDateRange(...dates)) {
        this._view.status(this._text.invalid);
        return;
      }
      this._dates = dates;
      const browser = this._scope?.browser;
      if (browser && browser.start === dates[0] && browser.end === dates[1] && !browser.error) return;
      this._open(el('vehicle').value);
    });
  }

  _bindConnection() {
    this._connection?.addEventListener?.('disconnected', this._lost);
    this._connection?.addEventListener?.('ready', this._ready);
  }

  _unbindConnection() {
    this._connection?.removeEventListener?.('disconnected', this._lost);
    this._connection?.removeEventListener?.('ready', this._ready);
  }

  _setOnline(online, changed = false) {
    if (!changed && this._online === online) return;
    this._online = online;
    if (online) {
      if (!this._suspended) void this._discover();
    }
    else {
      this._clearSession();
      this._view?.status(this._text.disconnected);
    }
  }

  _clearScope() {
    this._linked?.close();
    this._linked = null;
    const scope = this._scope;
    this._scope = null;
    scope?.stop?.();
    scope?.browser.disconnect();
    this._view?.clear();
  }

  _forgetResume() {
    this._resumeState?.scope?.browser.disconnect();
    this._resumeState = null;
  }

  _clearSession(keepResume = false) {
    this._session++;
    this._clearScope();
    if (!keepResume) this._forgetResume();
    this._accounts.clear();
    this._account = '';
    this._renderAccounts(false);
  }

  _suspend() {
    if (this._suspended) return;
    this._suspended = true;
    const scope = this._scope;
    if (scope) {
      const { browser } = scope;
      // An interrupted list read may have consumed continuation tokens. Keep only idle, verified pages.
      if (!scope.resume && browser.page && !browser.busy && !browser.error && this._listMatches(scope)) {
        scope.resume = { selection: this._view.bookmark() };
      }
      this._resumeState = { account: this._account, device: this._view.el('vehicle').value,
        devices: browser.targets.map(target => target.device_id), scope: scope.resume ? scope : null };
      if (scope.resume) {
        this._scope = null;
        scope.stop?.();
        scope.stop = null;
        browser.suspend();
      }
    }
    this._clearSession(true);
    if (this._online) this._view.status(this._text.loading);
  }

  _resume() {
    if (!this._suspended || this._pageHidden || document.hidden) return;
    this._suspended = false;
    if (this._online) void this._discover(undefined, true, true);
  }

  async _discover(reason, autoOpen = true, restoring = false) {
    if (this._suspended) return;
    this._clearSession(restoring);
    if (!this._online || !this.isConnected) return;
    this._search = window.location.search;
    const link = readRideLink(this._search);
    this._view.el('archive-controls').querySelector('form').hidden = Boolean(link);
    this._view.el('date-note').hidden = Boolean(link);
    this._view.el('linked-controls').hidden = !link;
    if (link) {
      if (link.kind === 'invalid') {
        this._view.status(this._text.linkUnavailable);
        return;
      }
      this._view.status(this._text.loading);
      this._linked = new LinkedRideView(this._hass, link, this._view,
        () => this._view.status(this._text.linkUnavailable), () => this._view.status(this._text.updated));
      await this._linked.open();
      return;
    }
    const session = this._session;
    this._view.status(this._text[reason ?? 'loading']);
    try {
      const response = await this._hass.callWS({ type: 'ninebot/history/vehicles' });
      if (session !== this._session || !this.isConnected) return;
      this._accounts = accountGroups(response?.vehicles);
      this._renderAccounts(response.more_available === true);
      const resume = this._resumeState;
      if (resume) {
        const ids = new Set(this._accounts.get(resume.account)?.map(target => target.device_id));
        if (!resume.devices.every(device => ids.has(device)) || (!resume.device && ids.size > MAX_VEHICLES)) {
          this._forgetResume();
          this._view.status(this._text.withdrawn);
          return;
        }
        this._resumeState = null;
        this._chooseAccount(resume.account, resume);
      } else if (autoOpen && this._accounts.size === 1) this._chooseAccount(this._accounts.keys().next().value);
      else this._view.status(this._text[reason ?? (this._accounts.size ? 'chooseAccount' : 'noAccess')]);
    } catch {
      if (session === this._session) {
        this._forgetResume();
        this._view.status(this._text[reason ?? 'unavailable']);
      }
    }
  }

  _renderAccounts(limited) {
    if (!this._view) return;
    const { el } = this._view;
    const select = el('account');
    select.replaceChildren();
    appendOption(select, '', this._text.choose);
    let index = 0;
    for (const [id, vehicles] of this._accounts) {
      const name = this._text.accountName.replace('{n}', ++index);
      appendOption(select, id, `${name} · ${vehicles[0].name}`);
    }
    select.value = this._account;
    select.disabled = !this._accounts.size;
    el('refresh').disabled = !this._online || this._suspended;
    el('date-note').textContent = limited ? `${this._text.zone} ${this._text.limited}` : this._text.zone;
    this._renderVehicles();
  }

  _renderVehicles() {
    const vehicles = this._accounts.get(this._account) ?? [];
    const { el } = this._view;
    const filter = el('vehicle');
    filter.replaceChildren();
    if (vehicles.length > 1 && vehicles.length <= MAX_VEHICLES) appendOption(filter, '', this._text.all);
    for (const { device_id, name } of vehicles) appendOption(filter, device_id, name);
    el('vehicle-filter').hidden = vehicles.length < 2;
    for (const id of ['start-date', 'end-date', 'apply']) el(id).disabled = !vehicles.length;
  }

  _chooseAccount(account, resume = null) {
    this._clearScope();
    this._account = this._accounts.has(account) ? account : '';
    this._view.el('account').value = this._account;
    this._renderVehicles();
    if (this._account) {
      const filter = this._view.el('vehicle');
      if (resume) filter.value = resume.device;
      this._open(filter.value, resume?.scope);
    }
    else this._view.status(this._text.chooseAccount);
  }

  _open(device, savedScope) {
    this._clearScope();
    const targets = (this._accounts.get(this._account) ?? []).filter(vehicle => !device || vehicle.device_id === device);
    if (!this._online || this._suspended || !targets.length || targets.length > MAX_VEHICLES) return;
    this._view.status(this._text.loading);
    const reusable = savedScope && targets.length === savedScope.browser.targets.length && targets.every(target =>
      savedScope.browser.targets.some(old => old.device_id === target.device_id && old.name === target.name));
    const scope = reusable ? savedScope : { browser: null, stop: null };
    if (!reusable) {
      savedScope?.browser.disconnect();
      scope.browser = new FleetBrowser(() => this._browseChanged(scope), () => {
        if (this._scope === scope) this._view.clear();
      });
    }
    scope.version = null;
    this._scope = scope;
    const { browser } = scope;
    browser.setHass(this._hass);
    if (!reusable) browser.configure({ vehicles: targets, start_date: this._dates[0], end_date: this._dates[1] });
    try {
      // A new authorization boundary needs a fresh baseline, including when another panel watches these vehicles.
      const stop = watchHistory(this._hass, targets.map(target => target.device_id),
        event => this._handleFeed(scope, event), { includeTrack: true, shared: false });
      if (this._scope === scope) scope.stop = stop;
      else stop();
    } catch {
      void this._discover('unavailable', false);
    }
  }

  _handleFeed(scope, event) {
    if (this._scope !== scope) return;
    if (event.type !== 'baseline' && event.type !== 'changed') {
      void this._discover(event.type === 'denied' ? 'withdrawn' : 'unavailable', false);
      return;
    }
    const version = new Map(event.vehicles.map(row => [row.device_id, row.selection_version]));
    if (scope.version && [...version].every(([device, key]) => scope.version.get(device) === key)) return;
    const initial = !scope.version;
    if (initial && event.type !== 'baseline') return;
    scope.version = version;
    if (initial) {
      const saved = scope.resume;
      scope.resume = null;
      if (saved && !this._listMatches(scope)) scope.browser.clearPage();
      scope.browser.connect();
      if (scope.browser.page) this._browseChanged(scope, saved?.selection);
    } else {
      this._view.clear();
      scope.browser.clearPage();
      this._view.status(this._text.loading);
      void scope.browser.read();
    }
  }

  _listMatches({ browser, version }) {
    return version && browser.streams.length === version.size &&
      browser.streams.every(stream => stream.revision === version.get(stream.device));
  }

  _browseChanged(scope, restore) {
    if (this._scope !== scope || !scope.version) return;
    const { browser } = scope, page = browser.page;
    if (browser.error === 'denied') {
      void this._discover('withdrawn', false);
      return;
    }
    if (browser.error) {
      this._view.clear();
      this._view.status(browser.error === 'cursor_error' ? this._text.cursorExpired : REPLAY_COPY[this._locale].loadFailed);
      return;
    }
    if (!page) return;
    if (!this._listMatches(scope)) {
      this._view.clear();
      this._view.status(this._text.stale);
      return;
    }
    const coverage = page.coverage;
    const partial = ['missing_months', 'partial_months', 'unknown_months'].some(key => coverage[key]?.length);
    this._view.setPage(page.rides, {
      number: browser.pageNumber, previous: browser.pages.has(browser.pageNumber - 1),
      next: Boolean(page.next_cursor || browser.pages.has(browser.pageNumber + 1)),
      busy: browser.busy, note: partial ? this._text.coverage : '', restore,
      collectionScope: JSON.stringify([this._user, this._account]),
    });
  }

  _accountCanRead(scope, device) {
    return Boolean(scope?.version && this._scope === scope && this._online && !this._suspended &&
      this._accounts.get(this._account)?.some(vehicle => vehicle.device_id === device));
  }

  async _collectionRide(ref) {
    const scope = this._scope;
    if (!this._accountCanRead(scope, ref.device_id)) throw new Error('unauthorized');
    const result = await this._hass.callWS({ type: 'ninebot/history/ride_summary',
      account_id: this._account, device_id: ref.device_id, query_month: ref.query_month, ride_id: ref.ride_id });
    if (!this._accountCanRead(scope, ref.device_id)) throw new Error('unauthorized');
    return result.ride;
  }

  _readDetail(service, data) {
    if (this._linked) return this._linked.detail(service, data);
    const scope = this._scope;
    if (!this._accountCanRead(scope, data.device_id) ||
        service !== 'get_trip_detail' || data.local_only !== true || data.include_track !== true) {
      throw new Error('unauthorized');
    }
    // Saved favorites may include other authorized vehicles in the same account.
    return invokeHistory(this._hass, service, data, () => this._accountCanRead(scope, data.device_id));
  }
}

if (!customElements.get('ninebot-archive-panel')) {
  customElements.define('ninebot-archive-panel', NinebotArchivePanel);
}
