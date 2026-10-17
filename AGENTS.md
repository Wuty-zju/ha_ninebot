# ha_ninebot product constraints

Check actual Git state and user changes before editing. Start with README/docs/README and the task-to-source/test table in docs/development.md; follow CONTRIBUTING.md for delivery. Read only relevant contracts and fixture metadata, not the entire release history. Private handbooks/captures/workspace tools stay outside this repository; no local paths are required for standalone contributions.

## Product constraints

- Runtime assets belong inside `custom_components/ninebot`; docs/tests/scripts are development-only.
- Keep the validated exact ninecli pin and minimum HA unless an explicitly reviewed change justifies migration.
- Preserve managed authenticated loopback, password HTTP body, ConfigEntry isolation, typed errors, ownership checks, bounded raw cache, per-group demand/freshness/backoff and control readback.
- Preserve meaningful unique IDs, user names and Recorder history. Do not revive b24 retired SOC estimates or duplicate range/raw entities. Created entities are visible by default; user opt-in and user-disabled states still apply.
- `ec` Wh and `charging_power` W are maintainer-confirmed. `used_electricity`, point speed/distance units, CRS and unknown enums remain unverified. Server max speed and distance/duration average are distinct.
- Large raw/history/GPS data stays out of entity attributes/Recorder; diagnostics use safe reviewed schema, never secrets/location/trails/arbitrary raw keys.
- No production HA writes, registry deletion, DB writes or automatic restart/deployment. No fresh cloud/SMS/control tests without a current explicit request. Existing authorization evidence does not authorize unrelated new controls.
- References without a clear license are behavior/schema evidence; do not copy or mechanically translate their implementation.

## Delivery

Keep changes independently reviewable; preserve branches, user changes and identity. Update public docs/translations when behavior changes. Docs use `python3 scripts/check_docs.py`; runtime changes need related offline tests, broader checks only at major boundaries/justified risks. Record actual checks/skips; do not claim physical effects. Parallel work requires explicit authorization, separate branch/worktree and fixed owner/scope/base; one integrator owns shared contracts, manifest/version, translations, docs navigation and releases. No force push/destructive reset, private-data publication or empty documentation prereleases.
