/** Dashboard config editor using the documented config-changed custom-card API. */
import { vehicleTargets, nativeCards } from './fleet-browser.js';
import { TripBrowser } from './trip-browser.js';
import { LABELS } from './labels.js';

export function checkedConfig(config) {
  const targets = vehicleTargets(config);
  new TripBrowser().configure({ ...config, ...targets[0], vehicles: undefined });
  nativeCards(targets, config.statistics_entities ?? []);
  return { ...config };
}

export class NinebotTripCardEditor extends (globalThis.HTMLElement ?? class {}) {
  constructor() { super(); this.attachShadow({ mode: 'open' }); }
  setConfig(config) { this.config = checkedConfig(config); this.render(); }
  set hass(value) { this._hass = value; this.render(); }
  render() {
    if (!this.config) return;
    const locale = this._hass?.language ?? 'en';
    const labels = LABELS[locale] ?? (locale.startsWith('zh') ? LABELS['zh-Hans'] : LABELS.en);
    const root = document.createElement('div');
    const style = document.createElement('style');
    style.textContent = ':host{display:block}label{display:flex;flex-direction:column;gap:6px;margin:12px 0}input,select,button{font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:10px}fieldset{border:1px solid var(--divider-color);margin:12px 0;border-radius:10px}button{margin:4px}p{color:var(--error-color)}';
    const emit = candidate => {
      try {
        checkedConfig(candidate); this.config = candidate;
        this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: candidate }, bubbles: true, composed: true }));
        this.render();
      } catch (error) { message.textContent = String(error.message); }
    };
    const field = (parent, text, value, changed, type = 'text') => {
      const label = document.createElement('label'); label.textContent = text;
      const input = document.createElement('input'); input.type = type; input.value = value ?? '';
      input.addEventListener('change', () => changed(input.value)); label.append(input); parent.append(label); return input;
    };
    const button = (parent, text, action) => {
      const value = document.createElement('button'); value.type = 'button'; value.textContent = text;
      value.addEventListener('click', action); parent.append(value); return value;
    };
    const option = (key, value) => {
      const candidate = { ...this.config };
      if (value === '') delete candidate[key]; else candidate[key] = value;
      emit(candidate);
    };
    const targets = vehicleTargets(this.config);
    const replace = values => {
      const candidate = { ...this.config };
      delete candidate.entity; delete candidate.device_id; delete candidate.vehicles; delete candidate.name;
      if (values.length === 1) Object.assign(candidate, values[0]); else candidate.vehicles = values;
      emit(candidate);
    };
    field(root, labels.editor_title, this.config.title, value => option('title', value));
    const limit = field(root, labels.editor_limit, this.config.limit ?? 20, value => option('limit', Number(value)), 'number');
    limit.min = '1'; limit.max = '100'; limit.step = '1';
    for (const key of ['start_date', 'end_date']) field(root, labels[key], this.config[key], value => option(key, value), 'date');
    const calendars = Object.entries(this._hass?.states ?? {}).filter(([id, state]) =>
      id.startsWith('calendar.') && state.attributes?.source === 'ride_archive');
    targets.forEach((target, index) => {
      const group = document.createElement('fieldset'), legend = document.createElement('legend');
      legend.textContent = `${labels.editor_vehicle} ${index + 1}`; group.append(legend);
      const change = next => { const values = targets.map(value => ({ ...value })); values[index] = next; replace(values); };
      const label = document.createElement('label'); label.textContent = labels.editor_vehicle;
      const select = document.createElement('select');
      const options = [...calendars.map(([id, state]) => [id, state.attributes.friendly_name ?? id]), ['@device', labels.editor_device]];
      if (target.entity && !calendars.some(([id]) => id === target.entity)) options.unshift([target.entity, target.entity]);
      for (const [value, name] of options) { const option = document.createElement('option'); option.value = value; option.textContent = name; select.append(option); }
      select.value = target.entity ?? '@device';
      select.addEventListener('change', () => change({ ...(target.name ? { name: target.name } : {}),
        ...(select.value === '@device' ? { device_id: 'REPLACE_WITH_VEHICLE_DEVICE_ID' } : { entity: select.value }) }));
      label.append(select); group.append(label);
      if (!target.entity) field(group, labels.editor_device, target.device_id, value => change({ ...target, device_id: value }));
      field(group, labels.editor_name, target.name, value => {
        const next = { ...target }; if (value) next.name = value; else delete next.name; change(next);
      });
      if (targets.length > 1) button(group, labels.editor_remove, () => replace(targets.filter((_, i) => i !== index)));
      root.append(group);
    });
    const add = button(root, labels.editor_add, () => {
      const used = new Set(targets.map(target => target.entity));
      const entity = calendars.find(([id]) => !used.has(id))?.[0];
      replace([...targets, entity ? { entity } : { device_id: 'REPLACE_WITH_VEHICLE_DEVICE_ID' }]);
    });
    add.disabled = targets.length >= 5;
    const note = document.createElement('small'); note.textContent = labels.editor_note; root.append(note);
    const message = document.createElement('p'); message.setAttribute('role', 'alert'); root.append(message);
    this.shadowRoot.replaceChildren(style, root);
  }
}
if (globalThis.customElements && !customElements.get('ninebot-trip-card-editor')) {
  customElements.define('ninebot-trip-card-editor', NinebotTripCardEditor);
}
