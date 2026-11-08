/** Optional HA custom panel; no login implementation, remote libraries or cloud polling. */
import { ReviewSession, TERMINAL, TEXT } from './assistant-review.js';

const css = `
:host { display:block; color:var(--primary-text-color); background:var(--primary-background-color); min-height:100%; font-family:var(--paper-font-body1_-_font-family, sans-serif); }
header { display:flex; align-items:center; gap:16px; padding:12px 20px; background:var(--card-background-color); color:var(--primary-text-color); border-bottom:1px solid var(--divider-color); }
h1 { font-size:20px; margin:0; } header p { margin:4px 0 0; font-size:14px; }
main { max-width:880px; margin:auto; padding:24px; } .toolbar { display:flex; gap:12px; align-items:end; flex-wrap:wrap; margin-bottom:20px; }
label { display:grid; gap:6px; flex:1; min-width:180px; } select, button { font:inherit; border:1px solid var(--divider-color); border-radius:8px; padding:10px 14px; }
select { color:var(--primary-text-color); background:var(--card-background-color); width:100%; }
button { cursor:pointer; background:var(--card-background-color); color:var(--primary-text-color); } button:disabled { cursor:default; opacity:.55; }
button.primary { font-weight:600; border:2px solid var(--primary-color); }
button:focus-visible, select:focus-visible { outline:3px solid var(--primary-color); outline-offset:3px; }
article { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px; padding:20px; margin:16px 0; }
h2 { font-size:18px; margin:0 0 14px; } p { line-height:1.55; } dl { display:grid; grid-template-columns:minmax(130px,1fr) 2fr; gap:10px 20px; }
dt { color:var(--secondary-text-color); } dd { margin:0; overflow-wrap:anywhere; } .actions { display:flex; gap:12px; flex-wrap:wrap; margin-top:20px; }
.error { color:var(--error-color); } .confirm { border:2px solid var(--primary-color); } code { overflow-wrap:anywhere; }
@media(max-width:500px) { main { padding:16px; } article { padding:16px; } dl { grid-template-columns:1fr; gap:4px; } dd { margin-bottom:10px; } }
`;
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

export class NinebotAssistantPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._session = null;
    this._timer = null;
    this._visible = () => {
      this._stopTimer();
      if (this._session) {
        this._session.prepared = null;
        this._render();
        if (document.visibilityState === 'visible') this._session.refresh();
      }
    };
  }
  set hass(value) {
    const changed = !this._hass || this._hass.user?.id !== value.user?.id
      || this._hass.connection !== value.connection;
    this._hass = value;
    if (changed) {
      this._session?.stop();
      this._session = null;
      this._start();
    } else if (this._session) {
      this._session.hass = value;
      if (this._language !== value.language) this._render();
    }
  }
  get hass() { return this._hass; }
  connectedCallback() {
    document.addEventListener('visibilitychange', this._visible);
    this._start();
  }
  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._visible);
    this._stopTimer();
    this._session?.stop();
    this._session = null;
  }
  _start() {
    if (!this.isConnected || !this._hass || this._session) return;
    this._session = new ReviewSession(this._hass, () => this._render());
    this._render();
    this._session.load();
  }
  _stopTimer() { clearTimeout(this._timer); this._timer = null; }
  _button(label, action, primary = false) {
    const node = element('button', label, primary ? 'primary' : '');
    node.type = 'button';
    node.disabled = this._session.busy;
    node.addEventListener('click', action);
    return node;
  }
  _date(value) {
    if (!value) return this._text.unknown;
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString(this._hass.language || 'en', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    }) : this._text.unknown;
  }
  _row(list, label, value) {
    list.append(element('dt', label), element('dd', value ?? this._text.unknown));
  }
  _render() {
    this._stopTimer();
    const session = this._session;
    if (!session || !this.isConnected) return;
    this._language = this._hass.language;
    const t = this._text = TEXT[this._language?.startsWith('zh') ? 'zh' : 'en'];
    const style = element('style', css);
    const header = element('header');
    // Use HA's drawer event even on a direct first load, before any other panel
    // has defined optional header components.
    const menu = this._button('☰', () => this.dispatchEvent(new CustomEvent('hass-toggle-menu', {
      bubbles: true, composed: true,
    })));
    menu.setAttribute('aria-label', t.menu);
    menu.disabled = false;
    const heading = element('div'); heading.append(element('h1', t.title), element('p', t.subtitle));
    header.append(menu, heading);
    const main = element('main'); main.setAttribute('aria-busy', String(session.busy));
    const toolbar = element('div', undefined, 'toolbar');
    const label = element('label', t.vehicle);
    const select = element('select'); select.disabled = session.busy;
    const placeholder = element('option', t.choose); placeholder.value = ''; select.append(placeholder);
    for (const vehicle of session.vehicles) {
      const option = element('option', vehicle.label); option.value = vehicle.vehicle_ref; select.append(option);
    }
    select.value = session.vehicleRef;
    select.addEventListener('change', () => session.select(select.value));
    label.append(select);
    toolbar.append(label, this._button(t.refresh, () => session.vehicleRef ? session.refresh() : session.load()));
    main.append(toolbar);
    if (session.vehicleRef) {
      const link = element('a', t.device);
      link.href = `/config/devices/device/${encodeURIComponent(session.vehicleRef)}`;
      link.style.color = 'var(--primary-text-color)';
      main.append(link);
    }
    if (session.error) {
      const error = element('p', t.errors[session.error] || t.failed, 'error');
      error.setAttribute('role', 'alert'); main.append(error);
    }
    if (session.more) main.append(element('p', t.more));
    if (!session.tasks.length) main.append(element('p', session.vehicleRef ? t.empty : t.select));
    if (session.prepared) {
      const plan = session.prepared.task;
      const confirmation = element('article', undefined, 'confirm');
      confirmation.setAttribute('role', 'region');
      confirmation.setAttribute('aria-label', t.confirm);
      confirmation.append(element('h2', t.confirm), element('p', t.notice));
      const fields = element('dl');
      this._row(fields, t.vehicle, session.vehicles.find((v) => v.vehicle_ref === session.vehicleRef)?.label);
      this._row(fields, t.dates, `${plan.start_date} → ${plan.end_date}`);
      this._row(fields, t.zone, plan.time_zone);
      this._row(fields, t.selected, plan.selected_count);
      this._row(fields, t.budget, plan.maximum_detail_attempts);
      this._row(fields, t.expires, this._date(plan.approval_expires_at));
      confirmation.append(fields);
      const records = element('details'); records.append(element('summary', t.records));
      const identities = element('ul');
      for (const identity of plan.ride_ids || []) identities.append(element('li', identity));
      records.append(identities); confirmation.append(records);
      const actions = element('div', undefined, 'actions');
      actions.append(this._button(t.approve, () => session.approve(), true), this._button(t.dismiss, () => {
        session.prepared = null; this._render();
      }));
      confirmation.append(actions); main.append(confirmation);
    }
    for (const task of session.tasks) {
      const article = element('article');
      article.append(element('h2', t.states[task.state] || t.unknown));
      const fields = element('dl');
      this._row(fields, t.dates, `${task.start_date} → ${task.end_date}`);
      this._row(fields, t.zone, task.time_zone);
      this._row(fields, t.selected, task.selected_count);
      this._row(fields, t.budget, task.maximum_detail_attempts);
      this._row(fields, t.saved, task.saved_count);
      this._row(fields, t.attempts, task.attempts_used);
      if (task.progress_updated_at) this._row(fields, t.checkpoint, this._date(task.progress_updated_at));
      if (task.retry_at) this._row(fields, t.retry, this._date(task.retry_at));
      article.append(fields);
      if (t.reasons[task.reason]) article.append(element('p', t.reasons[task.reason]));
      const actions = element('div', undefined, 'actions');
      if (task.state === 'awaiting_approval') actions.append(this._button(t.review, () => session.prepare(task.task_id), true));
      if (!TERMINAL.has(task.state)) actions.append(this._button(t.cancel, () => session.cancel(task.task_id)));
      article.append(actions); main.append(article);
    }
    this.shadowRoot.replaceChildren(style, header, main);
    const seconds = session.refreshSeconds;
    if (seconds && document.visibilityState === 'visible') this._timer = setTimeout(() => session.refresh(), seconds * 1000);
  }
}
if (!customElements.get('ninebot-assistant-panel')) customElements.define('ninebot-assistant-panel', NinebotAssistantPanel);
