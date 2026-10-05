# ha_ninebot product constraints

Check actual Git state and user changes before editing. Runtime assets belong in the integration directory. Local development handbooks, raw samples, histories and workspace tools live outside this product repository; do not add them to releases. Standalone contributors can start with README, docs/README, code, tests/fixtures and CI.

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

Keep changes independently reviewable; preserve branches, user changes and entity identity. Update public user documentation and translations when behavior changes. Run related offline tests; broaden at major functional boundaries or justified risks. Never claim skipped checks or physical effects as verified. Use isolated worktrees if parallel work is explicitly authorized, with one integrator for shared files and releases. No force push or destructive reset.
