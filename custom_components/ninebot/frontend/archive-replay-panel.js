/** Authenticated Home Assistant host for the same replay used by the standalone example. */
import { mountReplay, replayLocale, REPLAY_COPY } from './archive-replay-view.js';
import { FleetBrowser } from './fleet-browser.js';
import { watchHistory } from './history-network.js';
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
    stale: '档案正在更新…', coverage: '所选日期内有月份的本地清单缺失或未确认完整。',
    zone: '日期按上海时区计算。', limited: '仅显示前 100 辆可用车辆。',
  },
  en: {
    account: 'Account', choose: 'Choose an account', accountName: 'Account {n}', vehicle: 'Vehicle', all: 'All vehicles in this account',
    start: 'Start date', end: 'End date', apply: 'Show rides', refresh: 'Check access again',
    loading: 'Reading the local archive…', noAccess: 'No vehicles are available for replay.',
    chooseAccount: 'Choose a Ninebot account to open its archive.',
    disconnected: 'Disconnected from Home Assistant.', withdrawn: 'Ride or location access was withdrawn. Choose an account again.',
    unavailable: 'The local archive is unavailable. Check access again.', invalid: 'Choose a valid date range of up to five years.',
    stale: 'The archive is updating…', coverage: 'Some months in this range have missing or unverified local ride lists.',
    zone: 'Dates use the Shanghai time zone.', limited: 'Showing the first 100 available vehicles.',
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
  <output id="date-note"></output>`;

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
    this._visibility = () => { if (document.hidden) this._view?.pause(); };
    this._pagehide = () => this._clearSession();
    this._pageshow = event => { if (event.persisted) void this._discover(); };
    this._lost = () => this._setOnline(false);
    this._ready = () => this._setOnline(true);
  }

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
    this._mount(replayLocale(this._hass?.language));
    this._bindConnection();
    document.addEventListener('visibilitychange', this._visibility);
    window.addEventListener('pagehide', this._pagehide);
    window.addEventListener('pageshow', this._pageshow);
    const online = Boolean(this._user && this._connection && this._hass.connected !== false &&
      this._connection.connected !== false);
    this._setOnline(online, true);
  }

  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._visibility);
    window.removeEventListener('pagehide', this._pagehide);
    window.removeEventListener('pageshow', this._pageshow);
    this._unbindConnection();
    this._clearSession();
    this._online = false;
  }

  _mount(locale) {
    this._locale = locale;
    this._text = TEXT[locale];
    this._view = mountReplay(this.shadowRoot, locale, {
      detail: (service, data) => this._readDetail(service, data),
      onTrackError: error => void this._discover(
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
    if (online) void this._discover();
    else {
      this._clearSession();
      this._view?.status(this._text.disconnected);
    }
  }

  _clearScope() {
    const scope = this._scope;
    this._scope = null;
    scope?.stop?.();
    scope?.browser.disconnect();
    this._view?.clear();
  }

  _clearSession() {
    this._session++;
    this._clearScope();
    this._accounts.clear();
    this._account = '';
    this._renderAccounts(false);
  }

  async _discover(reason, autoOpen = true) {
    this._clearSession();
    if (!this._online || !this.isConnected) return;
    const session = this._session;
    this._view.status(this._text[reason ?? 'loading']);
    try {
      const response = await this._hass.callWS({ type: 'ninebot/history/vehicles' });
      if (session !== this._session || !this.isConnected) return;
      this._accounts = accountGroups(response?.vehicles);
      this._renderAccounts(response.more_available === true);
      if (autoOpen && this._accounts.size === 1) this._chooseAccount(this._accounts.keys().next().value);
      else this._view.status(this._text[reason ?? (this._accounts.size ? 'chooseAccount' : 'noAccess')]);
    } catch {
      if (session === this._session) this._view.status(this._text[reason ?? 'unavailable']);
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
    el('refresh').disabled = !this._online;
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

  _chooseAccount(account) {
    this._clearScope();
    this._account = this._accounts.has(account) ? account : '';
    this._view.el('account').value = this._account;
    this._renderVehicles();
    if (this._account) this._open(this._view.el('vehicle').value);
    else this._view.status(this._text.chooseAccount);
  }

  _open(device) {
    this._clearScope();
    const targets = (this._accounts.get(this._account) ?? []).filter(vehicle => !device || vehicle.device_id === device);
    if (!this._online || !targets.length || targets.length > MAX_VEHICLES) return;
    this._view.status(this._text.loading);
    const browser = new FleetBrowser(() => this._browseChanged(scope), () => {
      if (this._scope === scope) this._view.clear();
    });
    const scope = { browser, version: null, stop: null };
    this._scope = scope;
    browser.setHass(this._hass);
    browser.configure({ vehicles: targets, start_date: this._dates[0], end_date: this._dates[1] });
    try {
      const stop = watchHistory(this._hass, targets.map(target => target.device_id),
        event => this._handleFeed(scope, event), { includeTrack: true });
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
    scope.version = version;
    if (initial) scope.browser.connect();
    else {
      this._view.clear();
      scope.browser.clearPage();
      this._view.status(this._text.loading);
      void scope.browser.read();
    }
  }

  _browseChanged(scope) {
    if (this._scope !== scope || !scope.version) return;
    const { browser } = scope, page = browser.page;
    if (browser.error === 'denied') {
      void this._discover('withdrawn', false);
      return;
    }
    if (browser.error) {
      this._view.clear();
      this._view.status(REPLAY_COPY[this._locale].loadFailed);
      return;
    }
    if (!page) return;
    if (browser.streams.some(stream => stream.revision !== scope.version.get(stream.device))) {
      this._view.clear();
      this._view.status(this._text.stale);
      return;
    }
    const coverage = page.coverage;
    const partial = ['missing_months', 'partial_months', 'unknown_months'].some(key => coverage[key]?.length);
    this._view.setPage(page.rides, {
      number: browser.pageNumber, previous: browser.pages.has(browser.pageNumber - 1),
      next: Boolean(page.next_cursor || browser.pages.has(browser.pageNumber + 1)),
      busy: browser.busy, note: partial ? this._text.coverage : '',
    });
  }

  _readDetail(service, data) {
    const scope = this._scope;
    if (!scope?.version?.has(data.device_id) || !this._online ||
        service !== 'get_trip_detail' || data.local_only !== true || data.include_track !== true) {
      throw new Error('unauthorized');
    }
    return scope.browser.invoke(service, data);
  }
}

if (!customElements.get('ninebot-archive-panel')) {
  customElements.define('ninebot-archive-panel', NinebotArchivePanel);
}
