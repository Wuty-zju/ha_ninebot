export const HISTORY_STYLE = `
:host {
  display: block;
  container-type: inline-size;
  color: var(--primary-text-color, #171a1f);
  --replay-accent: var(--primary-color, #168af9);
  --replay-muted: var(--secondary-text-color, #59616e);
  --replay-surface: var(--secondary-background-color, color-mix(in srgb, currentColor 8%, var(--card-background-color, Canvas)));
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
ha-card { display: block; overflow: hidden; padding: clamp(12px, 3cqw, 24px); }
h2, h3, p { margin: 0; }
h2 { font-size: clamp(21px, 4cqw, 29px); line-height: 1.18; font-weight: 680; letter-spacing: -.025em; }
h3 { font-size: 14px; font-weight: 650; }
button, input { font: inherit; accent-color: var(--replay-accent); color: inherit; }
button {
  min-height: 44px; border: 1px solid var(--divider-color, #80808035);
  border-radius: 12px; padding: 8px 12px;
  background: var(--card-background-color, var(--ha-card-background, #fff)); cursor: pointer;
}
button:disabled { opacity: .52; cursor: default; }
button:not(:disabled):hover { border-color: var(--replay-accent); }
:focus-visible { outline: 2px solid var(--replay-accent); outline-offset: 2px; }
.bar, .controls, .pager, .quick, .compare-list { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.bar { justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
.filters { width: 100%; }
.filters summary { min-height: 36px; cursor: pointer; color: var(--replay-muted); padding-block: 7px; }
.toolbar { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; padding: 8px 0 16px; }
.toolbar label { display: grid; gap: 4px; font-size: 13px; color: var(--replay-muted); }
.toolbar .check { display: flex; align-items: center; min-height: 44px; gap: 8px; }
input[type=date] { background: var(--replay-surface); min-height: 44px; min-width: 0;
  border: 1px solid var(--divider-color, #80808035); border-radius: 10px; padding: 6px 8px; }
.note { color: var(--replay-muted); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.error { color: var(--error-color, #ca3434); font-size: 13px; }
.messages:not(:empty) { display: grid; justify-items: start; gap: 6px; padding: 0 0 12px; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; align-items: start; }
.player, .rides { min-width: 0; }
.map-frame {
  position: relative; height: clamp(280px, 49vh, 530px); overflow: hidden; isolation: isolate;
  border-radius: 18px; background: var(--replay-surface);
}
.map-surface { width: 100%; height: 100%; }
.map-placeholder { position: absolute; inset: 0; display: grid; place-content: center; padding: 20px;
  color: var(--replay-muted); text-align: center; pointer-events: none; }
.fit { position: absolute; z-index: 901; inset-inline-end: 12px; top: 12px; min-width: 44px; }
ha-icon { --mdc-icon-size: 20px; vertical-align: middle; }
.transport { display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; padding: 14px 0 0; }
.track-title { font-size: 18px; font-weight: 650; overflow-wrap: anywhere; }
.ride-metrics { margin-top: 3px; color: var(--replay-muted); font-variant-numeric: tabular-nums; font-size: 13px; }
.controls { flex-wrap: nowrap; }
.controls button { flex: none; min-width: 44px; }
.controls .play { background: var(--replay-surface); color: var(--primary-text-color); }
.scrubber { min-width: 0; flex: 1; padding: 0 2px; }
.scrubber input { display: block; width: 100%; height: 26px; margin: 0; }
.scrubber span { display: block; font-size: 11px; color: var(--replay-muted);
  white-space: nowrap; font-variant-numeric: tabular-nums; }
.controls output { font-variant-numeric: tabular-nums; }
.compare-list button { font-size: 13px; }
.compare-list button[aria-pressed=true] { border-color: var(--replay-accent); font-weight: 650; }
.swatch { display: inline-block; height: 9px; width: 9px; border-radius: 100%; margin-inline-end: 6px; }
.rides { background: var(--replay-surface); border-radius: 16px; padding: 12px; }
.rides h3 { margin-bottom: 6px; }
.rides .items { max-height: min(48vh, 500px); overflow: auto; padding: 4px; overscroll-behavior: contain; }
.ride { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px; margin-bottom: 4px; }
.ride button { min-width: 0; }
.ride .choose { display: grid; text-align: start; gap: 4px; border-color: transparent;
  background: transparent; line-height: 1.3; }
.ride .choose[aria-pressed=true] {
  border-color: var(--divider-color, #80808035);
  background: var(--card-background-color, var(--ha-card-background, #fff));
  box-shadow: inset 3px 0 0 var(--replay-accent);
}
.ride .choose strong { font-size: 14px; overflow-wrap: anywhere; }
.ride .choose small { color: var(--replay-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.ride .compare { align-self: center; background: transparent; border: none; width: 44px; padding: 0; }
.ride .compare[aria-pressed=true] { color: var(--replay-accent); }
.pager { justify-content: space-between; margin-top: 8px; }
@container (min-width: 760px) {
  .layout { grid-template-columns: minmax(0, 1.95fr) minmax(235px, 1fr); }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
`;
