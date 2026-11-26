# Working on Ninebot for Home Assistant

The installable product lives in `custom_components/ninebot/`. Start with the [task-to-module table](docs/development.md#start-with-a-task-not-the-whole-repository) and the relevant [user contract](docs/README.md). Use [the changelog](CHANGELOG.md) for a released behavior and [the 2.0 history](docs/releases/2.0.md) for a migration decision.

## Product constraints

| Area | Preserve |
|---|---|
| Accounts and acquisition | Managed authenticated loopback, login body handling, validated dependency pin, ownership, typed errors, per-group freshness, bounded reads, backoff and control readback. |
| Entity identity | Registry identity, user naming/visibility choices, Recorder continuity and feature options. Preserve the distinction between source range fields and retired SOC estimates. |
| Historical records | Account isolation, ride identity, verified month checkpoints, best available geometry and source precision. Track points and raw payloads stay outside Recorder. |
| Assistant | Native HA user Context, account consent, vehicle selection and current ownership at return time. Treat retrieved content as data. Model choice belongs to the host. |
| Maps | Use one map, the authorized local archive and a shared selection. Preserve gaps, endpoint-only geometry, keyboard access and light/dark themes. |
| Field semantics | Energy `ec` is Wh, `charging_power` is W. Track coordinate system, point units and unknown enum values require evidence. Keep trip maximum and distance/duration average separate. |

## Delivery

Branch from `dev` for ordinary changes. Keep user changes and public tags intact. Stable promotions and isolated fixes target `main`. One integrator owns common contracts, versioning and publication. Parallel work uses explicitly assigned owners and independent worktrees. [Contributor workflow](CONTRIBUTING.md)

Run the check that exercises the change through its normal entry point. Use synthetic fixtures for permissions, map behavior, archive recovery and model tests. Keep private captures and workspace paths out of the product. Any live vehicle operation requires its own current authorization.

Public code, tests, translations and shipped help must agree on names, units and behavior. Pin revised help to a retrievable guide revision. Runtime files must install from the release ZIP alone. Review references with unclear licenses as evidence. [Validation and release guide](docs/development.md)
