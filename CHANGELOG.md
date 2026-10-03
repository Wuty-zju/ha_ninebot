# Changelog

## 2.0.0b0 — 2026-10-03

Beta release: replaces the old OpenClaw query backend with pinned ninecli 0.1.7
through a managed, authenticated loopback child. Passwords are sent in request
bodies and are not retained in ConfigEntry or command-line arguments.

- Vehicle SOC, precise range, charging/power/lock state, BMS voltage/temperature,
  current-month distance, optional cloud ranges, location, image and diagnostics.
- Per-vehicle/group freshness, bounded serial requests, forced vehicle refresh,
  partial-failure isolation, local expiry/month notifications and process cleanup.
- Isolated session validation, same-account reauth/reconfigure, recoverable
  commits, rollback protection and Repairs for identity/storage/recovery issues.
- Conservative migration of both v1 layouts, preserving existing entity identities,
  names, disabled settings and history; no recorder SQL changes.
- Optional versioned SOC energy estimates with explicit nominal parameters and
  new entity identities. Old estimated counters are not reused as new measurements.

Breaking changes and limits: missing GSM/address/report-time and old estimate
sources remain legacy values; raw lock diagnostics keep 0=locked/1=unlocked.
Unsupported BMS cycles are unknown. `ec`/`charging_power` have unverified units
and remain optional unitless diagnostics. Coordinates and ride ordering/pagination
are unverified. Experimental controls are off by default, individually opt-in and
only mock-tested; they never retry automatically.

Requires HA 2026.1.0+ and a supported 64-bit ninecli wheel; ARMv7 is unsupported.
HA 2026.1.0/Python 3.13 and HA 2026.9.4/Python 3.14 are the test matrix.
Existing-session queries were verified; real new-password login/refresh recovery,
all hardware controls and other published platforms remain unverified.
Complete Go source/build modifications/reproducible builds are unavailable.

Before upgrading, back up HA configuration/storage/database and the old integration.
Code-only rollback cannot reverse a ConfigEntry schema upgrade: restore the matching
pre-upgrade HA backup and integration version. See the bilingual README,
[entity matrix](docs/2.0-实体迁移矩阵.md) and [audit](docs/2.0-预发布验收.md).
