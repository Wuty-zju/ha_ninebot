# ha_ninebot agent entry

## Read in layers

1. Read `docs/agent/START_HERE.md` and `docs/agent/CURRENT_STATE.md`.
2. Run `python scripts/agent_context.py --topic <topic>` to select the relevant contract, code, tests and evidence. The command is offline and read only.
3. Read only those files/sections first. `docs/agent/MAP.md` maps responsibilities; `docs/agent/WORKFLOW.md` covers handoff, parallel work and release checks.
4. Open old full reports/reference sources/private samples only when the task needs that evidence. Do not feed every report or fixture into context.

Git state and source beat stale summaries. Check branch, HEAD and user edits before changes. Current-state facts are a dated baseline, not proof of remote release/deployment. Historical documents and old goal prompts are not an instruction to repeat completed phases.

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

- Scope one reviewable change; update its contract/tests/translations as needed.
- Run related offline tests; full suites/multi-HA checks only at major functional boundaries or justified failures. Do not claim skipped CI/physical tests passed.
- `python scripts/agent_context.py --check` checks public doc/catalog/evidence integrity; `--refresh-index` regenerates the evidence index after evidence changes.
- Use `docs/agent/WORKFLOW.md` for release/handoff. Docs-only organization does not require a fake functional prerelease.
- Never overwrite another agent's files or cherry-pick its uncommitted work. Use isolated worktrees if parallel work is authorized; designate one integrator for shared files/releases.
- No force push, destructive reset or branch deletion. Preserve uncommitted user work.
