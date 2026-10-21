# ha_ninebot product constraints

Check actual Git state and user changes before editing. Start with README/docs/README and the task-to-source/test table in docs/development.md; follow CONTRIBUTING.md for delivery. Read only relevant contracts and fixture metadata, not the entire release history. Use docs/releases/2.0.md for the beta-to-RC design timeline; current topic guides remain the behavior contract. Private handbooks/captures/workspace tools stay outside this repository; no local paths are required for standalone contributions.

## Product constraints

- Runtime assets belong inside `custom_components/ninebot`; docs/tests/scripts are development-only.
- Keep the validated exact ninecli pin and minimum HA unless an explicitly reviewed change justifies migration.
- Preserve managed authenticated loopback, password HTTP body, ConfigEntry isolation, typed errors, ownership checks, bounded raw cache, per-group demand/freshness/backoff and control readback.
- Preserve meaningful unique IDs, user names and Recorder history. Do not revive b24 retired SOC estimates or duplicate range/raw entities. Standard entities follow feature options; debug-only diagnostics follow debug mode. Preserve user-disabled and hidden choices.
- `ec` Wh and `charging_power` W are maintainer-confirmed. `used_electricity`, point speed/distance units, CRS and unknown enums remain unverified. Server max speed and distance/duration average are distinct.
- Large raw/history/GPS data stays out of entity attributes/Recorder; diagnostics use safe reviewed schema, never secrets/location/trails/arbitrary raw keys.
- No production HA writes, registry deletion, DB writes or automatic restart/deployment. No fresh cloud/SMS/control tests without a current explicit request. Existing authorization evidence does not authorize unrelated new controls.
- References without a clear license are behavior/schema evidence; do not copy or mechanically translate their implementation.

## Delivery

Normal tasks start from `dev` and target `dev`; `main` is the stable promotion/fix line. Read the manifest and published release for the current candidate version. Core account/history behavior is in RC; bundled map/card UI remains early alpha. Read CONTRIBUTING's branch roles before opening a PR. Keep one bounded task per short-lived topic; long-lived dev/main synchronize with normal merges, not squash. Never merge next-cycle development to fix a stable bug. Retire only authorized, integrated, recoverable topic branches/worktrees; keep main/dev and all published tags. Release requests use the read-only preflight in docs/development.md; it does not publish or prove runtime testing.

Keep changes independently reviewable; preserve branches, user changes and identity. Update public docs/translations when behavior changes. Docs use `python3 scripts/check_docs.py`; runtime changes need related offline tests, broader checks only at major boundaries/justified risks. Record actual checks/skips; do not claim physical effects. Parallel work requires explicit authorization, separate branch/worktree and fixed owner/scope/base; one integrator owns shared contracts, manifest/version, translations, docs navigation and releases. No force push/destructive reset, private-data publication or empty documentation prereleases.
