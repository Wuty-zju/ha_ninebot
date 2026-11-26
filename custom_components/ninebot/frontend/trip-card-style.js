export const HISTORY_STYLE = `
:host {
  display: block;
  container-type: inline-size;
  color: var(--primary-text-color, CanvasText);
  --replay-accent: var(--primary-color, #168af9);
  --replay-card: var(--ha-card-background, var(--card-background-color, Canvas));
  --replay-glass: color-mix(in srgb, var(--replay-card) 86%, transparent);
  --replay-muted: var(--secondary-text-color, GrayText);
  --replay-line: var(--divider-color, color-mix(in srgb, currentColor 14%, transparent));
  --replay-surface: color-mix(in srgb, currentColor 5%, var(--replay-card));
  --replay-selection: color-mix(in srgb, var(--replay-accent) 12%, var(--replay-card));
}
* { box-sizing: border-box; }
[hidden], .note:empty, .route-note:empty, .route-more:empty, .pager:empty { display: none !important; }
ha-card { display: block; overflow: hidden; padding: clamp(12px, 2.5cqw, 24px); }
h2, h3, p, dl, dd { margin: 0; }
h2 { font-size: clamp(23px, 4cqw, 32px); letter-spacing: -.035em; line-height: 1.15; font-weight: 680; }
h3 { font-size: 15px; line-height: 1.3; font-weight: 650; }
.card-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 20px; margin-bottom: 14px; }
.messages:not(:empty) { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.note, .route-note { color: var(--replay-muted); font-size: 12px; line-height: 1.48; overflow-wrap: anywhere; }
.error { color: var(--error-color, #c72d36); font-size: 13px; }
.workspace { display: grid; gap: 16px; min-width: 0; }
.player, .library, .filters, .playback { min-width: 0; }
.map-frame {
  position: relative; isolation: isolate; height: clamp(256px, 49svh, 500px);
  overflow: hidden; border-radius: 20px; background: var(--replay-surface);
  border: 1px solid var(--replay-line);
}
.map-surface { width: 100%; height: 100%; }
.map-placeholder {
  position: absolute; inset: 0; display: grid; place-items: center; padding: 24px;
  color: var(--replay-muted); text-align: center; font-size: 14px; pointer-events: none;
}
button, input { font: inherit; color: inherit; accent-color: var(--replay-accent); }
button {
  min-height: 44px; min-width: 44px; border: 1px solid var(--replay-line);
  border-radius: 12px; padding: 7px 12px; background: var(--replay-card); cursor: pointer;
}
button:hover:not(:disabled) { border-color: var(--replay-accent); }
button:disabled { opacity: .5; cursor: default; }
:focus-visible { outline: 2px solid var(--replay-accent); outline-offset: 2px; }
ha-icon { --mdc-icon-size: 20px; vertical-align: middle; }
.glass {
  position: absolute; z-index: 901; background: var(--replay-card);
  border: 1px solid var(--replay-line); border-radius: 28px;
  box-shadow: 0 2px 9px #0002, 0 8px 24px #0002;
}
@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .glass { background: var(--replay-glass); -webkit-backdrop-filter: blur(18px) saturate(125%);
    backdrop-filter: blur(18px) saturate(125%); }
}
.map-tools { display: flex; padding: 2px; right: 10px; inset-block-start: 10px; }
.map-tool, .transport button {
  border: 0; border-radius: 50%; padding: 0; background: transparent;
}
.map-tool[aria-pressed=true] { color: var(--replay-accent); background: var(--replay-selection); }
.playback { padding-block: 15px 0; }
.route-title { font-size: 19px; letter-spacing: -.02em; overflow-wrap: anywhere; }
.interval { margin-top: 4px; color: var(--replay-muted); font-size: 13px; }
.interval:empty, .compare-list:empty { display: none; }
.compare-list { display: flex; flex-wrap: wrap; gap: 6px; margin-block: 10px; }
.route-chip {
  display: flex; align-items: center; max-width: 100%; border-radius: 24px;
  background: var(--replay-surface); border: 1px solid var(--replay-line);
}
.route-chip:has(.focus-track[aria-pressed=true]) { background: var(--replay-selection); }
.route-chip button { background: transparent; border-color: transparent; border-radius: 24px; }
.focus-track { display: flex; align-items: center; gap: 7px; min-width: 0; text-align: start; font-size: 13px; overflow-wrap: anywhere; }
.focus-track[aria-pressed=true] { font-weight: 650; }
.chip-text small { display: block; color: var(--replay-muted); font-weight: 400; }
.swatch {
  flex: none; display: grid; place-items: center; font: 650 12px/1 system-ui;
  width: 24px; height: 24px; border: 3px solid var(--route-color); border-radius: 50%;
  color: var(--primary-text-color, CanvasText); background: var(--replay-card);
}
.remove-track { padding: 0; font-size: 19px; color: var(--replay-muted); }
.metrics {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  border-radius: 14px; background: var(--replay-surface); margin-block: 12px;
}
.selection-totals { border-block-start: 1px solid var(--replay-line); }
.selection-totals summary { padding-block: 12px; cursor: pointer; font: inherit; font-weight: 550; }
.selection-totals summary:focus-visible { outline: 2px solid var(--replay-accent); outline-offset: -2px; }
.selection-totals .metrics { margin-block-start: 0; }
.metrics > div { min-width: 0; padding: 10px 11px; }
.metrics > div + div { border-inline-start: 1px solid var(--replay-line); }
.metrics dt { color: var(--replay-muted); font-size: 12px; overflow-wrap: anywhere; }
.metrics dd { margin-top: 3px; font-size: clamp(13px, 2.9cqw, 19px); font-weight: 650;
  font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.transport {
  display: flex; align-items: center; gap: 7px; padding: 3px 5px;
  inset-inline-start: 10px; inset-block-end: 38px; width: min(340px, calc(100% - 76px));
}
.transport button { flex: none; border-radius: 50%; padding: 0; }
.transport .play { background: var(--replay-selection); }
.seek { flex: 1; min-width: 0; display: grid; }
.seek input { width: 100%; height: 26px; margin: 0; touch-action: pan-y; }
.position { color: var(--replay-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.route-more { margin-top: 8px; }
.library {
  border-radius: 18px; border: 1px solid var(--replay-line); padding: 12px;
  background: var(--replay-surface); align-self: start;
}
.library h3 { margin: 2px 0 8px; }
.filters { border-block: 1px solid var(--replay-line); margin-bottom: 8px; }
.filters summary {
  min-height: 44px; cursor: pointer; padding: 8px 3px;
  font-size: 12px; overflow-wrap: anywhere;
}
.filters summary span { display: block; margin-top: 3px; color: var(--replay-muted); }
.toolbar { display: grid; gap: 8px; padding-bottom: 10px; }
.vehicle-filters, .quick { display: flex; flex-wrap: wrap; gap: 5px; }
.vehicle-filter { display: flex; align-items: center; padding: 4px 6px; min-height: 44px; gap: 7px; }
.date-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.date-fields label { display: grid; gap: 4px; color: var(--replay-muted); font-size: 12px; }
input[type=date] {
  min-width: 0; width: 100%; min-height: 44px; border: 1px solid var(--replay-line);
  border-radius: 10px; background: var(--replay-card); padding: 4px 6px;
}
.quick button { font-size: 12px; }
.apply { background: var(--replay-selection); font-weight: 600; }
.items { max-height: min(52svh, 520px); overflow: auto; padding: 3px; overscroll-behavior: contain; }
.ride { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px; }
.ride + .ride { border-top: 1px solid var(--replay-line); margin-top: 3px; padding-top: 3px; }
.choose {
  display: grid; gap: 4px; text-align: start; border-color: transparent; background: transparent;
  min-width: 0; overflow-wrap: anywhere; line-height: 1.35;
}
.choose[aria-pressed=true] { background: var(--replay-selection); border-color: var(--replay-line); }
.choose strong { font-size: 13px; }
.choose small { font-size: 12px; color: var(--replay-muted); }
.overlay { align-self: center; padding: 0; font-size: 19px; border-radius: 50%; }
.overlay[aria-pressed=true] { border-color: var(--route-color, var(--replay-accent)); }
.empty { padding: 12px 4px; }
.pager { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; }
@container (min-width: 740px) {
  .workspace { grid-template-columns: minmax(0, 1.9fr) minmax(236px, 1fr); }
}
@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
  .glass { background: var(--replay-card); -webkit-backdrop-filter: none; backdrop-filter: none; }
}
@media (forced-colors: active) {
  .glass { background: Canvas; border-color: ButtonText; box-shadow: none; }
  .map-tool[aria-pressed=true] { outline: 2px solid Highlight; outline-offset: -4px; }
}
`;
