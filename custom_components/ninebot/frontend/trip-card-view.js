/** Map-first ride library. The card controller owns all reads and playback. */
import { formatEntityLabel, formatRideDuration, formatRideInterval } from './trip-format.js';
import { digits } from './trip-browser.js';
import { hasEndpoints, trackKey } from './local-tracks.js';
import { ROUTE_COLORS } from './route-map.js';
import { MAX_SELECTED_ROUTES, selectionTotals } from './history-selection.js';
import { HISTORY_STYLE } from './trip-card-style.js';

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function button(text, action, control, className = '') {
  const node = element('button', className, text);
  node.type = 'button';
  node.dataset.control = control;
  node.addEventListener('click', action);
  return node;
}

function nameControl(node, name) {
  node.title = name;
  node.setAttribute('aria-label', name);
}

function enableTouchChoice(choices) {
  // Mobile Safari can deliver a complete tap to a dialog button without synthesizing click.
  // Leave swipes to the browser and keep the button's normal keyboard/VoiceOver activation.
  let tap;
  choices.addEventListener('touchstart', event => {
    const finger = event.touches[0], button = event.target.closest('button');
    tap = event.touches.length === 1 && button ? { button, finger } : null;
  }, { passive: true });
  const moved = other => !other || other.identifier !== tap.finger.identifier ||
    Math.hypot(other.clientX - tap.finger.clientX, other.clientY - tap.finger.clientY) > 10;
  choices.addEventListener('touchmove', event => {
    if (tap && moved(event.touches[0])) tap = null;
  }, { passive: true });
  choices.addEventListener('touchcancel', () => { tap = null; });
  choices.addEventListener('touchend', event => {
    const choice = tap && !event.touches.length && !moved(event.changedTouches[0]) && tap.button;
    tap = null;
    if (!choice || !event.cancelable || !choices.contains(choice)) return;
    event.preventDefault();
    choice.click();
  }, { passive: false });
}

function iconButton(icon, glyph, action, control, className) {
  const node = button('', action, control, className);
  const symbol = element('ha-icon', '', glyph);
  symbol.setAttribute('icon', icon);
  symbol.setAttribute('aria-hidden', 'true');
  node.append(symbol);
  return node;
}

function metricStrip() {
  const node = element('dl', 'metrics');
  const readouts = ['distance', 'duration', 'energy'].map(key => {
    const cell = element('div'), caption = element('dt'), value = element('dd');
    cell.append(caption, value);
    node.append(cell);
    return { key, caption, value };
  });
  return { node, update(labels, values) {
    readouts.forEach(({ key, caption, value }, index) => {
      caption.textContent = labels[key];
      value.textContent = values[index] ?? '—';
    });
  } };
}

export class TripCardView {
  constructor(card) {
    this.card = card;
    const shell = element('ha-card');
    const header = element('header', 'card-heading');
    const title = element('h2');
    const messages = element('div', 'messages');
    messages.setAttribute('role', 'status');
    const activeRoute = element('span', 'sr-only');
    activeRoute.setAttribute('role', 'status');
    activeRoute.setAttribute('aria-live', 'polite');
    activeRoute.setAttribute('aria-atomic', 'true');
    header.append(title, messages, activeRoute);

    const workspace = element('div', 'workspace');
    const player = element('section', 'player');
    const mapFrame = element('div', 'map-frame');
    const placeholder = element('p', 'map-placeholder');
    const mapTools = element('div', 'map-tools glass');
    const fit = iconButton('mdi:fit-to-screen-outline', '⌖', () => card.fitRoutes(), 'fit', 'map-tool');
    const follow = iconButton('mdi:crosshairs-gps', '◎', () => card.toggleFollow(), 'follow', 'map-tool');
    mapTools.append(fit, follow);
    card.routeMap.element.className = 'map-surface';
    mapFrame.append(card.routeMap.element, placeholder, mapTools);

    const playback = element('section', 'playback');
    const chosen = element('h3', 'route-title');
    const interval = element('p', 'interval');
    const compare = element('div', 'compare-list');
    const picker = element('dialog', 'route-picker');
    const pickerTitle = element('h3');
    pickerTitle.id = 'route-picker-title';
    picker.setAttribute('aria-labelledby', pickerTitle.id);
    const choices = element('div', 'pin-choices');
    enableTouchChoice(choices);
    const cancel = button('', () => picker.close(), 'dismiss-picker');
    picker.append(pickerTitle, choices, cancel);
    picker.addEventListener('close', () => {
      if (picker.open) return;
      choices.replaceChildren();
      if (this.returnToChoice) this.chips.get(card.selection.primary)?.focus.focus({ preventScroll: true });
      this.returnToChoice = false;
    });
    this.chips = new Map();
    const metrics = metricStrip(), totals = metricStrip();
    const selection = element('details', 'selection-totals');
    const selectionLabel = element('summary');
    selection.append(selectionLabel, totals.node);
    const transport = element('div', 'transport glass');
    const play = iconButton('mdi:play', '▶', () => card.playOrPause(), 'play', 'play');
    const slider = element('input'), position = element('span', 'position');
    slider.type = 'range';
    slider.min = '0';
    slider.step = '1';
    slider.dataset.control = 'scrub';
    slider.addEventListener('pointerdown', () => {
      if (card.playback) card.stopPlayback();
    });
    const seekPoint = () => {
      card.index = Number(slider.value);
      card.stopPlayback();
    };
    slider.addEventListener('input', seekPoint);
    slider.addEventListener('change', seekPoint);
    const seek = element('div', 'seek');
    seek.append(slider, position);
    const restart = iconButton('mdi:rewind', '↶', () => {
      card.index = 0;
      card.stopPlayback();
    }, 'restart', 'restart');
    transport.append(play, seek, restart);
    const quality = element('p', 'route-note');
    const more = element('div', 'route-more');
    mapFrame.append(transport);
    playback.append(chosen, interval, compare, metrics.node, selection, quality, more);
    player.append(mapFrame, playback);

    const library = element('section', 'library');
    const heading = element('h3');
    const coverage = element('p', 'note');
    const filters = element('details', 'filters');
    const range = element('summary');
    const filterText = element('strong'), dates = element('span');
    range.append(filterText, dates);
    const toolbar = element('div', 'toolbar');
    filters.append(range, toolbar);
    const items = element('div', 'items');
    const pager = element('footer', 'pager');
    library.append(heading, filters, coverage, items, pager);
    workspace.append(player, library);
    shell.append(header, workspace, picker);
    card.shadowRoot.append(element('style', '', HISTORY_STYLE), shell);
    this.ui = { title, messages, activeRoute, picker, pickerTitle, choices, cancel, placeholder, mapTools, fit, follow, playback, chosen, interval, compare, metrics, totals, selection, selectionLabel,
      transport, play, slider, position, restart, quality, more, heading, coverage, filterText, dates,
      toolbar, items, pager };
  }

  get language() {
    return this.card.browser.hass?.locale?.language ?? this.card.browser.hass?.language ?? 'en';
  }

  get timeZone() {
    return this.card.browser.hass?.config?.time_zone ?? 'Asia/Shanghai';
  }

  number(value, precision, unit = '') {
    if (!Number.isFinite(value)) return '—';
    const places = value === 0 ? 0 : digits(precision);
    const number = new Intl.NumberFormat(this.language, {
      minimumFractionDigits: places, maximumFractionDigits: places,
    }).format(value);
    return unit ? `${number} ${unit}` : number;
  }

  timestamp(value) {
    if (!value || !Number.isFinite(Date.parse(value))) return '';
    return new Intl.DateTimeFormat(this.language, {
      timeZone: this.timeZone, dateStyle: 'medium', timeStyle: 'short',
    }).format(new Date(value));
  }

  readings(ride) {
    return [
      Number.isFinite(ride.distance_m) ?
        this.number(ride.distance_m / 1000, ride.precision.mileages, 'km') : '—',
      Number.isFinite(ride.duration_s) || Number.isFinite(ride.duration_h) ?
        formatRideDuration(ride.duration_s, ride.duration_h, this.language) : '—',
      this.number(ride.energy_wh, ride.precision.ec, 'Wh'),
    ];
  }

  playbackInset() {
    const dock = this.ui.transport;
    return dock.offsetHeight ? dock.parentElement.clientHeight - dock.offsetTop + 8 : 0;
  }

  updatePlayback(labels = this.card.labels()) {
    if (this.card.resetting) return;
    const { transport, play, restart, slider, position } = this.ui;
    const { activeTrack: track, busy, playback } = this.card;
    const enabled = this.card.canReplay(track);
    transport.hidden = !enabled;
    this.ui.follow.hidden = !enabled || this.card.routeMap.renderer?.canFollow === false;
    this.ui.follow.disabled = busy;
    nameControl(this.ui.follow, labels.follow_playback);
    this.ui.follow.setAttribute('aria-pressed', String(Boolean(this.card.follow && enabled)));
    const playing = Boolean(playback);
    const symbol = play.firstElementChild;
    symbol.setAttribute('icon', playing ? 'mdi:pause' : 'mdi:play');
    symbol.textContent = playing ? '⏸' : '▶';
    nameControl(play, playing ? labels.pause_points : labels.play_points);
    nameControl(restart, labels.replay_restart);
    play.disabled = restart.disabled = slider.disabled = busy;
    const last = Math.max(0, (track?.points.length ?? 1) - 1);
    const index = Math.max(0, Math.min(this.card.index, last));
    slider.max = String(last);
    slider.value = String(index);
    const point = enabled ? track.points[index] : null;
    position.textContent = point ? `${index + 1} / ${last + 1}` : '';
    slider.setAttribute('aria-valuetext', point ?
      `${labels.sample_position.replace('{n}', index + 1).replace('{total}', last + 1)} · ${labels.point_sequence} ${point.sequence}` : '');
    this.card.routeMap.highlight(point, this.routeColor(this.card.selection.primary), this.card.follow);
  }

  renderFilters(labels) {
    const { ui, card } = this, { browser, busy } = card;
    ui.filterText.textContent = labels.filters;
    ui.dates.textContent = `${browser.start} – ${browser.end}`;
    const fragment = document.createDocumentFragment();
    if (browser.targets.length > 1) {
      const vehicles = element('div', 'vehicle-filters');
      browser.targets.forEach((target, index) => {
        const label = element('label', 'vehicle-filter', target.name ?? formatEntityLabel(browser.hass,
          browser.hass?.states?.[target.entity], target.entity ?? target.device_id));
        const input = element('input');
        input.type = 'checkbox';
        input.checked = browser.selected.has(index);
        input.disabled = busy;
        input.dataset.control = `vehicle:${index}`;
        input.addEventListener('change', () => browser.select(index, input.checked));
        label.prepend(input);
        vehicles.append(label);
      });
      fragment.append(vehicles);
    }
    const dates = element('div', 'date-fields');
    for (const [key, title] of [['start', labels.start_date], ['end', labels.end_date]]) {
      const label = element('label', '', title), input = element('input');
      input.type = 'date';
      input.value = browser[key];
      input.required = true;
      input.disabled = busy;
      input.dataset.control = key;
      input.addEventListener('change', () => {
        if (input.value) browser.dates(key === 'start' ? input.value : browser.start,
          key === 'end' ? input.value : browser.end);
      });
      label.append(input);
      dates.append(label);
    }
    const quick = element('div', 'quick');
    for (const kind of ['last_7', 'last_30', 'previous_month', 'next_month', 'recent']) {
      const control = button(labels[kind], () => browser.range(kind), kind);
      control.disabled = busy;
      quick.append(control);
    }
    const refresh = button(labels.refresh, () => void card.refreshLocal(), 'refresh', 'apply');
    refresh.disabled = busy || browser.start > browser.end || !browser.selected.size;
    ui.toolbar.replaceChildren(fragment, dates, quick, refresh);
  }

  renderMessages(labels) {
    const { card, ui } = this;
    const states = [];
    const error = card.browser.error || card.tracks.error;
    if (error) states.push(element('p', 'error', labels[error] ?? labels.track_error));
    if (card.busy) states.push(element('p', 'note', labels.loading));
    if (card.pendingUpdates) {
      const refresh = button(labels.history_updated, () => void card.refreshLocal(), 'latest', 'apply');
      refresh.disabled = card.busy;
      states.push(refresh);
    }
    if (card.feedStatus) states.push(element('p', 'note', labels[card.feedStatus]));
    ui.messages.replaceChildren(...states);
  }

  renderLibrary(labels) {
    const { card, ui } = this;
    const { page, pageNumber } = card.browser;
    ui.heading.textContent = labels.title;
    ui.coverage.textContent = page ? [
      [labels.enumerated, page.coverage.enumerated_months],
      [labels.partial, page.coverage.partial_months],
      [labels.missing, page.coverage.missing_months],
      [labels.unknown, page.coverage.unknown_months],
    ].filter(([, rows]) => rows?.length).map(([label, rows]) => `${label} ${rows.length}`).join(' · ') : '';
    const rows = page?.rides ?? [];
    const scroll = ui.items.scrollTop;
    ui.items.replaceChildren(...rows.map((ride, index) => {
      const key = trackKey(ride.device_id, ride), row = element('article', 'ride');
      const selected = card.selection.selected.has(key);
      const choose = button('', () => card.choose(ride), `ride:${key}`, 'choose');
      const time = this.timestamp(ride.start_time);
      choose.append(element('strong', '', time || ride.vehicle_label), element('small', '',
        [time && ride.vehicle_label, ...this.readings(ride).filter(value => value !== '—')].filter(Boolean).join(' · ')));
      choose.disabled = card.busy;
      choose.setAttribute('aria-pressed', String(key === card.selection.primary));
      choose.addEventListener('keydown', event => {
        const next = index + (event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0);
        if (next === index || !rows[next]) return;
        event.preventDefault();
        ui.items.querySelectorAll('.choose')[next]?.focus();
      });
      const overlay = button(selected ? '−' : '+',
        () => selected ? card.removeRide(key) : card.choose(ride, true), `overlay:${key}`, 'overlay');
      nameControl(overlay, [selected ? labels.remove_track : labels.compare, ride.vehicle_label, time]
        .filter(Boolean).join(' · '));
      overlay.setAttribute('aria-pressed', String(selected));
      if (selected) overlay.style.setProperty('--route-color', this.routeColor(key));
      overlay.disabled = card.busy || (!selected && card.selection.selected.size >= MAX_SELECTED_ROUTES);
      row.append(choose, overlay);
      return row;
    }));
    if (page && !rows.length) ui.items.append(element('p', 'note empty', labels.empty));
    ui.items.scrollTop = scroll;
    const previous = button(labels.previous_page, () => card.browser.showPage(pageNumber - 1), 'previous');
    previous.disabled = card.busy || !card.browser.pages.has(pageNumber - 1);
    const next = button(labels.next, () => void card.browser.read(page.next_cursor), 'next');
    next.disabled = card.busy || !page?.next_cursor;
    ui.pager.replaceChildren(...(page && (page.next_cursor || pageNumber > 1) ?
      [previous, element('span', 'note', `${labels.page} ${pageNumber}`), next] : []));
  }

  routeColor(key) {
    return ROUTE_COLORS[this.card.selection.slots.get(key)];
  }

  routeTitle(key) {
    const { selection } = this.card;
    const ride = selection.selected.get(key);
    return [selection.slots.get(key) + 1, ride.vehicle_label, this.timestamp(ride.start_time)]
      .filter(Boolean).join(' · ');
  }

  chooseRoute(keys) {
    const { card, ui } = this;
    if (ui.picker.open) return;
    const options = keys.filter(key => card.selection.selected.has(key)).map(key => {
      const option = button(this.routeTitle(key), () => {
        ui.picker.close();
        card.activate(key);
      }, `pick:${key}`);
      option.setAttribute('aria-pressed', String(key === card.selection.primary));
      return option;
    });
    if (!options.length) return;
    this.returnToChoice = true;
    (options.find(node => node.getAttribute('aria-pressed') === 'true') ?? options[0]).autofocus = true;
    ui.choices.replaceChildren(...options);
    ui.picker.showModal();
  }

  dismissRouteChoices() {
    this.returnToChoice = false;
    if (this.ui.picker.open) this.ui.picker.close();
    this.ui.choices.replaceChildren();
  }

  renderRouteChips(labels) {
    const { card, ui } = this;
    const selected = card.selection.selected;
    for (const [key, chip] of this.chips) {
      if (!selected.has(key)) {
        chip.node.remove();
        this.chips.delete(key);
      }
    }
    for (const [key, ride] of selected) {
      let chip = this.chips.get(key);
      if (!chip) {
        const node = element('div', 'route-chip');
        const focus = button('', () => card.activate(key), `focus:${key}`, 'focus-track');
        const badge = element('span', 'swatch');
        const text = element('span', 'chip-text');
        focus.append(badge, text);
        const remove = button('×', () => card.removeRide(key), `remove:${key}`, 'remove-track');
        node.append(focus, remove);
        chip = { node, focus, badge, text, remove };
        this.chips.set(key, chip);
        ui.compare.append(node);
      }
      const title = this.routeTitle(key);
      if (chip.focus.title !== title) {
        chip.node.style.setProperty('--route-color', this.routeColor(key));
        chip.badge.textContent = String(card.selection.slots.get(key) + 1);
        chip.text.textContent = ride.vehicle_label;
        const time = this.timestamp(ride.start_time);
        if (time) chip.text.append(element('small', '', time));
        nameControl(chip.focus, title);
      }
      nameControl(chip.remove, `${labels.remove_track} · ${title}`);
      chip.focus.setAttribute('aria-pressed', String(key === card.selection.primary));
      chip.focus.disabled = chip.remove.disabled = card.busy;
    }
  }

  renderMap(labels) {
    const { card, ui } = this;
    const { activeRide: ride, activeTrack: track } = card;
    const tracks = [...card.selection.selected.keys()].map(key => card.tracks.tracks.get(key))
      .filter(item => item?.points.length);
    const visible = tracks.length > 0 && !card.mapError;
    card.routeMap.element.hidden = !visible;
    ui.mapTools.hidden = !visible;
    ui.placeholder.hidden = visible;
    ui.placeholder.textContent = card.mapError ? labels.map_error : track && !track.points.length ?
      labels.saved_without_points : ride?.detail_saved === false ? labels.detail_missing :
        card.busy ? labels.loading : labels.map_placeholder;
    nameControl(ui.fit, labels.fit_routes);
    ui.playback.hidden = !ride;
    ui.chosen.textContent = ride?.vehicle_label ?? '';
    ui.interval.textContent = ride ?
      formatRideInterval(ride.start_time, ride.end_time, this.language, this.timeZone) : '';
    ui.metrics.update(labels, ride ? this.readings(track?.summary ?? ride) : []);
    const rides = [...card.selection.selected.values()];
    ui.selection.hidden = rides.length < 2;
    ui.selectionLabel.textContent = `${labels.selection_totals} (${rides.length})`;
    if (ui.selection.hidden) ui.selection.open = false;
    else ui.totals.update(labels, this.readings(selectionTotals(rides)));
    const title = ride ? this.routeTitle(card.selection.primary) : '';
    const announcement = title ? `${labels.active_route} · ${title}` : '';
    if (ui.activeRoute.textContent !== announcement) ui.activeRoute.textContent = announcement;
    ui.metrics.node.setAttribute('aria-label', announcement);
    ui.slider.setAttribute('aria-label', [labels.map_title, title].filter(Boolean).join(' · '));
    ui.pickerTitle.textContent = labels.choose_route;
    ui.cancel.textContent = labels.cancel;
    ui.compare.setAttribute('role', 'group');
    ui.compare.setAttribute('aria-label', labels.map);
    this.renderRouteChips(labels);
    const notes = [];
    if (track) {
      notes.push(`${labels.track_points} ${track.valid_read}/${track.total ?? '—'}`);
      if (hasEndpoints(track)) notes.push(labels.endpoints_only);
      else if (track.kind === 'server_simplified' || track.kind === 'single_point') notes.push(labels.simplified);
      if (track.kind === 'server_simplified' && track.windowDays) {
        notes.push(labels.detail_window.replace('{days}', this.number(track.windowDays, 0)));
      }
      if (track.partial) notes.push(labels.partial_archive);
      if (track.capped) notes.push(labels.display_limit);
      if (card.canReplay(track)) notes.push(labels.playback_note);
    }
    if (visible) notes.push(labels.coordinate_note);
    ui.quality.textContent = notes.join(' · ');
    if (ride && ride.detail_saved !== false && (!track?.complete || track.stale || card.mapError)) {
      const more = button(track && !track.complete && !track.stale ? labels.more_track : labels.show_track,
        () => void card.readRoute(ride, Boolean(track && !track.stale)), 'route');
      more.disabled = card.busy;
      ui.more.replaceChildren(more);
    } else {
      ui.more.replaceChildren();
    }
    const rendered = visible ? tracks : [];
    const slots = rendered.map(item => card.selection.slots.get(item.key));
    const notesText = ui.quality.textContent;
    void card.routeMap.update(rendered, card.browser.hass, labels, slots, rendered.map(item => this.routeTitle(item.key))).then(() => {
      this.updatePlayback();
      if (visible && card.routeMap.renderer?.simplified && ui.quality.textContent === notesText && !track?.capped) {
        ui.quality.textContent = `${notesText} · ${labels.display_limit}`;
      }
    });
    this.updatePlayback(labels);
  }

  render(labels) {
    const { card, ui } = this;
    const active = card.shadowRoot.activeElement?.dataset.control;
    ui.title.textContent = card.browser.config.title ?? labels.map_title;
    this.renderFilters(labels);
    this.renderMessages(labels);
    this.renderLibrary(labels);
    this.renderMap(labels);
    if (active && card.shadowRoot.activeElement?.dataset.control !== active) {
      [...card.shadowRoot.querySelectorAll('[data-control]')].find(node =>
        node.dataset.control === active)?.focus({ preventScroll: true });
    }
  }
}
